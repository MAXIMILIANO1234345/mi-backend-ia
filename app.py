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
print("--- Iniciando ORQUESTADOR HÍBRIDO (Cerebro Remoto: Tu PC) ---")
load_dotenv()

# Credenciales de Nube (Supabase)
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

# LA URL MÁGICA: La dirección de tu túnel Ngrok
REMOTE_LLM_URL = os.getenv('REMOTE_LLM_URL') 

# --- CORRECCIÓN AUTOMÁTICA DE URL ---
# Si el usuario olvidó poner '/api' al final en Render, lo arreglamos aquí.
if REMOTE_LLM_URL:
    REMOTE_LLM_URL = REMOTE_LLM_URL.strip().rstrip('/') # Quitar espacios y barra final
    if not REMOTE_LLM_URL.endswith('/api'):
        print(f"⚠️ Detectado URL sin '/api'. Corrigiendo automáticamente...")
        REMOTE_LLM_URL += '/api'
    print(f"✅ URL del Cerebro Configurada: {REMOTE_LLM_URL}")

# Validación de seguridad
if not all([SUPABASE_URL, SUPABASE_KEY, REMOTE_LLM_URL]):
    print("⚠️ Advertencia: Faltan variables de entorno en Render.")
    if not REMOTE_LLM_URL: REMOTE_LLM_URL = "http://localhost:11434/api"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
ORQUESTADOR_ID = 1 

# Nombre exacto de tu modelo en Ollama (PC)
CUSTOM_MODEL_NAME = "blender-expert" 

# Configuración Autónoma (El ciclo de vida sigue corriendo en la nube)
MODO_AUTONOMO_ACTIVO = True
TIEMPO_ENTRE_CICLOS = 60 # Segundos

app = Flask(__name__)
CORS(app)

# --- CACHE DE PILARES (Memoria RAM del Servidor) ---
CATALOGO_PILARES = {} 

def cargar_catalogo():
    """Carga la estructura de conocimiento desde Supabase."""
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

# --- UTILIDADES DE CONEXIÓN REMOTA (EL CABLE A TU CASA) ---

def remote_generate(prompt, json_mode=False):
    """
    Función Clave: Envía el prompt a tu PC via Ngrok.
    """
    payload = {
        "model": CUSTOM_MODEL_NAME,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.2,   # Precisión técnica
            "num_predict": 300,   # Longitud controlada
            "top_k": 40
        }
    }
    if json_mode: payload["format"] = "json"
    
    try:
        # Timeout alto (120s) porque la red doméstica puede tener latencia
        # print(f"📡 Llamando a la base ({REMOTE_LLM_URL})...") # Debug reducido
        res = requests.post(f"{REMOTE_LLM_URL}/generate", json=payload, timeout=120)
        
        if res.status_code == 200:
            return res.json().get("response", "")
        else:
            print(f"❌ Error PC (Status {res.status_code}): {res.text}")
            return f"Error: El cerebro remoto devolvió estatus {res.status_code}."
    except Exception as e:
        print(f"❌ Error Túnel: {e}")
        return "Error: No puedo conectar con tu PC. ¿Está encendido Ngrok?"

def remote_embedding(text):
    """Pide a tu PC que convierta texto a números (vectores)."""
    try:
        res = requests.post(f"{REMOTE_LLM_URL}/embeddings", json={"model": "nomic-embed-text", "prompt": text}, timeout=30)
        if res.status_code == 200:
            return res.json().get("embedding")
        else:
            print(f"⚠️ Error Embedding PC: {res.status_code}")
            return None
    except: return None

def normalizar_json(texto):
    """Limpia el JSON que llega de tu PC."""
    try:
        clean = re.sub(r'```json\s*|\s*```', '', texto.strip())
        return json.loads(clean)
    except: return {}

# ==============================================================================
# 🧬 LÓGICA COGNITIVA (El Cerebro Virtual)
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
# 🤖 CICLO DE VIDA AUTÓNOMO (El Jardinero Remoto)
# ==============================================================================
def ciclo_vida_autonomo():
    """
    Este hilo vive en la nube y coordina el trabajo,
    pero manda a tu PC a hacer la tarea.
    """
    print("🤖 [NUBE] Orquestador Híbrido iniciado...")
    while True:
        if MODO_AUTONOMO_ACTIVO:
            try:
                # 1. Verificar si hay tareas pendientes en Supabase
                res = supabase.table('laboratorio_ideas').select('*').in_('estado', ['borrador', 'rechazado']).limit(1).execute()
                tarea = res.data[0] if res.data else None
                
                if tarea:
                    print(f"🧪 [NUBE -> PC] Delegando tarea: {tarea['tema_objetivo']}")
                    
                    # Llamada a tu PC
                    contenido = investigar_tema(tarea['tema_objetivo'])
                    
                    if not contenido or "Error" in contenido:
                        print("⚠️ PC no respondió. Reintentando en 60s...")
                        time.sleep(60)
                        continue

                    # Juicio (Tu PC se evalúa a sí misma)
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
                                print(f"🎓 [NUBE] Tarea completada y guardada.")
                    else:
                        supabase.table('laboratorio_ideas').update({'estado': 'rechazado'}).eq('id', tarea['id']).execute()
                
                else:
                    # Crear tarea nueva (Auditoria)
                    pilar, nivel, estrategia = auditoria_sistema()
                    # print(f"💡 [NUBE] Auditando... Pilar débil: {pilar}")
                    tema = generar_curriculum(pilar, nivel, estrategia)
                    if tema and "Error" not in tema:
                        supabase.table('laboratorio_ideas').insert({
                            'orquestador_id': ORQUESTADOR_ID, 'tema_objetivo': tema, 'pilar_destino': pilar, 'estado': 'borrador'
                        }).execute()
                        print(f"💡 [NUBE] Nueva orden de trabajo creada: {tema}")
            
            except Exception as e: print(f"Error ciclo híbrido: {e}")
        
        time.sleep(TIEMPO_ENTRE_CICLOS)

# Iniciamos el hilo secundario
threading.Thread(target=ciclo_vida_autonomo, daemon=True).start()

# ==============================================================================
# API PÚBLICA (ENDPOINT WEB)
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
    print(f"\n📨 Usuario Web: {pregunta}")
    
    # 1. Tu PC calcula el vector
    vec = remote_embedding(pregunta)
    
    # 2. Render busca en Supabase
    pilares = planificar_busqueda(pregunta)
    contexto = consultar_memoria(pilares, vec)
    contexto_str = "\n".join(contexto) if contexto else ""
    
    # 3. Tu PC redacta la respuesta
    fuente = "Memoria Experta (Tu PC)" if contexto else "Conocimiento Latente (Tu PC)"
    prompt = f"Experto Blender. Contexto recuperado: {contexto_str}. Pregunta usuario: {pregunta}. Responde técnicamente."
    respuesta = remote_generate(prompt)
    
    if not respuesta or "Error" in respuesta:
        return jsonify({
            "respuesta_principal": "Lo siento, no puedo conectar con mi cerebro local en este momento. Verifica que Ngrok esté encendido.",
            "puntos_clave": [],
            "fuente": "Error de Conexión"
        })

    # 4. Formateo Final
    prompt_fmt = f"Formatea a JSON frontend.\nTexto: {respuesta}\nFuente: {fuente}\nJSON: {{ \"respuesta_principal\": \"...\", \"puntos_clave\": [{{ \"titulo\": \"...\", \"descripcion\": \"...\" }}], \"fuente\": \"{fuente}\" }}"
    json_final = normalizar_json(remote_generate(prompt_fmt, json_mode=True))
    
    if not json_final:
        json_final = {"respuesta_principal": respuesta, "puntos_clave": [], "fuente": fuente}

    return jsonify(json_final)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
