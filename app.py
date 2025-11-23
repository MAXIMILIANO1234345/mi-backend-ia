import os
import json
import re
import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import google.generativeai as genai
from supabase import create_client, Client

# --- 1. CONFIGURACIÓN ---
print("--- Iniciando API Proyecto 17 (Modo Relacional) ---")
load_dotenv()

# Variables de Entorno (Render Dashboard)
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

# Validación
if not all([GOOGLE_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
    print("⚠️ CRÍTICO: Faltan variables de entorno.")

# Inicializar Clientes
try:
    genai.configure(api_key=GOOGLE_API_KEY)
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Conexión establecida: Supabase + Gemini.")
except Exception as e:
    print(f"❌ Error fatal en inicialización: {e}")

# Modelos
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

# ==============================================================================
# === FLUJO 1: ASISTENTE DEL MANUAL (RAG RELACIONAL) ===
# ==============================================================================

def get_plan_de_busqueda(pregunta_usuario, modelo):
    """
    Paso 1.1: Deduce qué buscar.
    """
    prompt = f"""
    Eres un bibliotecario experto en el Manual de Blender (Proyecto 17).
    Pregunta: "{pregunta_usuario}"
    
    Genera 2 frases de búsqueda optimizadas para encontrar la respuesta en la base de datos.
    Responde SOLO JSON: {{"consultas": ["frase 1", "frase 2"]}}
    """
    try:
        res = modelo.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        return json.loads(res.text).get('consultas', [pregunta_usuario])
    except:
        return [pregunta_usuario]

def buscar_en_supabase_relacional(consultas):
    """
    Paso 1.2: Búsqueda Relacional.
    Llama a la función RPC 'buscar_secciones_manual' que hace JOIN entre
    Secciones, Capítulos y Partes.
    """
    resultados_unicos = {}
    
    for consulta in consultas:
        vector = get_text_embedding(consulta)
        if not vector: continue
        
        try:
            # Llamada a la función SQL personalizada que une las tablas
            response = supabase.rpc('buscar_secciones_manual', {
                'query_embedding': vector,
                'match_threshold': 0.45,
                'match_count': 3
            }).execute()
            
            if response.data:
                for item in response.data:
                    # Usamos ID de la sección para evitar duplicados
                    resultados_unicos[item['id']] = item
        except Exception as e:
            print(f"Error consultando Supabase: {e}")

    return list(resultados_unicos.values())

def generar_respuesta_rag(pregunta, contexto_items, modelo):
    """
    Paso 1.3: Genera respuesta citando la jerarquía (Parte > Capítulo).
    """
    contexto_str = ""
    fuentes = set()
    
    for item in contexto_items:
        # Extraemos datos directos de la respuesta RPC (ya vienen del JOIN)
        contenido = item.get('contenido', '')
        # Ajusta estos nombres según lo que devuelva tu función SQL
        capitulo = item.get('capitulo_titulo', 'Capítulo General') 
        parte = item.get('parte_titulo', 'Sección Base')
        
        referencia = f"{parte} > {capitulo}"
        fuentes.add(capitulo)
        
        contexto_str += f"--- DE: {referencia} ---\n{contenido}\n\n"
    
    fuente_final = ", ".join(fuentes) if fuentes else "Conocimiento General Blender"

    prompt = f"""
    Eres el Asistente Docente del Proyecto 17.
    Usa EXCLUSIVAMENTE el contexto para responder.
    
    CONTEXTO DEL MANUAL:
    {contexto_str}
    
    PREGUNTA DOCENTE: {pregunta}
    
    Responde en JSON:
    {{
        "respuesta_principal": "Explicación clara...",
        "puntos_clave": [{{"titulo": "...", "descripcion": "..."}}],
        "fuente": "Basado en: {fuente_final}"
    }}
    """
    try:
        res = modelo.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        return json.loads(res.text)
    except Exception as e:
        return {"respuesta_principal": "Error procesando respuesta.", "puntos_clave": [], "fuente": ""}

# ==============================================================================
# === FLUJO 2: GENERADOR DE SCRIPTS (ASSETS) ===
# ==============================================================================

def generar_script_blender(prompt_usuario, modelo):
    """Genera código bpy."""
    prompt_bpy = f"""
    Genera script Python (bpy) para Blender 3.6+ para: "{prompt_usuario}"
    REGLAS:
    1. import bpy
    2. Borrar objetos default.
    3. Usar `bpy.context.collection.objects.link`.
    4. Render Engine: CYCLES, Device: GPU.
    
    Salida JSON: {{ "script": "import bpy..." }}
    """
    try:
        res = modelo.generate_content(prompt_bpy, generation_config={"response_mime_type": "application/json"})
        return json.loads(res.text)
    except:
        return {"script": "# Error generando script"}

# --- ENDPOINTS ---

@app.route("/", methods=["GET"])
def health_check():
    return jsonify({"status": "online", "mode": "Relacional Supabase"}), 200

@app.route("/preguntar", methods=["POST"])
def endpoint_preguntar():
    data = request.json
    pregunta = data.get('pregunta')
    if not pregunta: return jsonify({"error": "Pregunta vacía"}), 400
    
    modelo = genai.GenerativeModel(GENERATIVE_MODEL)
    
    # 1. Planificar y Buscar (Relacional)
    consultas = get_plan_de_busqueda(pregunta, modelo)
    contexto = buscar_en_supabase_relacional(consultas)
    
    # 2. Responder
    respuesta = generar_respuesta_rag(pregunta, contexto, modelo)
    
    return jsonify(respuesta)

@app.route("/generar-script", methods=["POST"])
def endpoint_script():
    data = request.json
    prompt_usuario = data.get('pregunta')
    if not prompt_usuario: return jsonify({"error": "Falta prompt"}), 400
    
    modelo = genai.GenerativeModel(GENERATIVE_MODEL)
    
    # 1. Generar
    resultado = generar_script_blender(prompt_usuario, modelo)
    script_code = resultado.get("script", "# Error")
    
    # 2. Guardar en Supabase
    asset_id = None
    try:
        nuevo_asset = {
            "prompt": prompt_usuario,
            "script": script_code,
            "status": "PENDIENTE",
            "type": "BLENDER_SCRIPT",
            "created_at": datetime.datetime.now().isoformat()
        }
        res = supabase.table("generated_assets").insert(nuevo_asset).execute()
        if res.data:
            asset_id = res.data[0]['id']
    except Exception as e:
        print(f"Error guardando asset: {e}")

    return jsonify({
        "script": script_code,
        "asset_id": asset_id,
        "status": "PENDIENTE",
        "info": "Script generado y guardado."
    })

if __name__ == "__main__":
    app.run(debug=True, port=5000)
