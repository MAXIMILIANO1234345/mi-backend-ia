import os
import json
import re
import time
import threading
import requests
import urllib.request
import urllib3
import ssl 
import google.generativeai as genai  # <--- NUEVO: Cerebro de respaldo
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib3.poolmanager import PoolManager
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from supabase import create_client, Client
from datetime import datetime

# ==============================================================================
# 1. CONFIGURACIÓN (V25: Híbrido + Telemetría para Maestro)
# ==============================================================================
print("--- ORQUESTADOR HÍBRIDO INMORTAL (V25: Telemetría Activada) ---")
load_dotenv()

# Silenciar advertencias de SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Credenciales
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
REMOTE_LLM_URL = os.getenv('REMOTE_LLM_URL')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY') # <--- NUEVO: Para el Maestro/Backup
PUBLIC_URL = os.getenv('PUBLIC_URL') 

# Limpieza de URL Ngrok
if REMOTE_LLM_URL:
    REMOTE_LLM_URL = REMOTE_LLM_URL.strip().rstrip('/')
    if not REMOTE_LLM_URL.endswith('/api'):
        REMOTE_LLM_URL += '/api'

# Validaciones críticas
if not all([SUPABASE_URL, SUPABASE_KEY, REMOTE_LLM_URL]):
    print("⚠️ Advertencia: Faltan variables críticas.")
    if not REMOTE_LLM_URL: REMOTE_LLM_URL = "http://localhost:11434/api"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
ORQUESTADOR_ID = 1
CUSTOM_MODEL_NAME = "blender-expert"

# Configuración Gemini (Respaldo de Lujo)
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    model_gemini = genai.GenerativeModel("gemini-1.5-flash")

MODO_AUTONOMO_ACTIVO = True
TIEMPO_ENTRE_CICLOS = 600
TIEMPO_HEARTBEAT = 540

app = Flask(__name__)
CORS(app)

# ==============================================================================
# 🛠️ ADAPTADOR SSL & SESIÓN ROBUSTA (INTOCABLE)
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
            print(f"✅ Catálogo cargado: {len(CATALOGO_PILARES)} pilares.")
    except: pass
cargar_catalogo()

# ==============================================================================
# 📡 TELEMETRÍA (NUEVO: CONEXIÓN CON EL MAESTRO)
# ==============================================================================

def reportar_prompt_al_maestro(prompt_usuario):
    """Guarda lo que pide el usuario para que el Maestro analice el mercado."""
    try:
        threading.Thread(target=lambda: supabase.table('historial_prompts').insert({
            'orquestador_id': ORQUESTADOR_ID,
            'prompt_usuario': prompt_usuario
        }).execute()).start()
    except Exception as e:
        print(f"⚠️ Telemetría prompt falló: {e}")

def reportar_uso_memoria(lista_memorias):
    """Avisa qué recuerdos fueron útiles para subir su ranking (Héroes vs Zombies)."""
    def _reportar():
        for item in lista_memorias:
            try:
                # Llama a tu nueva función SQL V24
                supabase.rpc('registrar_uso_memoria', {
                    'p_tabla': item['tabla'], 
                    'p_id': item['id']
                }).execute()
            except Exception as e:
                print(f"⚠️ Telemetría memoria falló ID {item['id']}: {e}")
    
    if lista_memorias:
        threading.Thread(target=_reportar).start()

# ==============================================================================
# 🧠 CEREBRO HÍBRIDO (LOCAL + GEMINI FALLBACK)
# ==============================================================================

def get_headers():
    return {
        "ngrok-skip-browser-warning": "true",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Origin": "http://localhost:11434"
    }

def remote_generate(prompt, json_mode=False):
    """Intenta Local primero. Si falla o timeout, usa Gemini."""
    
    # 1. INTENTO LOCAL
    try:
        payload = {
            "model": CUSTOM_MODEL_NAME, "prompt": prompt, "stream": False,
            "options": {"temperature": 0.2, "num_predict": 300, "top_k": 40}
        }
        if json_mode: payload["format"] = "json"

        res = http_session.post(
            f"{REMOTE_LLM_URL}/generate", 
            json=payload, 
            headers=get_headers(), 
            timeout=180, # Bajamos un poco para dar tiempo al fallback
            verify=False 
        )
        if res.status_code == 200:
            return res.json().get("response", "")
    except Exception as e:
        print(f"🔌 Fallo Local ({e}). Intentando Fallback...")

    # 2. INTENTO GEMINI (RESPALDO)
    if GOOGLE_API_KEY:
        try:
            print("✨ Usando Gemini Back-up...")
            conf = {"response_mime_type": "application/json"} if json_mode else {}
            return model_gemini.generate_content(prompt, generation_config=conf).text
        except Exception as ex:
            print(f"❌ Fallo Total: {ex}")
    
    return "Error: Cerebros no disponibles."

def remote_embedding(text):
    # Prioridad Local para mantener consistencia vectorial, Gemini si falla
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
    except:
        if GOOGLE_API_KEY: # Fallback a embedding de Google (Ojo: dimensiones distintas, usar con cuidado)
            # Para V24 idealmente mantenemos local, pero retornamos None si falla
            pass 
    return None

def normalizar_json(texto):
    try: return json.loads(re.sub(r'```json\s*|\s*```', '', texto.strip()))
    except: return {}

# ==============================================================================
# ❤️ SISTEMA DE AUTO-PRESERVACIÓN & AUTONOMÍA
# ==============================================================================
def sistema_auto_preservacion():
    print("💓 [HEARTBEAT] Sistema de soporte vital activado.")
    while True:
        time.sleep(TIEMPO_HEARTBEAT)
        if PUBLIC_URL:
            try:
                requests.get(f"{PUBLIC_URL}/health", timeout=10)
                print(f"💓 [ALIVE] Auto-ping exitoso.")
            except: pass

threading.Thread(target=sistema_auto_preservacion, daemon=True).start()

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
    print("🤖 [NUBE] Jardinero Híbrido iniciado...")
    while True:
        if MODO_AUTONOMO_ACTIVO:
            try:
                # ... (Lógica de laboratorio existente) ...
                res = supabase.table('laboratorio_ideas').select('*').in_('estado', ['borrador']).limit(1).execute()
                if res.data:
                    tarea = res.data[0]
                    print(f"🧪 [AUTO] Estudiando: {tarea['tema_objetivo']}")
                    contenido = investigar_tema(tarea['tema_objetivo'])
                    if contenido and "Error" not in contenido:
                        # Simulamos evaluación rápida
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
                                print(f"🎓 [AUTO] Aprendido: {tarea['tema_objetivo']}")
                else:
                    # Si no hay ideas, auditar sistema
                    pilar = auditoria_sistema()
                    # Generar tema nuevo
                    tema = remote_generate(f"Eres admin BD. Pilar débil: {pilar}. Genera UN título técnico faltante.").strip()
                    if tema and "Error" not in tema:
                         supabase.table('laboratorio_ideas').insert({'orquestador_id': ORQUESTADOR_ID, 'tema_objetivo': tema, 'pilar_destino': pilar, 'estado': 'borrador'}).execute()
            except Exception as e:
                print(f"⚠️ Ciclo autónomo pausa: {e}")
        
        time.sleep(TIEMPO_ENTRE_CICLOS)

threading.Thread(target=ciclo_vida_autonomo, daemon=True).start()

# ==============================================================================
# 🚀 ENDPOINTS (ACTUALIZADOS PARA MAESTRO)
# ==============================================================================

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "Online", "mode": "Hybrid Immortal V25"}), 200

@app.route("/health", methods=["GET"])
def health_check():
    return "OK", 200

@app.route("/preguntar", methods=["POST"])
def endpoint_preguntar():
    data = request.json
    pregunta = data.get('pregunta', '')
    if not pregunta: return jsonify({"error": "Vacio"}), 400

    print(f"\n📨 Usuario: {pregunta}")
    
    # 1. TELEMETRÍA: REPORTAR AL MAESTRO
    reportar_prompt_al_maestro(pregunta)
    
    vec = remote_embedding(pregunta)
    contexto = []
    memorias_usadas = [] # Para reportar utilidad
    
    if vec:
        # Buscamos en TODOS los pilares
        for p_key, p_data in CATALOGO_PILARES.items():
            try:
                # Usamos la RPC actualizada V24 que devuelve IDs
                res = supabase.rpc('cerebro_recordar_flow', {
                    'p_orquestador_id': ORQUESTADOR_ID, 
                    'p_tabla_destino': p_data['nombre_tabla'], 
                    'p_vector': vec, 'p_umbral': 0.35, 'p_limite': 2
                }).execute()
                
                if res.data:
                    for i in res.data:
                        contexto.append(f"[{p_key}] {i['concepto']}: {i.get('detalle_tecnico') or i.get('detalle')}")
                        # Guardamos ID para reportar
                        if 'id' in i:
                            memorias_usadas.append({'tabla': p_data['nombre_tabla'], 'id': i['id']})
            except: pass
    
    # 2. TELEMETRÍA: REPORTAR USO DE MEMORIA
    if memorias_usadas:
        print(f"📚 Reportando {len(memorias_usadas)} recuerdos útiles al Maestro.")
        reportar_uso_memoria(memorias_usadas)

    contexto_str = "\n".join(contexto)
    prompt = f"Experto Blender. Contexto: {contexto_str}. Pregunta: {pregunta}. Responde."
    
    respuesta = remote_generate(prompt)
    
    # Manejo robusto de errores
    if not respuesta or "Error" in respuesta:
        return jsonify({
            "respuesta_principal": f"Mis cerebros están re-calibrando. ({respuesta}). Reintenta.",
            "puntos_clave": [], "fuente": "Error Timeout"
        })

    # Formateo JSON final
    json_final = normalizar_json(remote_generate(f"Formatea a JSON frontend:\nTexto: {respuesta}\nFuente: Híbrido\nJSON: {{ \"respuesta_principal\": \"...\", \"puntos_clave\": [], \"fuente\": \"...\" }}", json_mode=True))
    
    if not json_final: 
        json_final = {"respuesta_principal": respuesta, "puntos_clave": [], "fuente": "Híbrido Raw"}
    
    return jsonify(json_final)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
