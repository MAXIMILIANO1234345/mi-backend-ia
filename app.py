import os
import json
import re
import time
import threading
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from supabase import create_client, Client

# --- 1. CONFIGURACIÓN DE ALTO RENDIMIENTO ---
print("--- ORQUESTADOR HÍBRIDO (Estado de Flow Activo) ---")
load_dotenv()

SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
REMOTE_LLM_URL = os.getenv('REMOTE_LLM_URL')

# Limpieza y validación de URL del Túnel
if REMOTE_LLM_URL:
    REMOTE_LLM_URL = REMOTE_LLM_URL.strip().rstrip('/')
    if not REMOTE_LLM_URL.endswith('/api'):
        REMOTE_LLM_URL += '/api'

# Fallback para desarrollo local
if not all([SUPABASE_URL, SUPABASE_KEY, REMOTE_LLM_URL]):
    if not REMOTE_LLM_URL: REMOTE_LLM_URL = "http://localhost:11434/api"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
ORQUESTADOR_ID = 1
CUSTOM_MODEL_NAME = "blender-expert" # Tu modelo Qwen/Llama optimizado

# Configuración del Jardinero (Segundo plano)
MODO_AUTONOMO_ACTIVO = True
TIEMPO_ENTRE_CICLOS = 120 # Pausa para dejar enfriar tu CPU i3

app = Flask(__name__)
CORS(app)

# --- CACHE DE METADATOS ---
CATALOGO_PILARES = {}
def cargar_catalogo():
    """Carga estructura en RAM para evitar latencia SQL innecesaria."""
    global CATALOGO_PILARES
    try:
        response = supabase.table('catalogo_pilares').select('*').eq('orquestador_id', ORQUESTADOR_ID).execute()
        if response.data:
            CATALOGO_PILARES = {
                i['nombre_clave']: {
                    **i,
                    'criterio_admision': i.get('criterio_admision') or "Información relevante."
                } for i in response.data
            }
            print(f"✅ Flow State: {len(CATALOGO_PILARES)} pilares en memoria.")
    except: pass

cargar_catalogo()

# --- CONEXIÓN OPTIMIZADA (TÚNEL) ---

def get_headers():
    return {
        "ngrok-skip-browser-warning": "true",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
        "Origin": "http://localhost:11434"
    }

def remote_generate(prompt):
    """
    Inferencia optimizada para velocidad.
    """
    payload = {
        "model": CUSTOM_MODEL_NAME,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.1,   # Lógica pura, cero dudas
            "num_predict": 300,   # Respuestas concisas
            "num_ctx": 1024,      # Contexto ligero para RAM de 8GB
            "top_k": 20
        }
    }
    
    try:
        # Timeout agresivo pero realista para un túnel doméstico
        res = requests.post(f"{REMOTE_LLM_URL}/generate", json=payload, headers=get_headers(), timeout=120)
        if res.status_code == 200: 
            return res.json().get("response", "")
        return f"Error del Cerebro Local: {res.status_code}"
    except Exception as e:
        print(f"❌ Error Túnel: {e}")
        return ""

def remote_embedding(text):
    """Vectorización remota."""
    try:
        res = requests.post(
            f"{REMOTE_LLM_URL}/embeddings", 
            json={"model": "nomic-embed-text", "prompt": text}, 
            headers=get_headers(), 
            timeout=30
        )
        return res.json().get("embedding") if res.status_code == 200 else None
    except: return None

# ==============================================================================
# 🌊 MOTOR DE FLOW (SQL + LÓGICA)
# ==============================================================================

def planificar_busqueda_rapida(pregunta):
    """
    Estrategia de Flow: En lugar de pensar dónde buscar, buscamos en los pilares 
    más probables y dejamos que el índice HNSW de Supabase filtre lo irrelevante.
    Esto ahorra una llamada completa al LLM (aprox 2-3 segundos de ganancia).
    """
    return ["api", "objetos", "logica_ia", "materiales", "nodos_geo"]

def consultar_memoria_flow(pilares, vector):
    """
    Usa la RPC optimizada 'cerebro_recordar_flow' que creamos en SQL.
    Aprovecha índices HNSW y Clústeres físicos.
    """
    hallazgos = []
    if not vector: return []
    
    for clave in pilares:
        if clave in CATALOGO_PILARES:
            try:
                # RPC OPTIMIZADA: Solo trae lo realmente útil (> 0.4 similitud)
                res = supabase.rpc('cerebro_recordar_flow', {
                    'p_orquestador_id': ORQUESTADOR_ID, 
                    'p_tabla_destino': CATALOGO_PILARES[clave]['nombre_tabla'], 
                    'p_vector': vector, 
                    'p_umbral': 0.4, 
                    'p_limite': 2 # Solo los 2 mejores recuerdos para no saturar contexto
                }).execute()
                
                if res.data:
                    for i in res.data:
                        hallazgos.append(f"[{clave.upper()}] {i['concepto']}: {i['detalle_tecnico']}")
            except: pass
            
    return hallazgos

def investigar_tema_flash(tema):
    """Investigación rápida para auto-mejora."""
    return remote_generate(f"Experto Blender. Tema: {tema}. Explica brevemente y da código python.")

# ==============================================================================
# 🤖 JARDINERO AUTÓNOMO (Segundo Plano)
# ==============================================================================
def ciclo_vida_autonomo():
    print("🤖 [NUBE] Jardinero de Flow iniciado...")
    while True:
        if MODO_AUTONOMO_ACTIVO:
            try:
                # Solo trabajamos si hay tareas explícitas para no saturar tu i3
                res = supabase.table('laboratorio_ideas').select('*').in_('estado', ['borrador']).limit(1).execute()
                
                if res.data:
                    tarea = res.data[0]
                    print(f"🧪 [PC] Procesando en background: {tarea['tema_objetivo']}")
                    
                    contenido = investigar_tema_flash(tarea['tema_objetivo'])
                    
                    if contenido and len(contenido) > 50:
                        pilar = CATALOGO_PILARES.get(tarea['pilar_destino'])
                        if pilar:
                            # Calculamos vector y guardamos directo (Flow Write)
                            vec = remote_embedding(f"{tarea['tema_objetivo']} {contenido}")
                            
                            supabase.rpc('cerebro_aprender', {
                                'p_orquestador_id': ORQUESTADOR_ID, 
                                'p_tabla_destino': pilar['nombre_tabla'],
                                'p_concepto': tarea['tema_objetivo'], 
                                'p_detalle': contenido,
                                'p_codigo': "Auto-Gen", 
                                'p_vector': vec
                            }).execute()
                            
                            supabase.table('laboratorio_ideas').delete().eq('id', tarea['id']).execute()
            except Exception as e:
                print(f"⚠️ Error ciclo: {e}")
        
        time.sleep(TIEMPO_ENTRE_CICLOS)

threading.Thread(target=ciclo_vida_autonomo, daemon=True).start()

# ==============================================================================
# 🚀 ENDPOINT DE ALTA VELOCIDAD
# ==============================================================================

@app.route("/preguntar", methods=["POST"])
def endpoint_preguntar():
    start_time = time.time()
    data = request.json
    pregunta = data.get('pregunta', '')
    print(f"\n📨 Usuario: {pregunta}")
    
    # 1. Vectorización (PC)
    vec = remote_embedding(pregunta)
    
    # 2. Recuperación HNSW (Nube - Ultra Rápido)
    # Usamos la estrategia de planificación rápida
    pilares = planificar_busqueda_rapida(pregunta)
    contexto = consultar_memoria_flow(pilares, vec)
    contexto_str = "\n".join(contexto) if contexto else ""
    
    # 3. Generación (PC)
    prompt = f"""
    CONTEXTO RECUPERADO DE BD:
    {contexto_str}
    
    PREGUNTA USUARIO: "{pregunta}"
    
    Eres un experto en Blender. Responde usando el contexto si es útil.
    Si hay código, usa bloques ```python. Sé breve y técnico.
    """
    respuesta = remote_generate(prompt)
    
    # 4. Formateo Manual (Python Puro - Cero Latencia)
    # Evitamos pedirle a la IA que genere JSON para ahorrar 3-5 segundos
    json_response = {
        "respuesta_principal": respuesta,
        "puntos_clave": [
            {"titulo": "Motor Híbrido", "descripcion": f"Qwen 2.5 (Local) + Supabase HNSW"},
            {"titulo": "Latencia Total", "descripcion": f"{time.time() - start_time:.2f} segundos"}
        ],
        "fuente": "Memoria Híbrida (Flow State)"
    }
    
    return jsonify(json_response)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
