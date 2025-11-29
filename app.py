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
print("--- Iniciando API Proyecto 17 (Modo RAG con Metadatos) ---")
load_dotenv()

# Variables de Entorno (Render Dashboard o .env local)
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

# Validación de seguridad
if not all([GOOGLE_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
    print("⚠️ CRÍTICO: Faltan variables de entorno.")

# Inicializar Clientes
try:
    genai.configure(api_key=GOOGLE_API_KEY)
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Conexión establecida: Supabase + Gemini.")
except Exception as e:
    print(f"❌ Error fatal en inicialización: {e}")

# Configuración de Modelos
# Nota: Usamos 'text-embedding-004' (768 dimensiones) para coincidir con tu base de datos
EMBEDDING_MODEL = "models/text-embedding-004"
GENERATIVE_MODEL = "models/gemini-2.5-flash" 

app = Flask(__name__)
CORS(app)

# --- 2. FUNCIONES AUXILIARES ---

def get_text_embedding(text):
    """Genera vector de 768 dimensiones usando Gemini."""
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
# === FLUJO 1: ASISTENTE DEL MANUAL (RAG CON FILTRADO DE METADATOS) ===
# ==============================================================================

def analizar_intencion_usuario(pregunta_usuario, modelo):
    """
    Paso 1.1: CLASIFICADOR + PLANIFICADOR
    Analiza la pregunta para generar:
    1. Frases de búsqueda (Embeddings).
    2. Un FILTRO de metadatos (para buscar solo en el capítulo correcto).
    """
    # Estos capítulos deben coincidir con los que pusimos en el Frontmatter de los MD
    capitulos_validos = [
        "Introducción", "Historia", "Instalación", 
        "Interfaz de Usuario", "Gestión de Archivos", "Menús Principales"
    ]
    
    prompt = f"""
    Eres el bibliotecario del Manual de Blender.
    Pregunta: "{pregunta_usuario}"
    
    1. Identifica si la pregunta pertenece a uno de estos capítulos exactos: {capitulos_validos}.
    2. Si no estás seguro o es general, deja el filtro vacío.
    3. Genera 2 frases de búsqueda optimizadas para encontrar la respuesta técnica.
    
    Responde SOLO JSON con este formato: 
    {{
        "consultas": ["frase 1", "frase 2"],
        "filtro": {{ "capitulo": "Nombre Exacto" }} 
    }}
    Ejemplo si es general: "filtro": {{}}
    """
    try:
        res = modelo.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        datos = json.loads(res.text)
        return datos.get('consultas', [pregunta_usuario]), datos.get('filtro', {})
    except Exception as e:
        print(f"Error en clasificación: {e}")
        return [pregunta_usuario], {}

def buscar_con_filtro(consultas, filtro_metadata):
    """
    Paso 1.2: Búsqueda Vectorial Inteligente
    Usa la función RPC 'match_secciones' que creamos en SQL.
    Aplica el filtro JSONB para descartar capítulos irrelevantes antes de buscar.
    """
    resultados_unicos = {}
    
    print(f"🔎 Buscando con filtro activo: {filtro_metadata}")

    for consulta in consultas:
        vector = get_text_embedding(consulta)
        if not vector: continue
        
        try:
            # Llamada a la función RPC que creamos en Supabase
            # Esta función usa el índice vectorial Y aplica el filtro JSONB
            response = supabase.rpc('match_secciones', {
                'query_embedding': vector,
                'match_threshold': 0.50, # Umbral (0.0 a 1.0)
                'match_count': 4,        # Número de chunks a recuperar
                'filter': filtro_metadata # ¡Aquí ocurre el filtrado por capítulo!
            }).execute()
            
            if response.data:
                for item in response.data:
                    # Usamos ID para evitar duplicados si varias consultas traen lo mismo
                    resultados_unicos[item['id']] = item
        except Exception as e:
            print(f"❌ Error consultando Supabase: {e}")

    return list(resultados_unicos.values())

def generar_respuesta_rag(pregunta, contexto_items, modelo):
    """
    Paso 1.3: Generación de Respuesta
    Lee los metadatos JSONB recuperados para dar contexto y cita fuentes.
    """
    if not contexto_items:
        return {
            "respuesta_principal": "Lo siento, no encontré información específica en el manual sobre ese tema. Intenta reformular la pregunta.",
            "puntos_clave": [],
            "fuente": "Sin resultados"
        }

    contexto_str = ""
    fuentes_detectadas = set()
    
    for item in contexto_items:
        contenido = item.get('contenido', '')
        # Extraemos datos de la columna JSONB 'metadata'
        meta = item.get('metadata', {})
        
        capitulo = meta.get('capitulo', 'General')
        tema = meta.get('tema', 'Varios')
        prioridad = meta.get('prioridad', 'Normal')
        url = meta.get('url_fuente', '')
        
        # Construimos el bloque de contexto para la IA
        referencia = f"[{capitulo} > {tema}]"
        fuentes_detectadas.add(capitulo)
        
        contexto_str += f"--- FUENTE: {referencia} ---\n{contenido}\n\n"
    
    lista_fuentes = ", ".join(list(fuentes_detectadas))

    prompt = f"""
    Actúa como un experto docente en Blender 3D.
    Responde a la pregunta usando SOLO el siguiente contexto extraído del manual oficial.
    
    CONTEXTO RECUPERADO:
    {contexto_str}
    
    PREGUNTA DEL USUARIO: "{pregunta}"
    
    Instrucciones:
    1. Si el contexto tiene etiquetas de 'Historia', advierte que la info puede ser antigua.
    2. Si el contexto es sobre 'Instalación', menciona si es para Windows, Linux o Mac.
    3. Si hay enlaces útiles en el contexto, úsalos.
    4. Sé claro, conciso y técnico.
    
    Salida JSON:
    {{
        "respuesta_principal": "Texto de la respuesta...",
        "puntos_clave": ["Paso 1...", "Paso 2..."],
        "fuente": "Manual: {lista_fuentes}"
    }}
    """
    try:
        res = modelo.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        return json.loads(res.text)
    except Exception as e:
        print(f"Error generando respuesta final: {e}")
        return {"respuesta_principal": "Error al procesar la respuesta con la IA.", "puntos_clave": [], "fuente": "Error del Sistema"}

# ==============================================================================
# === FLUJO 2: GENERADOR DE SCRIPTS (ASSETS) - (Sin cambios) ===
# ==============================================================================

def generar_script_blender(prompt_usuario, modelo):
    """Genera código bpy para Blender."""
    prompt_bpy = f"""
    Genera script Python (bpy) para Blender 3.6+ para: "{prompt_usuario}"
    REGLAS:
    1. import bpy
    2. Borrar objetos default.
    3. Render Engine: CYCLES, Device: GPU.
    
    Salida JSON: {{ "script": "import bpy..." }}
    """
    try:
        res = modelo.generate_content(prompt_bpy, generation_config={"response_mime_type": "application/json"})
        return json.loads(res.text)
    except:
        return {"script": "# Error generando script"}

# --- ENDPOINTS DE LA API ---

@app.route("/", methods=["GET"])
def health_check():
    return jsonify({"status": "online", "mode": "RAG con Metadatos (Filtrado)"}), 200

@app.route("/preguntar", methods=["POST"])
def endpoint_preguntar():
    data = request.json
    pregunta = data.get('pregunta')
    if not pregunta: return jsonify({"error": "Pregunta vacía"}), 400
    
    modelo = genai.GenerativeModel(GENERATIVE_MODEL)
    
    # 1. Analizar Intención (Obtener querys + filtro de capítulo)
    consultas, filtro = analizar_intencion_usuario(pregunta, modelo)
    
    # 2. Buscar en Supabase (Usando el filtro inteligente)
    contexto = buscar_con_filtro(consultas, filtro)
    
    # 3. Generar Respuesta Final
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
    
    # 2. Guardar en Supabase (Registro de assets generados)
    asset_id = None
    try:
        nuevo_asset = {
            "prompt": prompt_usuario,
            "script": script_code,
            "status": "PENDIENTE",
            "type": "BLENDER_SCRIPT",
            "created_at": datetime.datetime.now().isoformat()
        }
        # Asegúrate de tener la tabla 'generated_assets' si usas esta función
        res = supabase.table("generated_assets").insert(nuevo_asset).execute()
        if res.data:
            asset_id = res.data[0]['id']
    except Exception as e:
        print(f"Nota: No se pudo guardar en 'generated_assets': {e}")

    return jsonify({
        "script": script_code,
        "asset_id": asset_id,
        "status": "PENDIENTE",
        "info": "Script generado correctamente."
    })

if __name__ == "__main__":
    app.run(debug=True, port=5000)

