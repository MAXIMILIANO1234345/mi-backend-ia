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
# 1. CONFIGURACIÓN (V28: MODO PACIENCIA & LOGS)
# ==============================================================================
# Función para imprimir logs instantáneos en Render
def log_r(msg):
    print(f"[Render] {msg}", flush=True)

log_r("--- INICIANDO ORQUESTADOR V28 (MODO PACIENCIA) ---")
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

MODO_AUTONOMO_ACTIVO = True
TIEMPO_ENTRE_CICLOS = 600
TIEMPO_HEARTBEAT = 540

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
        total=3, # Aumentamos reintentos para dar margen a la red
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
    log_r(f"🔌 [DEBUG] Iniciando conexión a: {REMOTE_LLM_URL}/generate")
    try:
        payload = {
            "model": CUSTOM_MODEL_NAME, "prompt": prompt, "stream": False,
            "options": {"temperature": 0.2, "num_predict": 400, "top_k": 40}
        }
        if json_mode: payload["format"] = "json"

        # TIMEOUT EXTENDIDO (180s = 3 minutos)
        # Esto permite que tu PC despierte, cargue el modelo y responda
        start_time = time.time()
        log_r(f"⏳ [DEBUG] Enviando payload (JSON Mode: {json_mode})... Esperando hasta 180s.")
        
        res = http_session.post(
            f"{REMOTE_LLM_URL}/generate", 
            json=payload, 
            headers=get_headers(), 
            timeout=180, 
            verify=False 
        )
        
        log_r(f"✅ [DEBUG] Respuesta recibida en {round(time.time() - start_time, 2)}s. Status: {res.status_code}")
        
        if res.status_code == 200:
            return res.json().get("response", "")
        else:
            log_r(f"❌ [DEBUG] Error HTTP del modelo: {res.text}")
            return f"Error HTTP {res.status_code}: {res.text}"
            
    except requests.exceptions.ConnectionError:
        log_r("❌ [DEBUG] CRÍTICO: Conexión rechazada. Verifica si Ngrok sigue abierto en tu PC.")
        return "Error: Conexión rechazada. Verifica Ngrok."
    except requests.exceptions.ReadTimeout:
        log_r("❌ [DEBUG] TIMEOUT REAL: Pasaron 3 minutos y tu PC no respondió.")
        return "Error: El modelo tardó demasiado en responder (Timeout 180s)."
    except Exception as e:
        log_r(f"❌ [DEBUG] Error desconocido: {e}")
        return f"Error: {str(e)}"

def remote_embedding(text):
    try:
        # Aumentamos el timeout de embeddings a 30s por si el modelo está frío
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
        log_r(f"⚠️ [DEBUG] Fallo embedding (Timeout o error): {e}")
    return None

def normalizar_json(texto):
    try: return json.loads(re.sub(r'```json\s*|\s*```', '', texto.strip()))
    except: return {}

# ==============================================================================
# ❤️ SISTEMA DE AUTO-PRESERVACIÓN
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

# ==============================================================================
# 🚀 ENDPOINTS
# ==============================================================================

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "Online", "mode": "Local Gemma V28 (Patient Mode)"}), 200

@app.route("/health", methods=["GET"])
def health_check():
    return "OK", 200

@app.route("/preguntar", methods=["POST"])
def endpoint_preguntar():
    log_r("="*40)
    data = request.json
    pregunta = data.get('pregunta', '')
    
    if not pregunta: 
        log_r("⚠️ [DEBUG] Petición vacía recibida.")
        return jsonify({"error": "Vacio"}), 400

    log_r(f"📨 [DEBUG] PREGUNTA ENTRANTE: {pregunta}")
    
    # 1. Telemetría
    reportar_prompt_al_maestro(pregunta)
    
    # 2. Contexto
    vec = remote_embedding(pregunta)
    contexto = []
    
    if vec:
        log_r("🧠 [DEBUG] Vector generado, buscando recuerdos...")
        for p_key, p_data in CATALOGO_PILARES.items():
            try:
                res = supabase.rpc('cerebro_recordar_flow', {
                    'p_orquestador_id': ORQUESTADOR_ID, 
                    'p_tabla_destino': p_data['nombre_tabla'], 
                    'p_vector': vec, 'p_umbral': 0.35, 'p_limite': 2
                }).execute()
                if res.data:
                    contexto.append(f"[{p_key}] Encontrado.")
            except: pass
    else:
        log_r("⚠️ [DEBUG] No se pudo generar embedding (Probable timeout inicial).")

    # 3. Generación
    log_r("🚀 [DEBUG] Enviando a Generar Texto...")
    respuesta = remote_generate(f"Pregunta: {pregunta}")
    
    if "Error" in respuesta:
        log_r(f"🔥 [DEBUG] FALLO GENERACIÓN: {respuesta}")
        return jsonify({
            "respuesta_principal": f"Fallo de conexión ({respuesta}). Verifica URL Ngrok en Render.",
            "puntos_clave": [], "fuente": "Error"
        })

    # 4. Formateo JSON
    log_r("🎨 [DEBUG] Formateando a JSON...")
    json_final = normalizar_json(remote_generate(f"Formatea a JSON: {respuesta}", json_mode=True))
    
    if not json_final: 
        json_final = {"respuesta_principal": respuesta, "puntos_clave": [], "fuente": "Local Raw"}
    
    log_r("✨ [DEBUG] Respuesta enviada al usuario.")
    return jsonify(json_final)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
