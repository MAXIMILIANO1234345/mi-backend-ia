import os
import json
import re
import time
import threading
import requests
import urllib3
import ssl 
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib3.poolmanager import PoolManager
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from supabase import create_client, Client

# ==============================================================================
# CONFIGURACIÓN INICIAL
# ==============================================================================
def log_r(msg):
    print(f"[Render-App] {msg}", flush=True)

log_r("--- INICIANDO SISTEMA ORQUESTADOR V35 (MODO JSON) ---")

load_dotenv()
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
REMOTE_LLM_URL = os.getenv('REMOTE_LLM_URL')

if REMOTE_LLM_URL:
    REMOTE_LLM_URL = REMOTE_LLM_URL.strip().rstrip('/')
    if not REMOTE_LLM_URL.endswith('/api'):
        REMOTE_LLM_URL += '/api'
    REMOTE_LLM_URL = REMOTE_LLM_URL.replace('/api/api', '/api')

if not REMOTE_LLM_URL: 
    REMOTE_LLM_URL = "http://localhost:11434"

log_r(f"🔗 URL LLM REMOTO: {REMOTE_LLM_URL}")

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except:
    supabase = None
    log_r("⚠️ Supabase desactivado o credenciales inválidas.")

CUSTOM_MODEL_NAME = "blender-expert" 
LAST_USER_ACTIVITY = 0 

app = Flask(__name__)
CORS(app)

# ==============================================================================
# CONEXIÓN ROBUSTA (NGROK)
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
    retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
    adapter = SSLAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

http_session = get_robust_session()

# ==============================================================================
# CEREBRO LOCAL (OLLAMA JSON MODE)
# ==============================================================================
def remote_generate(prompt):
    try:
        payload = {
            "model": CUSTOM_MODEL_NAME, 
            "prompt": prompt, 
            "stream": False,
            # Quitamos 'format: json' temporalmente para que el modelo no se bloquee
            # si no puede generar un JSON perfecto a la primera.
            "options": {
                "temperature": 0.7, # Subimos un poco para que sea más creativo
                "num_predict": 1000,
                "top_p": 0.9
            }
        }

        res = http_session.post(
            f"{REMOTE_LLM_URL}/generate", 
            json=payload, 
            headers={"ngrok-skip-browser-warning": "true"}, 
            timeout=180, 
            verify=False 
        )
        
        if res.status_code == 200:
            return res.json().get("response", "")
        else:
            return ""
    except Exception as e:
        log_r(f"❌ Error en Ollama: {e}")
        return ""

# ==============================================================================
# ENDPOINTS
# ==============================================================================
@app.route("/preguntar", methods=["POST"])
def endpoint_preguntar():
    global LAST_USER_ACTIVITY
    LAST_USER_ACTIVITY = time.time() 
    
    data = request.json
    pregunta = data.get('pregunta', '')
    if not pregunta: return jsonify({"error": "Vacio"}), 400

    log_r(f"📨 [USER] {pregunta}")
    
    prompt_final = f"""
    Eres un Experto Técnico Avanzado en Blender Python y A-Frame (WebVR).
    
    PREGUNTA DEL USUARIO: "{pregunta}"
    
    INSTRUCCIONES CRÍTICAS:
    Devuelve ÚNICAMENTE un objeto JSON válido con estas tres claves exactas. NO uses bloques de código (```json) alrededor de tu respuesta.
    
    {{
        "blender_python": "Tu código Python funcional para Blender aquí. Usa import bpy.",
        "explicacion": "Tu explicación en texto plano o markdown simple aquí.",
        "aframe_html": "El código HTML de A-Frame (<a-box>, <a-sphere>, etc.) para previsualizar el objeto 3D. Usa múltiples entidades si es complejo."
    }}
    """
    
    log_r("🚀 Enviando a Gemma (Modo Estructurado)...")
    respuesta_raw = remote_generate(prompt_final)
    
    # Lógica de extracción segura (Regex)
    try:
        match = re.search(r'\{.*\}', respuesta_raw, re.DOTALL)
        if match:
            datos_ia = json.loads(match.group(0))
        else:
            datos_ia = json.loads(respuesta_raw)
            
        json_final = {
            "blender_python": datos_ia.get("blender_python", ""),
            "explicacion": datos_ia.get("explicacion", "No se generó explicación."),
            "aframe_html": datos_ia.get("aframe_html", ""),
            "fuente": "Gemma (Estructurado)"
        }
        log_r("✨ JSON parseado y enviado al frontend.")
        
    except Exception as e:
        log_r(f"🔥 Fallo al parsear JSON: {e}")
        json_final = {
            "blender_python": "",
            "explicacion": "Error procesando el JSON del modelo local. Revisa la consola de Ollama.",
            "aframe_html": "",
            "respuesta_cruda": respuesta_raw,
            "fuente": "Error de Formato"
        }
    
    return jsonify(json_final)

if __name__ == "__main__":
    # Render asigna el puerto dinámicamente
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)


