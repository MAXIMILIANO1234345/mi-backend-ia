import os
import json
import re
import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import google.generativeai as genai
from supabase import create_client, Client

# --- 1. CONFIGURACIÓN E INICIALIZACIÓN ---
print("Iniciando API (Versión 20.0 - Render + Supabase)... Cargando variables.")
load_dotenv()

# --- Leemos variables de entorno ---
# En Render, estas se configuran en el Dashboard "Environment Variables"
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

# Modelos
embedding_model = "models/text-embedding-004"
generative_model_name = "models/gemini-2.5-flash"

# --- Inicializar Clientes ---
try:
    genai.configure(api_key=GOOGLE_API_KEY)
    # Cliente de Supabase
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Conexión a Supabase y Gemini inicializada correctamente.")
except Exception as e:
    print(f"❌ Error fatal iniciando clientes: {e}")

# --- 2. Funciones de Ayuda (Comunes) ---
def get_text_embedding(text_chunk, task_type="RETRIEVAL_QUERY"):
    """Vectoriza texto usando Gemini."""
    try:
        result = genai.embed_content(
            model=embedding_model,
            content=text_chunk,
            task_type=task_type
        )
        return result['embedding']
    except Exception as e:
        print(f"Error al vectorizar texto (tipo: {task_type}): {e}")
        return None

# ==============================================================================
# === FLUJO 1: ASISTENTE DEL MANUAL (RAG + SUPABASE) ===
# ==============================================================================

# PASO 1.1: EL PLANIFICADOR (Modo JSON)
def get_plan_de_busqueda(pregunta_usuario, modelo_gemini):
    print(f"Paso 1.1: Planificador - Creando plan para: '{pregunta_usuario}'")
    
    # Nota: Ya no consultamos la lista de capítulos a la BD para ahorrar tiempo.
    # Dejamos que el LLM infiera el contexto.
    
    prompt_planificador = f"""
    Eres un experto en el manual de Blender (Proyecto 17). La pregunta de un usuario es: "{pregunta_usuario}"
    
    Tu tarea es doble:
    1.  Analiza la pregunta: ¿A qué tema o capítulo pertenece probablemente?
    2.  Genera Consultas: Genera 2 consultas de búsqueda optimizadas para encontrar la respuesta en la base vectorial.
    
    Responde SÓLO con la estructura JSON: {{"capitulo_enfocado": "Tema probable", "consultas_busqueda": ["consulta 1", "consulta 2"]}}
    """
    try:
        config_json = {"response_mime_type": "application/json"}
        response = modelo_gemini.generate_content(
            prompt_planificador, 
            generation_config=config_json
        )
        clean_json_text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', response.text)
        plan = json.loads(clean_json_text)
        print(f"Paso 1.1: Planificador - Plan obtenido: {plan}")
        return plan
    except Exception as e:
        print(f"Error en el Planificador (Paso 1.1): {e}.")
        return {"capitulo_enfocado": "General", "consultas_busqueda": [pregunta_usuario]}

# PASO 1.2: EL INVESTIGADOR (Supabase RPC)
def buscar_en_supabase(plan):
    consultas = plan.get('consultas_busqueda', [])
    capitulo_enfocado = plan.get('capitulo_enfocado')
    print(f"Paso 1.2: Investigador - Buscando consultas: {consultas}")
    
    resultados_unicos = {} # Diccionario para deduplicar por ID
    
    for consulta in consultas:
        vector = get_text_embedding(consulta, task_type="RETRIEVAL_QUERY") 
        if not vector: continue

        try:
            # Llamada a la función remota (RPC) en Supabase Postgres
            # Esta función 'match_manual' debe existir en tu base de datos SQL
            response = supabase.rpc('match_manual', {
                'query_embedding': vector,
                'match_threshold': 0.5, # Ajustar umbral de similitud (0.0 a 1.0)
                'match_count': 3
            }).execute()
            
            # Procesar resultados
            if response.data:
                for item in response.data:
                    # Usamos el ID como clave para evitar duplicados si las 2 consultas traen lo mismo
                    item_id = item.get('id')
                    if item_id not in resultados_unicos:
                        resultados_unicos[item_id] = item
                        
        except Exception as e:
            print(f"Error buscando en Supabase: {e}")

    # Convertir diccionario a lista
    contexto_limpio = list(resultados_unicos.values())
    print(f"Paso 1.2: Investigador - Búsqueda completada. {len(contexto_limpio)} items únicos.")
    return contexto_limpio

# PASO 1.3: EL REDACTOR (Modo JSON)
def generar_respuesta_final(pregunta_usuario, contexto_items, modelo_gemini):
    print("Paso 1.3: Redactor - Generando respuesta JSON estructurada...")
    contexto_para_ia = ""
    fuente_unica = ""
    
    if contexto_items:
        fuentes_encontradas = set()
        for item in contexto_items:
            # Extraer metadata del JSONB de Supabase
            meta = item.get('metadata', {})
            contenido = item.get('content', '')
            
            cap = meta.get('capitulo', 'Sección')
            pag = meta.get('pagina', '?')
            
            fuente = f"{cap} (Pág {pag})"
            fuentes_encontradas.add(fuente)
            contexto_para_ia += f"--- Contexto de TEXTO de '{fuente}' ---\n{contenido}\n\n"
            
        if fuentes_encontradas:
            fuente_unica = ", ".join(fuentes_encontradas)
    
    prompt_para_ia = f"""
    Eres un asistente experto del manual de Blender (Proyecto 17). Basándote ESTRICTAMENTE en el 'Contexto' provisto, responde la 'Pregunta'.
    Debes responder SÓLO con un JSON con esta estructura:
    {{
      "respuesta_principal": "Un resumen en párrafo.",
      "puntos_clave": [
        {{"titulo": "Título punto 1", "descripcion": "Descripción..."}}
      ],
      "fuente": "Cita la fuente aquí (ej. '{fuente_unica}')"
    }}
    Si la respuesta NO se encuentra en el 'Contexto', responde con:
    {{
      "respuesta_principal": "Lo siento, encontré información relacionada, pero no pude hallar una respuesta específica a tu pregunta en el manual.",
      "puntos_clave": [],
      "fuente": ""
    }}
    Contexto: {contexto_para_ia}
    Pregunta: {pregunta_usuario}
    Respuesta JSON:
    """
    try:
        config_json = {"response_mime_type": "application/json"}
        respuesta_ia = modelo_gemini.generate_content(prompt_para_ia, generation_config=config_json)
        
        raw_text = respuesta_ia.text
        clean_json_text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', raw_text)
        return json.loads(clean_json_text)

    except Exception as e:
        print(f"Error en el Redactor (Paso 1.3): {e}")
        return {"respuesta_principal": "Ocurrió un error al generar la respuesta JSON.", "puntos_clave": [], "fuente": ""}

# --- Inicialización de Flask ---
app = Flask(__name__)
CORS(app)

# --- ENDPOINT 1: ASISTENTE DEL MANUAL ---
@app.route("/preguntar", methods=["POST"])
def manejar_pregunta():
    try:
        print("Manejando petición en /preguntar...")
        
        data = request.json
        if not data or 'pregunta' not in data:
            return jsonify({"error": "No se proporcionó una 'pregunta'"}), 400
        pregunta = data['pregunta']
    
        modelo_gemini = genai.GenerativeModel(generative_model_name)

        # Ejecutar flujo RAG (Ahora con Supabase)
        plan = get_plan_de_busqueda(pregunta, modelo_gemini)
        contexto_items = buscar_en_supabase(plan)
        
        # Generar respuesta
        respuesta_dict = generar_respuesta_final(pregunta, contexto_items, modelo_gemini)
        return jsonify(respuesta_dict)
    
    except Exception as e:
        print(f"--- ¡ERROR FATAL EN /preguntar! ---: {e}")
        return jsonify({
            "respuesta_principal": "Ocurrió un error interno mayor al procesar tu solicitud.",
            "puntos_clave": [], "fuente": ""
        }), 500

# ==============================================================================
# === FLUJO 2: GENERADOR DE CÓDIGO BLENDER (bpy) ===
# ==============================================================================

# PASO 2.1: GENERADOR PURO
def generar_script_blender(prompt_usuario, modelo_gemini):
    print(f"Paso 2.1: Generador de Script - Creando script para: '{prompt_usuario}'")
    
    prompt_generador_bpy = f"""
    Eres un asistente experto en el API de Python (bpy) de Blender (versión 3.4+).
    Solicitud: "{prompt_usuario}"

    REGLAS OBLIGATORIAS:
    1. Importa `bpy`.
    2. Limpia la escena por defecto (cubo, luz, cámara).
    3. Usa `bpy.context.collection.objects.link(obj)` (NO scene.objects.link).
    4. Usa `obj.select_set(True)`.
    5. Configura render a 'CYCLES' y dispositivo 'GPU'.
    6. Configura salida a '/tmp/render_result.png'.
    
    Responde SÓLO JSON:
    {{
      "script": "import bpy\\n\\n# Código..."
    }}
    """
    try:
        config_json = {"response_mime_type": "application/json"}
        response = modelo_gemini.generate_content(
            prompt_generador_bpy,
            generation_config=config_json
        )
        clean_json_text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', response.text)
        return json.loads(clean_json_text)
        
    except Exception as e:
        print(f"Error en Generador de Script: {e}")
        return {"script": f"# Error al generar el script: {e}"}

# --- ENDPOINT 2: GENERADOR DE SCRIPT ---
@app.route("/generar-script", methods=["POST"])
def manejar_generacion_script():
    try:
        print("Manejando petición en /generar-script...")
        
        data = request.json
        if not data or 'pregunta' not in data:
            return jsonify({"error": "No se proporcionó una 'pregunta'"}), 400
        
        prompt_usuario = data['pregunta']
        modelo_gemini = genai.GenerativeModel(generative_model_name)
        
        # 1. Generar el script
        script_dict = generar_script_blender(prompt_usuario, modelo_gemini)
        script_code = script_dict.get("script", "# No se generó código.")
        
        # 2. Guardar en Supabase (Tabla 'generated_assets')
        # Nota: 'generated_assets' debe existir en Supabase
        
        nuevo_asset = {
            "prompt": prompt_usuario,
            "script": script_code,
            "status": "PENDIENTE",
            "type": "BLENDER_SCRIPT",
            "created_at": datetime.datetime.now().isoformat()
        }
        
        print("Guardando asset en Supabase...")
        res_db = supabase.table("generated_assets").insert(nuevo_asset).execute()
        
        # Recuperar ID del asset insertado
        asset_id = None
        if res_db.data and len(res_db.data) > 0:
            asset_id = res_db.data[0]['id']
            print(f"Asset guardado correctamente. ID: {asset_id}")

        return jsonify({
            "script": script_code,
            "asset_id": asset_id,
            "status": "PENDIENTE",
            "message": "Script generado y guardado en la nube."
        })

    except Exception as e:
        print(f"--- ¡ERROR FATAL EN /generar-script! ---: {e}")
        return jsonify({
            "script": f"# Error fatal en el servidor: {e}",
            "asset_id": None,
            "status": "FALLO"
        }), 500

# --- ARRANQUE ---
if __name__ == "__main__":
    # En local usamos debug=True. En Render, Gunicorn se encarga del run.
    app.run(debug=True, port=5000)
