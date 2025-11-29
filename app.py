import os
import json
import datetime
import re
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import google.generativeai as genai
from supabase import create_client, Client

# --- 1. CONFIGURACIÓN ---
print("--- Iniciando API Proyecto 17 (Modo GraphRAG) ---")
load_dotenv()

# Variables de Entorno
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

if not all([GOOGLE_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
    print("⚠️ CRÍTICO: Faltan variables de entorno.")

# Inicializar Clientes
try:
    genai.configure(api_key=GOOGLE_API_KEY)
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Conexión establecida: Supabase Graph + Gemini.")
except Exception as e:
    print(f"❌ Error fatal en inicialización: {e}")

# Modelos (CORREGIDO: Usamos 1.5-flash que es estable y rápido)
EMBEDDING_MODEL = "models/text-embedding-004"
GENERATIVE_MODEL = "models/gemini-2.5-flash" 

app = Flask(__name__)
CORS(app)

# --- 2. FUNCIONES AUXILIARES ---

def get_text_embedding(text):
    """Genera vector de 768 dimensiones."""
    try:
        result = genai.embed_content(
            model=EMBEDDING_MODEL,
            content=text,
            task_type="RETRIEVAL_QUERY"
        )
        return result['embedding']
    except Exception as e:
        print(f"Error vectorizando: {e}")
        return None

def limpiar_respuesta_json(texto_bruto):
    """
    Elimina los bloques de código Markdown ```json y ``` que Gemini a veces añade.
    Esto previene errores de parseo en el Frontend.
    """
    texto_limpio = texto_bruto.strip()
    # Eliminar ```json al inicio y ``` al final si existen
    if texto_limpio.startswith("```json"):
        texto_limpio = texto_limpio[7:]
    if texto_limpio.startswith("```"):
        texto_limpio = texto_limpio[3:]
    if texto_limpio.endswith("```"):
        texto_limpio = texto_limpio[:-3]
    return texto_limpio.strip()

# ==============================================================================
# === FLUJO 1: ASISTENTE BLENDER (GRAPHRAG) ===
# ==============================================================================

def buscar_nodos_conceptuales(pregunta_usuario):
    """Paso 1: BÚSQUEDA VECTORIAL DE NODOS"""
    vector = get_text_embedding(pregunta_usuario)
    if not vector: return []

    try:
        # RPC call a Supabase
        response = supabase.rpc('buscar_nodos', {
            'query_embedding': vector,
            'match_threshold': 0.55,
            'match_count': 5
        }).execute()
        return response.data or []
    except Exception as e:
        print(f"❌ Error buscando nodos: {e}")
        return []

def expandir_relaciones(nodos_encontrados):
    """Paso 2: TRAVESÍA DEL GRAFO (SQL)"""
    if not nodos_encontrados:
        return []

    ids_nodos = [n['id'] for n in nodos_encontrados]
    contexto_grafo = []

    try:
        # Consulta JOIN manual
        response = supabase.table('relaciones')\
            .select('relacion, origen_id, destino_id, nodos!destino_id(nombre, descripcion)')\
            .in_('origen_id', ids_nodos)\
            .execute()

        mapa_nombres = {n['id']: n['nombre'] for n in nodos_encontrados}

        if response.data:
            for rel in response.data:
                nombre_origen = mapa_nombres.get(rel['origen_id'], "Concepto")
                datos_destino = rel.get('nodos') 
                nombre_destino = datos_destino.get('nombre') if datos_destino else "Desconocido"
                desc_destino = datos_destino.get('descripcion') if datos_destino else ""
                tipo_rel = rel['relacion']

                hecho = f"- {nombre_origen} --[{tipo_rel}]--> {nombre_destino} ({desc_destino})"
                contexto_grafo.append(hecho)

    except Exception as e:
        print(f"⚠️ Error expandiendo grafo: {e}")
    
    return contexto_grafo

def generar_respuesta_grafo(pregunta, nodos, relaciones, modelo):
    """Paso 3: RAZONAMIENTO CON GRAFOS"""
    
    definiciones = "\n".join([f"* {n['nombre']}: {n['descripcion']}" for n in nodos])
    conexiones = "\n".join(relaciones) if relaciones else "Sin conexiones directas encontradas."

    if not nodos:
        # Retornamos una respuesta válida para que el frontend no falle
        return {
            "respuesta_principal": "No encontré conceptos relacionados en mi base de conocimientos.",
            "puntos_clave": [],
            "fuente": "Base de datos vacía"
        }

    prompt = f"""
    Eres un experto en Blender 3D usando un Grafo de Conocimiento.
    
    PREGUNTA: "{pregunta}"
    
    DATOS DEL GRAFO:
    -- CONCEPTOS --
    {definiciones}
    
    -- CONEXIONES --
    {conexiones}
    
    Instrucciones:
    1. Explica CAUSA y EFECTO basándote en las conexiones.
    2. Si hay pasos técnicos, enuméralos.
    
    Salida JSON:
    {{
        "respuesta_principal": "Respuesta...",
        "puntos_clave": ["Dato 1", "Dato 2"],
        "fuente": "Grafo de Conocimiento"
    }}
    """
    try:
        res = modelo.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        texto_limpio = limpiar_respuesta_json(res.text) # LIMPIEZA AGREGADA
        return json.loads(texto_limpio)
    except Exception as e:
        print(f"Error generando respuesta: {e}")
        return {
            "respuesta_principal": "Hubo un error técnico generando la respuesta.",
            "puntos_clave": [],
            "fuente": "Error de Sistema"
        }

# ==============================================================================
# === FLUJO 2: GENERADOR DE SCRIPTS ===
# ==============================================================================

def generar_script_blender(prompt_usuario, modelo):
    prompt_bpy = f"""
    Genera script Python (bpy) para Blender 3.6+: "{prompt_usuario}"
    JSON Output: {{ "script": "import bpy..." }}
    """
    try:
        res = modelo.generate_content(prompt_bpy, generation_config={"response_mime_type": "application/json"})
        texto_limpio = limpiar_respuesta_json(res.text)
        return json.loads(texto_limpio)
    except:
        return {"script": "# Error generando script"}

# --- ENDPOINTS ---

@app.route("/", methods=["GET"])
def health_check():
    return jsonify({"status": "online", "mode": "GraphRAG"}), 200

@app.route("/preguntar", methods=["POST"])
def endpoint_preguntar():
    data = request.json
    pregunta = data.get('pregunta')
    if not pregunta: return jsonify({"error": "Pregunta vacía"}), 400
    
    modelo = genai.GenerativeModel(GENERATIVE_MODEL)
    
    # 1. Buscar
    nodos = buscar_nodos_conceptuales(pregunta)
    relaciones = expandir_relaciones(nodos)
    
    # 2. Responder
    respuesta = generar_respuesta_grafo(pregunta, nodos, relaciones, modelo)
    
    return jsonify(respuesta)

@app.route("/generar-script", methods=["POST"])
def endpoint_script():
    data = request.json
    prompt_usuario = data.get('pregunta')
    if not prompt_usuario: return jsonify({"error": "Falta prompt"}), 400
    
    modelo = genai.GenerativeModel(GENERATIVE_MODEL)
    resultado = generar_script_blender(prompt_usuario, modelo)
    
    # Asegurar que siempre devolvemos un string en 'script' para evitar el error .replace en JS
    script_seguro = resultado.get("script", "# Error en generación")
    if not isinstance(script_seguro, str):
        script_seguro = str(script_seguro)

    return jsonify({
        "script": script_seguro,
        "status": "OK",
        "info": "Generado con GraphRAG API"
    })

if __name__ == "__main__":
    app.run(debug=True, port=5000)

