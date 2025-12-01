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
print("--- Iniciando CEREBRO ORQUESTADOR (V4: Especialista + Flow State) ---")
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
    global CATALOGO_PILARES
    try:
        # Traemos también el criterio de admisión para que el filtro sea inteligente
        response = supabase.table('catalogo_pilares')\
            .select('nombre_clave, nombre_tabla, descripcion, criterio_admision')\
            .eq('orquestador_id', ORQUESTADOR_ID)\
            .execute()
        if response.data:
            CATALOGO_PILARES = {item['nombre_clave']: item for item in response.data}
            print(f"✅ Catálogo cargado: {len(CATALOGO_PILARES)} pilares listos.")
    except Exception as e:
        print(f"❌ Error catálogo: {e}")

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
    """
    EL PORTERO: Rechaza preguntas triviales o fuera del dominio.
    Ahorra tiempo y evita "basura" en la base de datos.
    """
    modelo = genai.GenerativeModel(GENERATIVE_MODEL)
    prompt = f"""
    ERES UN FILTRO DE SEGURIDAD PARA UN ASISTENTE EXPERTO EN BLENDER 3D.
    
    PREGUNTA ENTRANTE: "{pregunta}"
    
    REGLAS DE ADMISIÓN:
    1. ACEPTAR solo si se relaciona con: Blender, Python (bpy), Modelado 3D, Render, Animación o Gráficos por Computadora.
    2. RECHAZAR si es sobre: Cocina, Política, Deportes, Clima, Chistes o Saludos vacíos ("hola" sin pregunta).
    
    RESPONDE JSON:
    {{
        "es_relevante": true/false,
        "razon": "Breve motivo"
    }}
    """
    try:
        res = modelo.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        return json.loads(limpiar_json(res.text))
    except:
        return {"es_relevante": True, "razon": "Pass-through por error"}

def planificar_busqueda(pregunta):
    """Decide qué tablas consultar."""
    modelo = genai.GenerativeModel(GENERATIVE_MODEL)
    lista_pilares = "\n".join([f"- {k}: {v['descripcion']}" for k, v in CATALOGO_PILARES.items()])
    
    prompt = f"""
    Eres el Orquestador de Blender.
    PREGUNTA: "{pregunta}"
    TABLAS:
    {lista_pilares}
    
    JSON: {{ "pilares_seleccionados": ["nombre_clave"] }}
    """
    try:
        res = modelo.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        return json.loads(limpiar_json(res.text)).get("pilares_seleccionados", [])
    except:
        return ["api"]

def consultar_memoria_flow(pilares_objetivo, vector_pregunta):
    """
    USO DEL PODER DE LA BASE DE DATOS:
    Llama a 'cerebro_recordar_flow' (definida en el SQL optimizado)
    para usar índices HNSW y obtener respuesta ultra-rápida.
    """
    hallazgos = []
    for clave in pilares_objetivo:
        if clave not in CATALOGO_PILARES: continue
        tabla = CATALOGO_PILARES[clave]['nombre_tabla']
        try:
            # RPC OPTIMIZADA (Flow State)
            response = supabase.rpc('cerebro_recordar_flow', {
                'p_orquestador_id': ORQUESTADOR_ID,
                'p_tabla_destino': tabla,
                'p_vector': vector_pregunta,
                'p_umbral': 0.35,
                'p_limite': 5
            }).execute()
            
            if response.data:
                for item in response.data:
                    # Notar que el SQL devuelve 'tipo_recuerdo' (Directo/Asociado)
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
    
    ¿La memoria tiene código y detalles técnicos SUFICIENTES para responder?
    JSON: {{ "es_suficiente": true/false, "razon_critica": "..." }}
    """
    try:
        res = modelo.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        ev = json.loads(limpiar_json(res.text))
        return ev.get("es_suficiente", False), ev.get("razon_critica", "N/A")
    except:
        return False, "Error evaluando"

def aprender_experto(pregunta, contexto_parcial=""):
    """
    INVESTIGACIÓN DIRIGIDA Y PROFESIONAL.
    Usa herramientas de búsqueda (simuladas por prompt grounding) 
    enfocándose en la documentación oficial.
    """
    print("🌐 INVESTIGANDO EN FUENTES OFICIALES (Blender Docs)...")
    
    # Configuramos el modelo con herramientas de búsqueda de Google si es posible
    tools = [
        {"google_search": {}} # Habilitamos búsqueda real
    ]
    modelo_investigador = genai.GenerativeModel(GENERATIVE_MODEL, tools=tools)
    
    prompt_investigacion = f"""
    ACTÚA COMO UN INVESTIGADOR DE DOCUMENTACIÓN TÉCNICA.
    
    PREGUNTA: "{pregunta}"
    CONTEXTO PARCIAL: {contexto_parcial}
    
    TU MISIÓN:
    Busca información técnica actualizada y precisa.
    
    FUENTES PRIORITARIAS OBLIGATORIAS:
    1. Manual Oficial de Blender ([https://docs.blender.org/manual/es/4.0/](https://docs.blender.org/manual/es/4.0/))
    2. Blender Python API ([https://docs.blender.org/api/current/](https://docs.blender.org/api/current/))
    3. Blender Stack Exchange / BlenderArtists
    
    Genera una explicación técnica completa con SNIPPETS DE CÓDIGO FUNCIONALES.
    """
    
    try:
        res_investigacion = modelo_investigador.generate_content(prompt_investigacion)
        info_nueva = res_investigacion.text
        
        # Clasificación estricta usando los criterios de la BD
        criterios_bd = "\n".join([f"- {k}: {v['criterio_admision']}" for k, v in CATALOGO_PILARES.items()])
        
        prompt_clasif = f"""
        CLASIFICA ESTE NUEVO CONOCIMIENTO:
        "{info_nueva}"
        
        REGLAS DE ADMISIÓN DE LA BASE DE DATOS:
        {criterios_bd}
        
        Si la información es vaga o trivial, NO la guardes (tabla_destino: null).
        Si es valiosa, elige la tabla correcta.
        
        JSON: {{ "tabla_destino": "nombre_clave | null", "concepto": "...", "detalle": "...", "codigo": "..." }}
        """
        
        modelo_clasif = genai.GenerativeModel(GENERATIVE_MODEL) # Modelo normal para JSON
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
        return "No pude conectar con el manual de Blender en este momento."

# --- ENDPOINTS ---

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "online", "mode": "Expert Flow Agent"}), 200

@app.route("/preguntar", methods=["POST"])
def endpoint_preguntar():
    data = request.json
    pregunta = data.get('pregunta', '')
    if not pregunta: return jsonify({"error": "Vacio"}), 400
    
    print(f"\n📨 [{pregunta}]")
    
    # 1. EL PORTERO (Filtro)
    analisis_entrada = filtro_especialidad(pregunta)
    if not analisis_entrada.get("es_relevante", True):
        print(f"⛔ Rechazado: {analisis_entrada.get('razon')}")
        return jsonify({
            "respuesta_principal": "Solo puedo responder preguntas sobre Blender, Python y 3D.",
            "puntos_clave": [{"titulo": "Filtro de Especialidad", "descripcion": analisis_entrada.get('razon')}],
            "fuente": "Sistema de Seguridad"
        })

    # 2. PLANIFICAR
    pilares = planificar_busqueda(pregunta)
    
    # 3. CONSULTA OPTIMIZADA (Flow State)
    vector = get_embedding(pregunta)
    contexto = consultar_memoria_flow(pilares, vector) # ¡Usa la función SQL nueva!
    
    # 4. CRÍTICA
    es_suficiente = False
    razon = "Inicio"
    if contexto:
        es_suficiente, razon = evaluar_suficiencia(pregunta, contexto)
        print(f"⚖️ ¿Suficiente?: {es_suficiente} ({razon})")
    
    respuesta_texto = ""
    fuente = ""
    
    modelo = genai.GenerativeModel(GENERATIVE_MODEL)

    if es_suficiente:
        fuente = "Memoria Experta (HNSW Index)"
        prompt_final = f"Responde experto con este contexto: {contexto}. Pregunta: {pregunta}"
        res = modelo.generate_content(prompt_final)
        respuesta_texto = res.text
    else:
        # Aquí entra la investigación dirigida al manual
        fuente = "Manual Oficial Blender & Foros"
        respuesta_texto = aprender_experto(pregunta, contexto)

    # 5. SALIDA JSON
    prompt_json = f"""
    FORMATO JSON PARA FRONTEND:
    TEXTO: {respuesta_texto}
    FUENTE: {fuente}
    
    JSON: {{ "respuesta_principal": "...", "puntos_clave": [{{ "titulo": "...", "descripcion": "..." }}], "fuente": "..." }}
    """
    try:
        res = modelo.generate_content(prompt_json, generation_config={"response_mime_type": "application/json"})
        return jsonify(json.loads(limpiar_json(res.text)))
    except:
        return jsonify({"respuesta_principal": respuesta_texto, "puntos_clave": [], "fuente": fuente})

if __name__ == "__main__":
    app.run(debug=True, port=5000)
