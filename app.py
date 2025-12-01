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

# --- 1. CONFIGURACIÓN ---
print("--- Iniciando ORQUESTADOR HÍBRIDO (V13: Conexión Directa) ---")
load_dotenv()

SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
REMOTE_LLM_URL = os.getenv('REMOTE_LLM_URL')

# Limpieza automática de la URL de Ngrok
if REMOTE_LLM_URL:
    REMOTE_LLM_URL = REMOTE_LLM_URL.strip().rstrip('/')
    if not REMOTE_LLM_URL.endswith('/api'):
        REMOTE_LLM_URL += '/api'
    print(f"✅ Conectando al Cerebro Local en: {REMOTE_LLM_URL}")

if not all([SUPABASE_URL, SUPABASE_KEY, REMOTE_LLM_URL]):
    # Fallback para pruebas locales
    if not REMOTE_LLM_URL: REMOTE_LLM_URL = "http://localhost:11434/api"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
ORQUESTADOR_ID = 1
CUSTOM_MODEL_NAME = "blender-expert"
MODO_AUTONOMO_ACTIVO = True
TIEMPO_ENTRE_CICLOS = 60

app = Flask(__name__)
CORS(app)

# --- CACHE DE PILARES ---
CATALOGO_PILARES = {}
def cargar_catalogo():
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
            print(f"✅ Catálogo cargado: {len(CATALOGO_PILARES)} pilares.")
    except Exception as e:
        print(f"❌ Error cargando catálogo: {e}")

cargar_catalogo()

# --- UTILIDADES DE CONEXIÓN REMOTA ---

def get_headers():
    """
    Headers esenciales. Solo necesitamos saltar la advertencia de Ngrok.
    Al haber configurado OLLAMA_ORIGINS='*' en tu PC, ya no necesitamos más trucos.
    """
    return {
        "ngrok-skip-browser-warning": "true",
        "Content-Type": "application/json",
        # CAMUFLAJE: Le decimos a Ollama que somos locales para evitar el bloqueo 403
        "User-Agent": "Ollama-Client/1.0",
        "Origin": "http://localhost:11434"
    }

def remote_generate(prompt, json_mode=False):
    payload = {
        "model": CUSTOM_MODEL_NAME,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.2,
            "num_predict": 300,
            "top_k": 40
        }
    }
    if json_mode: payload["format"] = "json"
    
    try:
        res = requests.post(
            f"{REMOTE_LLM_URL}/generate",
            json=payload,
            headers=get_headers(),
            timeout=120
        )
        
        if res.status_code == 200:
            return res.json().get("response", "")
        else:
            # Si falla ahora, será un error real de red, no de permisos
            print(f"❌ Error PC (Status {res.status_code}): {res.text[:100]}")
            return f"Error {res.status_code}: Falla en el cerebro local."
    except Exception as e:
        print(f"❌ Error de Conexión: {e}")
        return "Error: No se puede contactar con tu PC (Revisa Ngrok)."

def remote_embedding(text):
    try:
        res = requests.post(
            f"{REMOTE_LLM_URL}/embeddings",
            json={"model": "nomic-embed-text", "prompt": text},
            headers=get_headers(),
            timeout=30
        )
        if res.status_code == 200:
            return res.json().get("embedding")
        else:
            print(f"⚠️ Error Embedding (Status {res.status_code})")
            return None
    except: return None

def normalizar_json(texto):
    try:
        clean = re.sub(r'```json\s*|\s*```', '', texto.strip())
        return json.loads(clean)
    except: return {}

# ==============================================================================
# 🧬 LÓGICA COGNITIVA
# ==============================================================================

def determinar_nivel(conteo):
    if conteo < 5: return "NOVATO", "conceptos base"
    if conteo < 20: return "APRENDIZ", "flujos estándar"
    return "EXPERTO", "técnicas avanzadas"

def auditoria_sistema():
    stats = {}
    for clave, data in CATALOGO_PILARES.items():
        try:
            res = supabase.table(data['nombre_tabla']).select('id', count='exact').execute()
            stats[clave] = res.count
        except: stats[clave] = 0
    
    if not stats: return "api", "NOVATO", "bases"
    pilar = min(stats, key=stats.get)
    return pilar, *determinar_nivel(stats[pilar])

def generar_curriculum(pilar, nivel, estrategia):
    info = CATALOGO_PILARES.get(pilar)
    prompt = f"Eres maestro IA. Pilar: {info['nombre_clave']}. Nivel: {nivel}. Estrategia: {estrategia}. Genera UN título técnico faltante."
    return remote_generate(prompt).strip()

def investigar_tema(tema, nivel="EXPERTO"):
    prompt = f"ACTÚA COMO EXPERTO BLENDER. Tema: {tema}. Nivel: {nivel}. Explica técnicamente con código python."
    return remote_generate(prompt)

# ==============================================================================
# 🤖 CICLO DE VIDA AUTÓNOMO
# ==============================================================================
def ciclo_vida_autonomo():
    print("🤖 [NUBE] Orquestador Híbrido iniciado...")
    while True:
        if MODO_AUTONOMO_ACTIVO:
            try:
                res = supabase.table('laboratorio_ideas').select('*').in_('estado', ['borrador', 'rechazado']).limit(1).execute()
                tarea = res.data[0] if res.data else None
                
                if tarea:
                    print(f"🧪 [NUBE -> PC] Tarea: {tarea['tema_objetivo']}")
                    contenido = investigar_tema(tarea['tema_objetivo'])
                    
                    if not contenido or "Error" in contenido:
                        print("⚠️ PC no respondió. Reintentando...")
                        time.sleep(60)
                        continue

                    eval_prompt = f"Evalúa:\n{contenido}\nJSON: {{ \"aprobado\": true, \"critica\": \"ok\", \"codigo\": \"...\" }}"
                    evaluacion = normalizar_json(remote_generate(eval_prompt, json_mode=True))
                    
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
                                print(f"🎓 [NUBE] Guardado: {tarea['tema_objetivo']}")
                    else:
                        supabase.table('laboratorio_ideas').update({'estado': 'rechazado'}).eq('id', tarea['id']).execute()
                else:
                    pilar, nivel, estrategia = auditoria_sistema()
                    tema = generar_curriculum(pilar, nivel, estrategia)
                    if tema and "Error" not in tema:
                        supabase.table('laboratorio_ideas').insert({'orquestador_id': ORQUESTADOR_ID, 'tema_objetivo': tema, 'pilar_destino': pilar, 'estado': 'borrador'}).execute()
            
            except Exception as e: print(f"Error ciclo: {e}")
        time.sleep(TIEMPO_ENTRE_CICLOS)

threading.Thread(target=ciclo_vida_autonomo, daemon=True).start()

# ==============================================================================
# API PÚBLICA
# ==============================================================================

def planificar_busqueda(pregunta):
    pilares = "\n".join([f"- {k}: {v['descripcion']}" for k,v in CATALOGO_PILARES.items()])
    prompt = f"Pregunta: {pregunta}\nTablas:\n{pilares}\nJSON: {{ \"pilares_seleccionados\": [\"nombre_clave\"] }}"
    return normalizar_json(remote_generate(prompt, json_mode=True)).get("pilares_seleccionados", ["api"])

def consultar_memoria(pilares, vector):
    hallazgos = []
    if not vector: return []
    for clave in pilares:
        if clave in CATALOGO_PILARES:
            try:
                res = supabase.rpc('cerebro_recordar_flow', {'p_orquestador_id': ORQUESTADOR_ID, 'p_tabla_destino': CATALOGO_PILARES[clave]['nombre_tabla'], 'p_vector': vector, 'p_umbral': 0.35, 'p_limite': 3}).execute()
                if res.data: hallazgos.extend([f"[{clave}] {i['concepto']}: {i['detalle']}" for i in res.data])
            except: pass
    return hallazgos

@app.route("/preguntar", methods=["POST"])
def endpoint_preguntar():
    data = request.json
    pregunta = data.get('pregunta', '')
    print(f"\n📨 Usuario: {pregunta}")
    
    vec = remote_embedding(pregunta)
    pilares = planificar_busqueda(pregunta)
    contexto = consultar_memoria(pilares, vec)
    contexto_str = "\n".join(contexto) if contexto else ""
    
    fuente = "Memoria PC" if contexto else "Cerebro PC"
    prompt = f"Experto Blender. Contexto: {contexto_str}. Pregunta: {pregunta}. Responde."
    respuesta = remote_generate(prompt)
    
    if not respuesta or "Error" in respuesta:
        return jsonify({"respuesta_principal": "Error conectando con PC.", "puntos_clave": [], "fuente": "Error"})

    prompt_fmt = f"Formatea a JSON frontend.\nTexto: {respuesta}\nFuente: {fuente}\nJSON: {{ \"respuesta_principal\": \"...\", \"puntos_clave\": [{{ \"titulo\": \"...\", \"descripcion\": \"...\" }}], \"fuente\": \"{fuente}\" }}"
    json_final = normalizar_json(remote_generate(prompt_fmt, json_mode=True))
    
    if not json_final:
        json_final = {"respuesta_principal": respuesta, "puntos_clave": [], "fuente": fuente}

    return jsonify(json_final)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
