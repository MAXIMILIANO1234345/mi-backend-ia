import os
import json
import google.generativeai as genai
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from supabase import create_client, Client

# --- 1. CONFIGURACIÓN ---
print("--- Iniciando API Proyecto 17 (GraphRAG Mejorado) ---")
load_dotenv()

GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

if not all([GOOGLE_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
    raise ValueError("⚠️ Faltan variables de entorno (.env)")

# Configuración Gemini
genai.configure(api_key=GOOGLE_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# MODELOS OPTIMIZADOS
# Nota: text-embedding-004 genera vectores de 768 dimensiones
EMBEDDING_MODEL = "models/text-embedding-004"
GENERATIVE_MODEL = "models/gemini-2.5-flash" 

app = Flask(__name__)
CORS(app)

# --- 2. FUNCIONES DE VECTORIZACIÓN ---

def get_text_embedding(text):
    """Genera vector de 768 dimensiones para búsqueda."""
    try:
        # task_type="RETRIEVAL_QUERY" optimiza el vector para búsquedas
        result = genai.embed_content(
            model=EMBEDDING_MODEL,
            content=text,
            task_type="RETRIEVAL_QUERY"
        )
        return result['embedding']
    except Exception as e:
        print(f"❌ Error generando embedding: {e}")
        return None

def limpiar_respuesta_json(texto_bruto):
    """Limpia wrappers de Markdown para evitar errores de JSON."""
    texto = texto_bruto.strip()
    if texto.startswith("```json"): texto = texto[7:]
    elif texto.startswith("```"): texto = texto[3:]
    if texto.endswith("```"): texto = texto[:-3]
    return texto.strip()

# --- 3. LÓGICA GRAPHRAG (BÚSQUEDA REAL) ---

def buscar_contexto_rag(pregunta_usuario):
    """
    Realiza la búsqueda vectorial en Supabase y recupera relaciones.
    """
    # 1. Vectorizar pregunta
    vector_busqueda = get_text_embedding(pregunta_usuario)
    if not vector_busqueda:
        return [], []

    nodos_encontrados = []
    
    try:
        # 2. Llamada RPC a Supabase (Match Vectorial)
        # Bajamos el threshold a 0.50 para encontrar más coincidencias
        response = supabase.rpc('buscar_nodos', {
            'query_embedding': vector_busqueda,
            'match_threshold': 0.50, 
            'match_count': 6
        }).execute()
        
        nodos_encontrados = response.data or []
        print(f"🔍 Nodos encontrados: {len(nodos_encontrados)}")
    except Exception as e:
        print(f"❌ Error en RPC buscar_nodos: {e}")
        return [], []

    # 3. Expandir Grafo (Buscar relaciones de esos nodos)
    relaciones_contexto = []
    if nodos_encontrados:
        ids_nodos = [n['id'] for n in nodos_encontrados]
        try:
            # Buscamos relaciones donde el origen o destino sean los nodos encontrados
            rel_response = supabase.table('relaciones')\
                .select('relacion, origen_id, destino_id, nodo_origen:nodos!origen_id(nombre), nodo_destino:nodos!destino_id(nombre, descripcion)')\
                .in_('origen_id', ids_nodos)\
                .limit(10)\
                .execute()
            
            if rel_response.data:
                for r in rel_response.data:
                    origen = r['nodo_origen']['nombre'] if r['nodo_origen'] else "Concepto"
                    destino = r['nodo_destino']['nombre'] if r['nodo_destino'] else "Algo"
                    desc = r['nodo_destino']['descripcion'] if r['nodo_destino'] else ""
                    tipo = r['relacion']
                    relaciones_contexto.append(f"{origen} --[{tipo}]--> {destino} ({desc})")
                    
        except Exception as e:
            print(f"⚠️ Error expandiendo relaciones: {e}")

    return nodos_encontrados, relaciones_contexto

def generar_respuesta(pregunta, nodos, relaciones):
    modelo = genai.GenerativeModel(GENERATIVE_MODEL)

    # Convertir contexto a texto
    txt_nodos = "\n".join([f"- {n['nombre']}: {n['descripcion']}" for n in nodos])
    txt_rels = "\n".join(relaciones)
    
    # Prompt de Sistema (GraphRAG estricto)
    prompt = f"""
    Actúa como un Asistente Experto en Blender 3D. Tu objetivo es responder usando la información recuperada de la base de datos (Contexto).

    PREGUNTA DEL USUARIO: "{pregunta}"

    --- CONTEXTO RECUPERADO (Base de Conocimiento) ---
    CONCEPTOS:
    {txt_nodos}

    RELACIONES:
    {txt_rels}
    --------------------------------------------------

    INSTRUCCIONES:
    1. Analiza el CONTEXTO proporcionado arriba.
    2. Si el contexto contiene la respuesta, úsalo como fuente principal y cita las relaciones.
    3. Si el contexto es vacío o insuficiente, responde usando tu conocimiento general de Blender, pero inicia la respuesta con: "Nota: No encontré información específica en tu base de datos, pero aquí tienes una respuesta general:".
    4. Formatea la salida estrictamente como JSON.

    SALIDA JSON ESPERADA:
    {{
        "respuesta_principal": "Explicación detallada...",
        "puntos_clave": ["Punto 1", "Punto 2"],
        "fuente": "Grafo" o "Conocimiento General"
    }}
    """

    try:
        res = modelo.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        return json.loads(limpiar_respuesta_json(res.text))
    except Exception as e:
        print(f"Error generando: {e}")
        return {
            "respuesta_principal": "Error procesando la respuesta.",
            "puntos_clave": [],
            "fuente": "Error"
        }

# --- ENDPOINTS ---

@app.route("/preguntar", methods=["POST"])
def endpoint_preguntar():
    data = request.json
    pregunta = data.get('pregunta', '')
    
    # 1. Búsqueda RAG
    nodos, relaciones = buscar_contexto_rag(pregunta)
    
    # 2. Generación
    respuesta = generar_respuesta(pregunta, nodos, relaciones)
    
    return jsonify(respuesta)

@app.route("/generar-script", methods=["POST"])
def endpoint_script():
    data = request.json
    pregunta = data.get('pregunta', '')
    modelo = genai.GenerativeModel(GENERATIVE_MODEL)
    
    prompt = f'Crea un script de Python para Blender (bpy) que haga: "{pregunta}". Devuelve JSON {{ "script": "código..." }}'
    
    try:
        res = modelo.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        return jsonify(json.loads(limpiar_respuesta_json(res.text)))
    except:
        return jsonify({"script": "# Error generando script"})

if __name__ == "__main__":
    app.run(debug=True, port=5000)

