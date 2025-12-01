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
print("--- Iniciando CEREBRO ORQUESTADOR (V6: Investigador Autónomo) ---")
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
GENERATIVE_MODEL = "models/gemini-2.5-flash" 

app = Flask(__name__)
CORS(app)

# --- CACHE DE PILARES ---
CATALOGO_PILARES = {} 

def cargar_catalogo():
    """
    Carga el mapa mental y asegura que existan criterios de admisión.
    """
    global CATALOGO_PILARES
    try:
        response = supabase.table('catalogo_pilares')\
            .select('nombre_clave, nombre_tabla, descripcion, criterio_admision')\
            .eq('orquestador_id', ORQUESTADOR_ID)\
            .execute()
        
        if response.data:
            CATALOGO_PILARES = {
                item['nombre_clave']: {
                    **item,
                    'criterio_admision': item.get('criterio_admision') or "Información relevante del tema."
                } 
                for item in response.data
            }
            print(f"✅ Catálogo cargado: {len(CATALOGO_PILARES)} pilares listos.")
        else:
            print("⚠️ ADVERTENCIA: Catálogo vacío.")
            
    except Exception as e:
        print(f"❌ Error cargando catálogo: {e}")

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
    """EL PORTERO: Mantiene el foco en Blender/3D."""
    modelo = genai.GenerativeModel(GENERATIVE_MODEL)
    prompt = f"""
    ERES UN FILTRO DE SEGURIDAD PARA UN ASISTENTE EXPERTO EN BLENDER 3D.
    PREGUNTA: "{pregunta}"
    REGLAS:
    1. ACEPTAR si se relaciona con: Blender, Python, 3D, Render, Animación, Matemáticas 3D.
    2. RECHAZAR trivialidades (clima, politica, cocina).
    JSON: {{ "es_relevante": true/false, "razon": "..." }}
    """
    try:
        res = modelo.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        return json.loads(limpiar_json(res.text))
    except:
        return {"es_relevante": True, "razon": "Error filtro"}

def planificar_busqueda(pregunta):
    """Decide qué tablas consultar en la memoria interna."""
    modelo = genai.GenerativeModel(GENERATIVE_MODEL)
    lista_pilares = "\n".join([f"- {k}: {v['descripcion']}" for k, v in CATALOGO_PILARES.items()])
    
    prompt = f"""
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
    """Búsqueda rápida en DB (HNSW)."""
    hallazgos = []
    for clave in pilares_objetivo:
        if clave not in CATALOGO_PILARES: continue
        tabla = CATALOGO_PILARES[clave]['nombre_tabla']
        try:
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
                    hallazgos.append(f"[{clave.upper()}-{tipo}] {item['concepto']}: {item['detalle']} (Código: {item['codigo']})")
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
    ¿La memoria es SUFICIENTE para responder con código y precisión?
    JSON: {{ "es_suficiente": true/false, "razon_critica": "..." }}
    """
    try:
        res = modelo.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        ev = json.loads(limpiar_json(res.text))
        return ev.get("es_suficiente", False), ev.get("razon_critica", "N/A")
    except:
        return False, "Error evaluando"

def aprender_autonomo(pregunta, contexto_parcial=""):
    """
    INVESTIGACIÓN AUTÓNOMA (Sin fuentes fijas).
    La IA decide dónde buscar basándose en la naturaleza de la pregunta.
    """
    print("🌐 INICIANDO PROTOCOLO DE INVESTIGACIÓN AUTÓNOMA...")
    
    # 1. Definir Estrategia de Búsqueda (Pensamiento)
    prompt_estrategia = f"""
    Eres un Investigador Senior de Gráficos 3D.
    PREGUNTA: "{pregunta}"
    CONTEXTO ACTUAL: {contexto_parcial}
    
    Decide qué fuentes son las mejores para esto.
    - Si es código -> Blender Python API, GitHub, StackOverflow.
    - Si es técnica -> Blender Manual, ArtStation, Foros especializados.
    - Si es matemáticas -> Recursos de geometría.
    
    Genera una respuesta completa y técnica basada en tu búsqueda.
    """
    
    info_nueva = ""
    
    # INTENTO 1: Google Search Grounding (Herramienta Real)
    try:
        tools = [{"google_search_retrieval": {}}] # Usamos la herramienta dinámica
        modelo_investigador = genai.GenerativeModel(GENERATIVE_MODEL, tools=tools)
        res_investigacion = modelo_investigador.generate_content(prompt_estrategia)
        info_nueva = res_investigacion.text
        print("✅ Investigación completada con éxito.")
    except Exception as e:
        print(f"⚠️ Fallo búsqueda dinámica ({e}). Usando conocimiento interno Latente...")
        # INTENTO 2: Fallback (Conocimiento Latente del Modelo)
        try:
            modelo_fallback = genai.GenerativeModel(GENERATIVE_MODEL)
            res_fallback = modelo_fallback.generate_content(prompt_estrategia + "\n(Nota: Usa tu vasto conocimiento interno ya que no hay internet).")
            info_nueva = res_fallback.text
        except Exception as e2:
            return "Error crítico: No pude generar respuesta."

    # --- FASE DE APRENDIZAJE PERMANENTE (ETL) ---
    # Aquí aseguramos que siga teniendo la capacidad de "subir informacion constantemente"
    try:
        criterios_bd = "\n".join([f"- {k}: {v.get('criterio_admision', 'General')}" for k, v in CATALOGO_PILARES.items()])
        
        prompt_clasif = f"""
        ANALIZA Y ESTRUCTURA ESTE NUEVO CONOCIMIENTO PARA LA BASE DE DATOS:
        "{info_nueva}"
        
        REGLAS DE ADMISIÓN (TABLAS):
        {criterios_bd}
        
        INSTRUCCIONES:
        1. Extrae el concepto técnico más valioso.
        2. Si hay código, extráelo limpio.
        3. Elige la tabla correcta. Si es irrelevante, usa null.
        
        JSON: {{ "tabla_destino": "nombre_clave | null", "concepto": "...", "detalle": "...", "codigo": "..." }}
        """
        
        modelo_clasif = genai.GenerativeModel(GENERATIVE_MODEL)
        res_json = modelo_clasif.generate_content(prompt_clasif, generation_config={"response_mime_type": "application/json"})
        datos = json.loads(limpiar_json(res_json.text))
        
        if datos.get("tabla_destino") in CATALOGO_PILARES:
            tabla = CATALOGO_PILARES[datos["tabla_destino"]]['nombre_tabla']
            vec = get_embedding(f"{datos['concepto']} {datos['detalle']}")
            
            # Guardamos en Supabase (Aprendizaje Permanente)
            supabase.rpc('cerebro_aprender', {
                'p_orquestador_id': ORQUESTADOR_ID, 
                'p_tabla_destino': tabla,
                'p_concepto': datos['concepto'], 
                'p_detalle': datos['detalle'],
                'p_codigo': datos.get('codigo', ''), 
                'p_vector': vec
            }).execute()
            print(f"💾 CONOCIMIENTO INYECTADO EN {tabla}: {datos['concepto']}")
            
    except Exception as e:
        print(f"❌ Error en el proceso de guardado: {e}")

    return info_nueva

# --- ENDPOINTS ---

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "online", "mode": "Autonomous Researcher V6"}), 200

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
    
    # 3. Consultar Memoria
    vector = get_embedding(pregunta)
    contexto = consultar_memoria_flow(pilares, vector)
    
    # 4. Crítica
    es_suficiente = False
    if contexto:
        es_suficiente, _ = evaluar_suficiencia(pregunta, contexto)
    
    # 5. Generar Respuesta
    modelo = genai.GenerativeModel(GENERATIVE_MODEL)
    if es_suficiente:
        fuente = "Memoria Interna (Experta)"
        res = modelo.generate_content(f"Responde experto con: {contexto}. Pregunta: {pregunta}")
        texto = res.text
    else:
        fuente = "Investigación Autónoma"
        texto = aprender_autonomo(pregunta, contexto)

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
