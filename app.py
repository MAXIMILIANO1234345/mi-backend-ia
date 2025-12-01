import os
import json
import re
import time
import threading
import requests
import urllib.request
import urllib3
import ssl # Necesario para el ajuste SSL
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib3.poolmanager import PoolManager
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from supabase import create_client, Client

# --- 1. CONFIGURACIÓN ---
print("--- ORQUESTADOR HÍBRIDO INMORTAL (V19: Fix SSL & Arguments) ---")
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

if not all([SUPABASE_URL, SUPABASE_KEY, REMOTE_LLM_URL]):
    print("⚠️ Advertencia: Faltan variables críticas.")
    if not REMOTE_LLM_URL: REMOTE_LLM_URL = "http://localhost:11434/api"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
ORQUESTADOR_ID = 1
CUSTOM_MODEL_NAME = "blender-expert"

MODO_AUTONOMO_ACTIVO = True
TIEMPO_ENTRE_CICLOS = 600
TIEMPO_HEARTBEAT = 540

app = Flask(__name__)
CORS(app)

# --- ADAPTADOR SSL PERSONALIZADO (FIX DECRYPTION_FAILED) ---
class SSLAdapter(HTTPAdapter):
    """
    Fuerza el uso de TLS v1.2 y ciphers compatibles para evitar errores
    de desencriptación con Ngrok.
    """
    def init_poolmanager(self, connections, maxsize, block=False):
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        # Permitimos ciphers antiguos por compatibilidad si es necesario
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
        total=5, # Aumentamos intentos
        read=5,
        connect=5,
        backoff_factor=0.5, # Reintentos más rápidos
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["POST"]
    )
    # Usamos nuestro adaptador SSL personalizado
    adapter = SSLAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

http_session = get_robust_session()

# --- CACHE ---
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

# --- CONEXIÓN REMOTA ---
def get_headers():
    return {
        "ngrok-skip-browser-warning": "true",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
        "Origin": "http://localhost:11434"
    }

# CORRECCIÓN: Agregado parámetro json_mode=False
def remote_generate(prompt, json_mode=False):
    payload = {
        "model": CUSTOM_MODEL_NAME, "prompt": prompt, "stream": False,
        "options": {"temperature": 0.2, "num_predict": 300, "top_k": 40}
    }
    
    # Si pedimos JSON, se lo decimos a Ollama
    if json_mode:
        payload["format"] = "json"

    try:
        # Usamos la sesión con SSL Fix
        res = http_session.post(
            f"{REMOTE_LLM_URL}/generate", 
            json=payload, 
            headers=get_headers(), 
            timeout=600
        )
        return res.json().get("response", "") if res.status_code == 200 else ""
    except Exception as e:
        print(f"❌ Error PC (Generate): {e}")
        return ""

def remote_embedding(text):
    try:
        res = http_session.post(
            f"{REMOTE_LLM_URL}/embeddings", 
            json={"model": "nomic-embed-text", "prompt": text}, 
            headers=get_headers(), 
            timeout=60
        )
        return res.json().get("embedding") if res.status_code == 200 else None
    except Exception as e:
        print(f"❌ Error PC (Embedding): {e}")
        return None

def normalizar_json(texto):
    try: return json.loads(re.sub(r'```json\s*|\s*```', '', texto.strip()))
    except: return {}

# ==============================================================================
# ❤️ SISTEMA DE AUTO-PRESERVACIÓN
# ==============================================================================
def sistema_auto_preservacion():
    print("💓 [HEARTBEAT] Sistema de soporte vital activado.")
    while True:
        time.sleep(TIEMPO_HEARTBEAT)
        if PUBLIC_URL:
            try:
                requests.get(f"{PUBLIC_URL}/health", timeout=10)
                print(f"💓 [ALIVE] Auto-ping exitoso.")
            except Exception as e:
                print(f"⚠️ [ALIVE] Fallo en auto-ping: {e}")
        else:
            print("⚠️ [ALIVE] Configura PUBLIC_URL para evitar sueño.")

threading.Thread(target=sistema_auto_preservacion, daemon=True).start()

# ==============================================================================
# 🤖 CICLO DE VIDA AUTÓNOMO
# ==============================================================================

def auditoria_sistema():
    stats = {}
    for clave, data in CATALOGO_PILARES.items():
        try:
            res = supabase.table(data['nombre_tabla']).select('id', count='exact').execute()
            stats[clave] = res.count
        except: stats[clave] = 0
    return min(stats, key=stats.get) if stats else "api"

def generar_curriculum(pilar):
    info = CATALOGO_PILARES.get(pilar)
    return remote_generate(f"Eres admin BD. Pilar débil: {info['nombre_clave']}. Genera UN título técnico faltante.").strip()

def investigar_tema(tema):
    return remote_generate(f"ACTÚA COMO EXPERTO BLENDER. Tema: {tema}. Explica técnicamente con código python.")

def ciclo_vida_autonomo():
    print("🤖 [NUBE] Jardinero Híbrido iniciado...")
    while True:
        if MODO_AUTONOMO_ACTIVO:
            try:
                res = supabase.table('laboratorio_ideas').select('*').in_('estado', ['borrador']).limit(1).execute()
                
                if res.data:
                    tarea = res.data[0]
                    print(f"🧪 [AUTO] Estudiando: {tarea['tema_objetivo']}")
                    contenido = investigar_tema(tarea['tema_objetivo'])
                    
                    if contenido:
                        evaluacion = normalizar_json(remote_generate(f"Evalúa:\n{contenido}\nJSON: {{ \"aprobado\": true, \"critica\": \"ok\", \"codigo\": \"...\" }}", json_mode=True))
                        
                        if evaluacion.get('aprobado'):
                            pilar = CATALOGO_PILARES.get(tarea['pilar_destino'])
                            if pilar:
                                vec = remote_embedding(f"{tarea['tema_objetivo']} {contenido}")
                                if vec:
                                    supabase.rpc('cerebro_aprender', {
                                        'p_orquestador_id': ORQUESTADOR_ID, 'p_tabla_destino': pilar['nombre_tabla'],
                                        'p_concepto': tarea['tema_objetivo'], 'p_detalle': contenido,
                                        'p_codigo': evaluacion.get('codigo', ''), 'p_vector': vec
                                    }).execute()
                                    supabase.table('laboratorio_ideas').delete().eq('id', tarea['id']).execute()
                                    print(f"🎓 [AUTO] Aprendido: {tarea['tema_objetivo']}")
                        else:
                            supabase.table('laboratorio_ideas').update({'estado': 'rechazado'}).eq('id', tarea['id']).execute()
                else:
                    pilar = auditoria_sistema()
                    print(f"💡 [AUTO] Auditando... Pilar débil: {pilar}")
                    tema = generar_curriculum(pilar)
                    if tema:
                        supabase.table('laboratorio_ideas').insert({'orquestador_id': ORQUESTADOR_ID, 'tema_objetivo': tema, 'pilar_destino': pilar, 'estado': 'borrador'}).execute()
            
            except Exception as e:
                print(f"⚠️ Error ciclo autónomo: {e}")
        
        time.sleep(TIEMPO_ENTRE_CICLOS)

threading.Thread(target=ciclo_vida_autonomo, daemon=True).start()

# ==============================================================================
# ENDPOINTS
# ==============================================================================

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "Online", "mode": "Hybrid Immortal V19"}), 200

@app.route("/health", methods=["GET"])
def health_check():
    return "OK", 200

@app.route("/preguntar", methods=["POST"])
def endpoint_preguntar():
    data = request.json
    pregunta = data.get('pregunta', '')
    print(f"\n📨 Usuario: {pregunta}")
    
    vec = remote_embedding(pregunta)
    contexto = []
    if vec:
        for p in ["api", "objetos", "logica_ia"]:
            if p in CATALOGO_PILARES:
                try:
                    res = supabase.rpc('cerebro_recordar_flow', {'p_orquestador_id': ORQUESTADOR_ID, 'p_tabla_destino': CATALOGO_PILARES[p]['nombre_tabla'], 'p_vector': vec, 'p_umbral': 0.35, 'p_limite': 2}).execute()
                    if res.data: contexto.extend([f"[{p}] {i['concepto']}: {i['detalle']}" for i in res.data])
                except: pass
    
    contexto_str = "\n".join(contexto)
    prompt = f"Experto Blender. Contexto: {contexto_str}. Pregunta: {pregunta}. Responde."
    respuesta = remote_generate(prompt)
    
    # CORRECCIÓN: Ahora json_mode=True es aceptado correctamente
    json_final = normalizar_json(remote_generate(f"Formatea a JSON frontend:\nTexto: {respuesta}\nFuente: Híbrido\nJSON: {{ \"respuesta_principal\": \"...\", \"puntos_clave\": [], \"fuente\": \"...\" }}", json_mode=True))
    
    if not json_final: json_final = {"respuesta_principal": respuesta, "puntos_clave": [], "fuente": "Híbrido"}
    
    return jsonify(json_final)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
