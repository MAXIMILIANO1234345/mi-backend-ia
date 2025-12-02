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
# 1. CONFIGURACIÓN (V34: CORRECCIÓN DE LANZAMIENTO)
# ==============================================================================
def log_r(msg):
    print(f"[Render-App] {msg}", flush=True)

log_r("--- INICIANDO SISTEMA UNIFICADO V34 ---")
log_r("✅ MODO: Web Server + Maestro Autodetect")

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

log_r(f"🔗 URL LLM REMOTO (GEMMA): {REMOTE_LLM_URL}")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
ORQUESTADOR_ID = 1

# IMPORTANTE: Nombre del modelo en tu Ollama local
CUSTOM_MODEL_NAME = "blender-expert" 

# SEMÁFORO DE USUARIO (Para que el Maestro respete el tráfico)
LAST_USER_ACTIVITY = 0 
USER_COOLDOWN = 60 

app = Flask(__name__)
CORS(app)

# ==============================================================================
# 🛠️ CONEXIÓN ROBUSTA CON TU PC (NGROK)
# ==============================================================================
class SSLAdapter(HTTPAdapter):
    def init_poolmanager(self, connections, maxsize, block=False):
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.set_ciphers('DEFAULT:@SECLEVEL=1') 
        self.poolmanager = PoolManager(num_pools=connections, maxsize=maxsize, block=block, ssl_context=context)

def get_robust_session():
    session = requests.Session()
    retry = Retry(total=3, read=3, connect=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504], allowed_methods=["POST"])
    adapter = SSLAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

http_session = get_robust_session()

# ==============================================================================
# 📚 CACHE DE PILARES (MEMORIA RÁPIDA)
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
# 📡 TELEMETRÍA (ALIMENTANDO AL MAESTRO)
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
                # Si tienes la función RPC para contar uso, úsala
                supabase.rpc('registrar_uso_memoria', {'p_tabla': item['tabla'], 'p_id': item['id']}).execute()
            except: pass
    if lista_memorias:
        threading.Thread(target=_reportar).start()

# ==============================================================================
# 🧠 CEREBRO LOCAL (GEMMA / OLLAMA)
# ==============================================================================

def get_headers():
    return {
        "ngrok-skip-browser-warning": "true",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Origin": "http://localhost:11434"
    }

def remote_generate(prompt, json_mode=False):
    """Consulta a Gemma en tu PC."""
    try:
        payload = {
            "model": CUSTOM_MODEL_NAME, 
            "prompt": prompt, 
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": 1000, "top_k": 40} # Aumentado tokens para respuestas largas
        }
        if json_mode: payload["format"] = "json"

        # TIMEOUT ALTO: Damos 3 minutos a Gemma para pensar respuestas complejas
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
            log_r(f"❌ Error HTTP Gemma: {res.text}")
            return f"Error HTTP {res.status_code}"
            
    except Exception as e:
        log_r(f"❌ Error conectando a Gemma: {e}")
        return f"Error de conexión: {str(e)}"

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
    except: pass
    return None

# ==============================================================================
# 🔥 LANZAMIENTO DEL MAESTRO INTEGRADO (CORRECCIÓN V34)
# ==============================================================================
def despertar_maestro():
    try:
        log_r("🎩 Invocando a gemini_maestro...")
        import gemini_maestro
        
        # LÓGICA DE DETECCIÓN INTELIGENTE
        # Verifica qué función existe en el archivo maestro que tienes subido
        target_func = None
        
        if hasattr(gemini_maestro, 'bucle_infinito'):
            # V34+ (Modo Constructor/Autónomo)
            target_func = gemini_maestro.bucle_infinito
            log_r("✅ Detectado Maestro Moderno (bucle_infinito)")
            
        elif hasattr(gemini_maestro, 'ciclo_vida'):
            # V33 (Modo Auditoría)
            target_func = gemini_maestro.ciclo_vida
            log_r("✅ Detectado Maestro Clásico (ciclo_vida)")
            
        elif hasattr(gemini_maestro, 'ciclo_maestro_loop'):
            # V1 (Legacy)
            target_func = gemini_maestro.ciclo_maestro_loop
            log_r("✅ Detectado Maestro Legacy (ciclo_maestro_loop)")

        if target_func:
            log_r(f"🚀 Lanzando hilo del Maestro...")
            hilo = threading.Thread(target=target_func, daemon=True)
            hilo.start()
        else:
            log_r("⚠️ Se importó el Maestro pero no se encontró ninguna función de inicio conocida.")
            log_r(f"   Contenido detectado: {dir(gemini_maestro)}")
            
    except ImportError:
        log_r("⚠️ No se encontró el archivo 'gemini_maestro.py'. El Maestro no correrá.")
    except Exception as e:
        log_r(f"❌ Error fatal lanzando al Maestro: {e}")

# INICIAR EL MAESTRO EN PARALELO
despertar_maestro()

# ==============================================================================
# 🚀 ENDPOINTS (API PARA EL FRONTEND)
# ==============================================================================

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "Online", "mode": "V34 Unified"}), 200

@app.route("/health", methods=["GET"])
def health_check():
    return "OK", 200

@app.route("/preguntar", methods=["POST"])
def endpoint_preguntar():
    global LAST_USER_ACTIVITY
    LAST_USER_ACTIVITY = time.time() # Semáforo en rojo para el Maestro
    
    log_r("="*40)
    data = request.json
    pregunta = data.get('pregunta', '')
    
    if not pregunta: return jsonify({"error": "Vacio"}), 400

    log_r(f"📨 [USER] {pregunta}")
    reportar_prompt_al_maestro(pregunta)
    
    # 1. Búsqueda de Contexto (RAG)
    vec = remote_embedding(pregunta)
    contexto = []
    
    if vec:
        for p_key, p_data in CATALOGO_PILARES.items():
            try:
                # Buscamos en las tablas que el Maestro ha descubierto/creado
                res = supabase.rpc('cerebro_recordar_flow', {
                    'p_orquestador_id': ORQUESTADOR_ID, 
                    'p_tabla_destino': p_data['nombre_tabla'], 
                    'p_vector': vec, 'p_umbral': 0.35, 'p_limite': 2
                }).execute()
                
                if res.data:
                    for item in res.data:
                        # Añadimos al contexto para Gemma
                        contexto.append(f"[{p_key.upper()}] {item['concepto']}: {item.get('detalle_tecnico') or item.get('codigo_ejemplo')}")
                        # Reportamos uso para que el Maestro sepa qué sirve
                        reportar_uso_memoria([{'tabla': p_data['nombre_tabla'], 'id': item['id']}])
            except: pass

    # 2. Generación con Gemma
    contexto_str = "\n".join(contexto)
    log_r(f"📚 Contexto encontrado: {len(contexto)} fragmentos.")
    
    prompt_final = f"""
    Eres un Experto Técnico en Blender Python.
    
    CONTEXTO (Base de Datos):
    {contexto_str if contexto_str else "No hay datos previos."}
    
    PREGUNTA DEL USUARIO:
    {pregunta}
    
    INSTRUCCIONES:
    - Responde con código Python funcional para Blender (bpy).
    - Explica brevemente.
    - Si el contexto ayuda, úsalo. Si no, usa tu conocimiento.
    """
    
    log_r("🚀 Enviando a Gemma...")
    respuesta = remote_generate(prompt_final)
    
    # 3. Respuesta Directa (Raw)
    if "Error" in respuesta:
        log_r("🔥 Fallo en generación.")
    else:
        log_r("✨ Respuesta generada con éxito.")

    json_final = {
        "respuesta_principal": respuesta, 
        "puntos_clave": [], 
        "fuente": "Memoria + Gemma" if contexto else "Gemma (Imaginación)"
    }
    
    LAST_USER_ACTIVITY = time.time()
    return jsonify(json_final)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
