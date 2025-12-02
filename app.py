import os
import json
import re
import time
import threading
import requests
import urllib.request
import urllib3
import ssl 
import sys
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib3.poolmanager import PoolManager
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from supabase import create_client, Client
from datetime import datetime

# ==============================================================================
# 1. CONFIGURACIÓN (V31: RESPUESTA DIRECTA - SIN REFORMATO)
# ==============================================================================
def log_r(msg):
    print(f"[Render] {msg}", flush=True)

log_r("--- INICIANDO ORQUESTADOR V31 (RAW MODE) ---")
log_r("✅ ESTRATEGIA: Paso de formateo eliminado para máxima estabilidad.")

load_dotenv()

# Silenciar advertencias de SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Credenciales
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
REMOTE_LLM_URL = os.getenv('REMOTE_LLM_URL')
PUBLIC_URL = os.getenv('PUBLIC_URL') 

# Limpieza de URL Ngrok
if REMOTE_LLM_URL:
    REMOTE_LLM_URL = REMOTE_LLM_URL.strip().rstrip('/')
    if not REMOTE_LLM_URL.endswith('/api'):
        REMOTE_LLM_URL += '/api'

# Validaciones críticas
if not all([SUPABASE_URL, SUPABASE_KEY, REMOTE_LLM_URL]):
    log_r("⚠️ Advertencia: Faltan variables críticas.")
    if not REMOTE_LLM_URL: REMOTE_LLM_URL = "http://localhost:11434/api"

log_r(f"🔗 URL LLM REMOTO CONFIGURADA: {REMOTE_LLM_URL}")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
ORQUESTADOR_ID = 1

# IMPORTANTE: Asegúrate de que este nombre coincida con tu modelo en Ollama
CUSTOM_MODEL_NAME = "blender-expert" 

# --- CONTROL DE TRÁFICO ---
MODO_AUTONOMO_ACTIVO = True
TIEMPO_ENTRE_CICLOS = 600 # 10 minutos entre mejoras
TIEMPO_HEARTBEAT = 540

# SEMÁFORO DE USUARIO
# Si un humano preguntó hace menos de 60s, el bot se detiene.
LAST_USER_ACTIVITY = 0 
USER_COOLDOWN = 60 

app = Flask(__name__)
CORS(app)

# ==============================================================================
# 🛠️ ADAPTADOR SSL & SESIÓN ROBUSTA
# ==============================================================================
class SSLAdapter(HTTPAdapter):
    def init_poolmanager(self, connections, maxsize, block=False):
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.set_ciphers('DEFAULT:@SECLEVEL=1') 
        self.poolmanager = PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            ssl_context=context
        )

def get_robust_session():
    session = requests.Session()
    retry = Retry(
        total=3, 
        read=3,
        connect=3,
        backoff_factor=0.5,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["POST"]
    )
    adapter = SSLAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

http_session = get_robust_session()

# ==============================================================================
# 📚 CACHE DE PILARES
# ==============================================================================
CATALOGO_PILARES = {}
def cargar_catalogo():
    global CATALOGO_PILARES
    try:
        response = supabase.table('catalogo_pilares').select('*').eq('orquestador_id', ORQUESTADOR_ID).execute()
        if response.data:
            CATALOGO_PILARES = {
                i['nombre_clave']: {**i, 'criterio_admision': i.get('criterio_admision') or "Info."} 
                for i in response.data
            }
            log_r(f"✅ Catálogo cargado: {len(CATALOGO_PILARES)} pilares.")
    except Exception as e:
        log_r(f"❌ Error cargando catálogo: {e}")

cargar_catalogo()

# ==============================================================================
# 📡 TELEMETRÍA
# ==============================================================================

def reportar_prompt_al_maestro(prompt_usuario):
    try:
        threading.Thread(target=lambda: supabase.table('historial_prompts').insert({
            'orquestador_id': ORQUESTADOR_ID,
            'prompt_usuario': prompt_usuario
        }).execute()).start()
    except Exception as e:
        log_r(f"⚠️ Telemetría prompt falló: {e}")

def reportar_uso_memoria(lista_memorias):
    def _reportar():
        for item in lista_memorias:
            try:
                supabase.rpc('registrar_uso_memoria', {
                    'p_tabla': item['tabla'], 
                    'p_id': item['id']
                }).execute()
            except Exception as e:
                log_r(f"⚠️ Telemetría memoria falló ID {item['id']}: {e}")
    if lista_memorias:
        threading.Thread(target=_reportar).start()

# ==============================================================================
# 🧠 CEREBRO LOCAL (NGROK / OLLAMA)
# ==============================================================================

def get_headers():
    return {
        "ngrok-skip-browser-warning": "true",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Origin": "http://localhost:11434"
    }

def remote_generate(prompt, json_mode=False):
    """Usa EXCLUSIVAMENTE el modelo local con TIEMPO EXTENDIDO."""
    # log_r(f"🔌 [DEBUG] Conectando: {REMOTE_LLM_URL}/generate") # Comentado para limpiar log
    try:
        payload = {
            "model": CUSTOM_MODEL_NAME, "prompt": prompt, "stream": False,
            "options": {"temperature": 0.2, "num_predict": 400, "top_k": 40}
        }
        if json_mode: payload["format"] = "json"

        # TIMEOUT 180s
        res = http_session.post(
            f"{REMOTE_LLM_URL}/generate", 
            json=payload, 
            headers=get_headers(), 
            timeout=180, 
            verify=False 
        )
        
        if res.status_code == 200:
            return res.json().get("response", "")
        else:
            log_r(f"❌ [DEBUG] Error HTTP del modelo: {res.text}")
            return f"Error HTTP {res.status_code}: {res.text}"
            
    except requests.exceptions.ConnectionError:
        log_r("❌ [DEBUG] CRÍTICO: Conexión rechazada. Verifica Ngrok.")
        return "Error: Conexión rechazada. Verifica Ngrok."
    except requests.exceptions.ReadTimeout:
        log_r("❌ [DEBUG] TIMEOUT: Pasaron 3 minutos sin respuesta.")
        return "Error: Timeout 180s."
    except Exception as e:
        log_r(f"❌ [DEBUG] Error desconocido: {e}")
        return f"Error: {str(e)}"

def remote_embedding(text):
    try:
        res = http_session.post(
            f"{REMOTE_LLM_URL}/embeddings", 
            json={"model": "nomic-embed-text", "prompt": text}, 
            headers=get_headers(), 
            timeout=30, 
            verify=False
        )
        if res.status_code == 200:
            return res.json().get("embedding")
    except Exception as e:
        log_r(f"⚠️ [DEBUG] Fallo embedding: {e}")
    return None

# ==============================================================================
# ❤️ SISTEMA DE AUTO-PRESERVACIÓN & AUTONOMÍA CONSCIENTE
# ==============================================================================
def sistema_auto_preservacion():
    log_r("💓 [HEARTBEAT] Sistema de soporte vital activado.")
    while True:
        time.sleep(TIEMPO_HEARTBEAT)
        if PUBLIC_URL:
            try:
                requests.get(f"{PUBLIC_URL}/health", timeout=10)
            except: pass

threading.Thread(target=sistema_auto_preservacion, daemon=True).start()

# --- FUNCIONES AUXILIARES AUTÓNOMAS ---
def auditoria_sistema():
    stats = {}
    for clave, data in CATALOGO_PILARES.items():
        try:
            res = supabase.table(data['nombre_tabla']).select('id', count='exact').execute()
            stats[clave] = res.count
        except: stats[clave] = 0
    return min(stats, key=stats.get) if stats else "api"

def investigar_tema(tema):
    return remote_generate(f"ACTÚA COMO EXPERTO BLENDER. Tema: {tema}. Explica técnicamente con código python.")

def ciclo_vida_autonomo():
    log_r("🤖 [NUBE] Jardinero Híbrido iniciado (Modo Respetuoso).")
    
    while True:
        # 1. VERIFICAR SEMÁFORO DE USUARIO
        # Si alguien habló hace poco, esperamos.
        tiempo_desde_ultimo_usuario = time.time() - LAST_USER_ACTIVITY
        if tiempo_desde_ultimo_usuario < USER_COOLDOWN:
            log_r(f"✋ [AUTO] Usuario activo hace {int(tiempo_desde_ultimo_usuario)}s. Pausando mantenimiento...")
            time.sleep(30) # Esperamos 30s antes de volver a checar
            continue

        if MODO_AUTONOMO_ACTIVO:
            try:
                # ... (Lógica de laboratorio existente) ...
                res = supabase.table('laboratorio_ideas').select('*').in_('estado', ['borrador']).limit(1).execute()
                
                # --- PUNTO DE CONTROL 2 ---
                # Verificar de nuevo antes de una operación pesada
                if (time.time() - LAST_USER_ACTIVITY) < USER_COOLDOWN: continue 

                if res.data:
                    tarea = res.data[0]
                    log_r(f"🧪 [AUTO] Estudiando: {tarea['tema_objetivo']}")
                    
                    contenido = investigar_tema(tarea['tema_objetivo'])
                    
                    if contenido and "Error" not in contenido:
                        pilar = CATALOGO_PILARES.get(tarea['pilar_destino'])
                        if pilar:
                            vec = remote_embedding(f"{tarea['tema_objetivo']} {contenido}")
                            if vec:
                                supabase.rpc('cerebro_aprender', {
                                    'p_orquestador_id': ORQUESTADOR_ID, 'p_tabla_destino': pilar['nombre_tabla'],
                                    'p_concepto': tarea['tema_objetivo'], 'p_detalle': contenido,
                                    'p_codigo': "", 'p_vector': vec
                                }).execute()
                                supabase.table('laboratorio_ideas').delete().eq('id', tarea['id']).execute()
                                log_r(f"🎓 [AUTO] Aprendido: {tarea['tema_objetivo']}")
                else:
                    # Crear nuevas tareas si no hay
                    pilar = auditoria_sistema()
                    tema = remote_generate(f"Eres admin BD. Pilar débil: {pilar}. Genera UN título técnico faltante.").strip()
                    if tema and "Error" not in tema:
                         supabase.table('laboratorio_ideas').insert({'orquestador_id': ORQUESTADOR_ID, 'tema_objetivo': tema, 'pilar_destino': pilar, 'estado': 'borrador'}).execute()
            
            except Exception as e:
                log_r(f"⚠️ Ciclo autónomo pausa: {e}")
        
        # Descanso largo entre ciclos de mejora
        time.sleep(TIEMPO_ENTRE_CICLOS)

threading.Thread(target=ciclo_vida_autonomo, daemon=True).start()

# ==============================================================================
# 🚀 ENDPOINTS (PRIORIDAD ALTA)
# ==============================================================================

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "Online", "mode": "V31 Direct Raw"}), 200

@app.route("/health", methods=["GET"])
def health_check():
    return "OK", 200

@app.route("/preguntar", methods=["POST"])
def endpoint_preguntar():
    # 🔴 ACTIVAR SEMÁFORO ROJO PARA EL BOT AUTÓNOMO
    global LAST_USER_ACTIVITY
    LAST_USER_ACTIVITY = time.time()
    
    log_r("="*40)
    data = request.json
    pregunta = data.get('pregunta', '')
    
    if not pregunta: 
        return jsonify({"error": "Vacio"}), 400

    log_r(f"📨 [USER] PREGUNTA: {pregunta}")
    
    # 1. Telemetría
    reportar_prompt_al_maestro(pregunta)
    
    # 2. Contexto
    vec = remote_embedding(pregunta)
    contexto = []
    
    if vec:
        for p_key, p_data in CATALOGO_PILARES.items():
            try:
                res = supabase.rpc('cerebro_recordar_flow', {
                    'p_orquestador_id': ORQUESTADOR_ID, 
                    'p_tabla_destino': p_data['nombre_tabla'], 
                    'p_vector': vec, 'p_umbral': 0.35, 'p_limite': 2
                }).execute()
                if res.data:
                    contexto.append(f"[{p_key}] Info Encontrada.")
            except: pass

    # 3. Generación (Actualizamos actividad de nuevo por si tardó el embedding)
    LAST_USER_ACTIVITY = time.time()
    
    log_r("🚀 [USER] Generando respuesta (RAW)...")
    prompt_final = f"Pregunta sobre Blender Python: {pregunta}. Responde con código y explicación técnica."
    respuesta = remote_generate(prompt_final)
    
    # Manejo de errores simple para el usuario
    if "Error" in respuesta:
        log_r(f"🔥 [USER] FALLO: {respuesta}")
        return jsonify({
            "respuesta_principal": "Lo siento, mi conexión con el cerebro local es inestable en este momento.",
            "puntos_clave": [], "fuente": "Error de Conexión"
        })

    # 4. ENTREGA DIRECTA (SIN RE-FORMATEO)
    # Empaquetamos la respuesta cruda en el formato JSON que el frontend espera
    # para no romper la interfaz.
    json_final = {
        "respuesta_principal": respuesta, 
        "puntos_clave": [], 
        "fuente": "Local Gemma (Raw)"
    }
    
    log_r("✨ [USER] Respuesta enviada (Directa).")
    LAST_USER_ACTIVITY = time.time()
    
    return jsonify(json_final)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
