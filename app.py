import os
import json
import re
import google.generativeai as genai
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from supabase import create_client, Client
from google.generativeai.types import HarmCategory, HarmBlockThreshold

# --- 1. CONFIGURACIÓN ---
print("--- Iniciando CEREBRO ORQUESTADOR (V5: Integrado y Estable) ---")
load_dotenv()

GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

if not all([GOOGLE_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
    raise ValueError("⚠️ Faltan variables de entorno (.env)")

genai.configure(api_key=GOOGLE_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

ORQUESTADOR_ID = 1 
EMBEDDING_MODEL = "models/text-embedding-004"
# Usamos el modelo flash por su velocidad para el 'Flow State'
GENERATIVE_MODEL = "models/gemini-2.5-flash" 

app = Flask(__name__)
CORS(app)

# --- CACHE DE PILARES ---
CATALOGO_PILARES = {} 

def cargar_catalogo():
    """
    Carga el mapa mental de la base de datos.
    Ahora asume que la DB está actualizada con 'criterio_admision'.
    """
    global CATALOGO_PILARES
    try:
        # Consulta directa optimizada
        response = supabase.table('catalogo_pilares')\
            .select('nombre_clave, nombre_tabla, descripcion, criterio_admision')\
            .eq('orquestador_id', ORQUESTADOR_ID)\
            .execute()
        
        if response.data:
            # Procesamos para asegurar que no haya valores nulos que rompan el código
            CATALOGO_PILARES = {
                item['nombre_clave']: {
                    **item,
                    'criterio_admision': item.get('criterio_admision') or "Información general relevante."
                } 
                for item in response.data
            }
            print(f"✅ Catálogo cargado: {len(CATALOGO_PILARES)} pilares listos y filtrados.")
        else:
            print("⚠️ ADVERTENCIA: El catálogo está vacío. Revisa tu base de datos.")
            
    except Exception as e:
        print(f"❌ Error crítico cargando catálogo: {e}")

# Carga inicial
cargar_catalogo()

# --- UTILIDADES ---

def limpiar_json(texto):
    texto = texto.strip()
    texto = re.sub(r'^```json\s*', '', texto)
    texto = re.sub(r'^```\s*', '', texto)
    texto = re.sub(r'\s*```$', '', texto)
    return texto.strip()

def get_embedding(text):
    try:
        res = genai.embed_content(model=EMBEDDING_MODEL, content=text, task_type="RETRIEVAL_QUERY")
        return res['embedding']
    except:
        return None

# --- MÓDULOS COGNITIVOS ---

def filtro_especialidad(pregunta):
    """EL PORTERO: Solo pasa Blender/3D."""
    modelo = genai.GenerativeModel(GENERATIVE_MODEL)
    prompt = f"""
    ERES UN FILTRO DE SEGURIDAD PARA UN ASISTENTE EXPERTO EN BLENDER 3D.
    PREGUNTA ENTRANTE: "{pregunta}"
    REGLAS:
    1. ACEPTAR solo si se relaciona con: Blender, Python, 3D, Render, Animación.
    2. RECHAZAR trivialidades o temas ajenos.
    JSON: {{ "es_relevante": true/false, "razon": "..." }}
    """
    try:
        res = modelo.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        return json.loads(limpiar_json(res.text))
    except:
        return {"es_relevante": True, "razon": "Error en filtro"}

def planificar_busqueda(pregunta):
    """Decide qué tablas consultar."""
    modelo = genai.GenerativeModel(GENERATIVE_MODEL)
    lista_pilares = "\n".join([f"- {k}: {v['descripcion']}" for k, v in CATALOGO_PILARES.items()])
    
    prompt = f"""
    Eres el Orquestador de Blender.
    PREGUNTA: "{pregunta}"
    TABLAS DISPONIBLES:
    {lista_pilares}
    
    JSON: {{ "pilares_seleccionados": ["nombre_clave"] }}
    """
    try:
        res = modelo.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        return json.loads(limpiar_json(res.text)).get("pilares_seleccionados", [])
    except:
        return ["api"]

def consultar_memoria_flow(pilares_objetivo, vector_pregunta):
    """USO DEL PODER DE LA BASE DE DATOS (HNSW)."""
    hallazgos = []
    for clave in pilares_objetivo:
        if clave not in CATALOGO_PILARES: continue
        tabla = CATALOGO_PILARES[clave]['nombre_tabla']
        try:
            # RPC OPTIMIZADA
            response = supabase.rpc('cerebro_recordar_flow', {
                'p_orquestador_id': ORQUESTADOR_ID,
                'p_tabla_destino': tabla,
                'p_vector': vector_pregunta,
                'p_umbral': 0.35,
                'p_limite': 5
            }).execute()
            
            if response.data:
                for item in response.data:
                    tipo = item.get('tipo_recuerdo', 'Directo')
                    hallazgos.append(f"[{clave.upper()} - {tipo}] {item['concepto']}: {item['detalle']} (Código: {item['codigo']})")
        except Exception as e:
            print(f"⚠️ Error en {tabla}: {e}")
    return hallazgos

def evaluar_suficiencia(pregunta, contexto_recuperado):
    """El Juez Crítico."""
    if not contexto_recuperado: return False, "Memoria vacía"
    
    modelo = genai.GenerativeModel(GENERATIVE_MODEL)
    contexto_str = "\n".join(contexto_recuperado)
    
    prompt = f"""
    PREGUNTA: "{pregunta}"
    MEMORIA: {contexto_str}
    ¿La memoria es SUFICIENTE para responder con código?
    JSON: {{ "es_suficiente": true/false, "razon_critica": "..." }}
    """
    try:
        res = modelo.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        ev = json.loads(limpiar_json(res.text))
        return ev.get("es_suficiente", False), ev.get("razon_critica", "N/A")
    except:
        return False, "Error evaluando"

def aprender_experto(pregunta, contexto_parcial=""):
    """INVESTIGACIÓN DIRIGIDA AL MANUAL DE BLENDER."""
    print("🌐 INVESTIGANDO EN FUENTES OFICIALES...")
    
    tools = [{"google_search": {}}]
    modelo_investigador = genai.GenerativeModel(GENERATIVE_MODEL, tools=tools)
    
    prompt_investigacion = f"""
    ACTÚA COMO UN INVESTIGADOR DE BLENDER.
    PREGUNTA: "{pregunta}"
    CONTEXTO PARCIAL: {contexto_parcial}
    
    FUENTES OBLIGATORIAS:
    1. [https://docs.blender.org/manual/es/4.0/](https://docs.blender.org/manual/es/4.0/)
    2. [https://docs.blender.org/api/current/](https://docs.blender.org/api/current/)
    
    Genera explicación técnica y SNIPPETS DE CÓDIGO.
    """
    
    try:
        res_investigacion = modelo_investigador.generate_content(prompt_investigacion)
        info_nueva = res_investigacion.text
        
        # Clasificación estricta usando CRITERIOS DE ADMISIÓN
        criterios_bd = "\n".join([f"- {k}: {v.get('criterio_admision', 'General')}" for k, v in CATALOGO_PILARES.items()])
        
        prompt_clasif = f"""
        CLASIFICA ESTE NUEVO CONOCIMIENTO:
        "{info_nueva}"
        
        REGLAS DE ADMISIÓN (FILTRO ESTRICTO):
        {criterios_bd}
        
        Si es trivial, tabla_destino: null.
        JSON: {{ "tabla_destino": "nombre_clave | null", "concepto": "...", "detalle": "...", "codigo": "..." }}
        """
        
        modelo_clasif = genai.GenerativeModel(GENERATIVE_MODEL)
        res_json = modelo_clasif.generate_content(prompt_clasif, generation_config={"response_mime_type": "application/json"})
        datos = json.loads(limpiar_json(res_json.text))
        
        if datos.get("tabla_destino") in CATALOGO_PILARES:
            tabla = CATALOGO_PILARES[datos["tabla_destino"]]['nombre_tabla']
            vec = get_embedding(f"{datos['concepto']} {datos['detalle']}")
            
            supabase.rpc('cerebro_aprender', {
                'p_orquestador_id': ORQUESTADOR_ID, 'p_tabla_destino': tabla,
                'p_concepto': datos['concepto'], 'p_detalle': datos['detalle'],
                'p_codigo': datos.get('codigo', ''), 'p_vector': vec
            }).execute()
            print(f"💾 APRENDIZAJE EXPERTO GUARDADO en {tabla}")
            
        return info_nueva
        
    except Exception as e:
        print(f"❌ Fallo en investigación: {e}")
        return "No pude conectar con el manual de Blender."

# --- ENDPOINTS ---

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "online", "mode": "Expert Flow Agent V5"}), 200

@app.route("/preguntar", methods=["POST"])
def endpoint_preguntar():
    data = request.json
    pregunta = data.get('pregunta', '')
    if not pregunta: return jsonify({"error": "Vacio"}), 400
    
    print(f"\n📨 [{pregunta}]")
    
    # 1. Filtro
    analisis = filtro_especialidad(pregunta)
    if not analisis.get("es_relevante", True):
        return jsonify({
            "respuesta_principal": "Solo respondo sobre Blender/3D.",
            "puntos_clave": [],
            "fuente": "Filtro de Seguridad"
        })

    # 2. Planificar
    pilares = planificar_busqueda(pregunta)
    
    # 3. Flow State (DB)
    vector = get_embedding(pregunta)
    contexto = consultar_memoria_flow(pilares, vector)
    
    # 4. Crítica
    es_suficiente = False
    if contexto:
        es_suficiente, _ = evaluar_suficiencia(pregunta, contexto)
    
    # 5. Respuesta
    modelo = genai.GenerativeModel(GENERATIVE_MODEL)
    if es_suficiente:
        fuente = "Memoria Experta (HNSW)"
        res = modelo.generate_content(f"Responde experto con: {contexto}. Pregunta: {pregunta}")
        texto = res.text
    else:
        fuente = "Manual Oficial Blender"
        texto = aprender_experto(pregunta, contexto)

    # 6. Salida JSON
    prompt_json = f"""
    FORMATO JSON:
    TEXTO: {texto}
    FUENTE: {fuente}
    JSON: {{ "respuesta_principal": "...", "puntos_clave": [{{ "titulo": "...", "descripcion": "..." }}], "fuente": "..." }}
    """
    try:
        res = modelo.generate_content(prompt_json, generation_config={"response_mime_type": "application/json"})
        return jsonify(json.loads(limpiar_json(res.text)))
    except:
        return jsonify({"respuesta_principal": texto, "puntos_clave": [], "fuente": fuente})

if __name__ == "__main__":
    app.run(debug=True, port=5000)
