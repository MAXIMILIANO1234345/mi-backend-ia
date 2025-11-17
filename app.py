import os
from dotenv import load_dotenv
import google.generativeai as genai
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
from neo4j import GraphDatabase
import re
import datetime
import jwt # Se requiere instalar PyJWT (pip install PyJWT)
from supabase import create_client, Client # Se requiere instalar supabase-py (pip install supabase-py)

# --- 1. Cargar "secretos" y configurar clientes ---
print("Iniciando API (Versión 18.0 - Integración Supabase Auth/Logging)... Cargando variables.")
load_dotenv()

# --- Leemos variables de entorno (Corregidas) ---
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
NEO4J_URI = os.getenv('NEO4J_URI')
NEO4J_USERNAME = os.getenv('NEO4J_USERNAME')
NEO4J_PASSWORD = os.getenv('NEO4J_PASSWORD')
NEO4J_DATABASE = os.getenv('NEO4J_DATABASE')

# --- NUEVAS VARIABLES DE ENTORNO (Supabase) ---
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_SERVICE_KEY = os.getenv('SUPABASE_SERVICE_KEY') # Clave de servicio de Admin

embedding_model = "models/text-embedding-004"
generative_model_name = "models/gemini-2.5-flash"

# --- Inicialización de Cliente Supabase ---
try:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        print("ADVERTENCIA: SUPABASE_URL o SUPABASE_SERVICE_KEY no están configuradas. El logging será ignorado.")
        supabase = None
    else:
        # Inicialización del cliente Supabase con la service key (permite bypass RLS para logging seguro)
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        print("Cliente Supabase inicializado para Logging.")
except Exception as e:
    print(f"Error al inicializar cliente Supabase: {e}")
    supabase = None

# ------------------------------------------------------------------------------
# --- NUEVAS FUNCIONES: AUTENTICACIÓN Y LOGGING ---
# ------------------------------------------------------------------------------

def get_auth_user_id(request):
    """Verifica el JWT del header y devuelve el user_id (sub). Retorna None si falla."""
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return None, "Authorization token is missing"

    try:
        token = auth_header.split(' ')[1]
    except IndexError:
        return None, "Invalid Authorization header format"

    try:
        # Usamos la clave de servicio como secreto para verificar el JWT
        decoded_token = jwt.decode(
            token, 
            SUPABASE_SERVICE_KEY, 
            algorithms=["HS256"], 
            audience=["authenticated"] 
        )
        # El ID de usuario está en el campo 'sub' (subject)
        user_id = decoded_token.get('sub')
        return user_id, None
        
    except jwt.ExpiredSignatureError:
        return None, "Token has expired"
    except jwt.InvalidSignatureError:
        return None, "Invalid token signature"
    except Exception as e:
        print(f"Error de Verificación de Token: {e}")
        return None, "Token validation failed"


def log_conversation_to_supabase(user_id, session_id, pregunta, respuesta, model_used):
    """Inserta la conversación en la tabla historial_conversaciones de Supabase."""
    if not supabase:
        print("ERROR: Cliente Supabase no inicializado. No se pudo loggear la conversación.")
        return

    try:
        data = {
            "usuario_id": user_id,
            "sesion_id": session_id,
            "pregunta": pregunta,
            "respuesta": respuesta,
            "modelo_ia_usado": model_used,
        }
        
        # Insertar datos usando la service key (bypassa RLS)
        supabase.table("historial_conversaciones").insert(data).execute()
        print(f"Conversación loggeada exitosamente para user: {user_id}")
             
    except Exception as e:
        print(f"ERROR: No se pudo loggear la conversación en Supabase: {e}")

# ------------------------------------------------------------------------------
# --- FIN DE FUNCIONES AUXILIARES ---
# ------------------------------------------------------------------------------

# --- 2. Funciones de Ayuda (Comunes) ---
def get_text_embedding(text_chunk, task_type="RETRIEVAL_QUERY"):
    """Vectoriza texto."""
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
# === FLUJO 1: ASISTENTE DEL MANUAL (RAG + NEO4J) ===
# ==============================================================================

# PASO 1.1: EL PLANIFICADOR (Modo JSON)
def get_plan_de_busqueda(pregunta_usuario, driver, modelo_gemini):
    print(f"Paso 1.1: Planificador - Creando plan para: '{pregunta_usuario}'")
    lista_capitulos = []
    try:
        with driver.session(database=NEO4J_DATABASE) as session:
            result = session.run("MATCH (c:Capitulo) RETURN c.titulo AS titulo")
            lista_capitulos = [record['titulo'] for record in result]
    except Exception as e:
        print(f"Error obteniendo lista de capítulos: {e}")
    
    prompt_planificador = f"""
    Eres un experto en el manual de Blender. La pregunta de un usuario es: "{pregunta_usuario}"
    El manual tiene los siguientes capítulos: {json.dumps(lista_capitulos)}
    Tu tarea es doble:
    1.  Analiza la pregunta: ¿A cuál de estos capítulos pertenece MÁS PROBABLEMENTE esta pregunta?
    2.  Genera Consultas: Genera 2 consultas de búsqueda optimizadas para encontrar la respuesta.
    Responde SÓLO con la estructura JSON: {{"capitulo_enfocado": "...", "consultas_busqueda": ["..."]}}
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
        return {"capitulo_enfocado": None, "consultas_busqueda": [pregunta_usuario]}

# PASO 1.2: EL INVESTIGADOR (Sin cambios)
def buscar_en_grafo(db_driver, plan):
    consultas = plan.get('consultas_busqueda', [])
    capitulo_enfocado = plan.get('capitulo_enfocado')
    print(f"Paso 1.2: Investigador - Buscando consultas: {consultas} (Enfocado en: {capitulo_enfocado})")
    contexto_encontrado = []
    vectores_de_busqueda = []
    
    for consulta in consultas:
        vec = get_text_embedding(consulta, task_type="RETRIEVAL_QUERY") 
        if vec:
            vectores_de_busqueda.append(vec)

    if not vectores_de_busqueda: return []
    with db_driver.session(database=NEO4J_DATABASE) as session:
        params = {"vectors": vectores_de_busqueda, "capitulo_enfocado": capitulo_enfocado}
        cypher_query = """
            UNWIND $vectors AS vector
            CALL db.index.vector.queryNodes('chunk_vector_index', 3, vector) YIELD node AS item, score
            MATCH (item)-[:PERTENECE_A]->(c:Capitulo)-[:PERTENECE_A]->(p:Parte)
            WITH item, score, c, p,
                 CASE
                   WHEN $capitulo_enfocado IS NULL THEN score
                   WHEN c.titulo = $capitulo_enfocado THEN score + 0.1
                   ELSE score
                 END AS boosted_score
            RETURN 'Texto' AS tipo, item.texto AS contenido, item.pagina AS pagina, 
                   c.titulo AS capitulo, p.titulo AS parte, boosted_score AS score
            ORDER BY score DESC LIMIT 3
        """
        result = session.run(cypher_query, params)
        contexto_encontrado = [dict(record) for record in result]
    contexto_limpio = []
    ids_vistos = set()
    for item in contexto_encontrado:
        item_id = item['contenido']
        if item_id not in ids_vistos:
            contexto_limpio.append(item)
            ids_vistos.add(item_id)
    print(f"Paso 1.2: Investigador - Búsqueda completada. {len(contexto_limpio)} items únicos encontrados.")
    return contexto_limpio


# PASO 1.3: EL REDACTOR (Modo JSON)
def generar_respuesta_final(pregunta_usuario, contexto_items, modelo_gemini):
    print("Paso 1.3: Redactor - Generando respuesta JSON estructurada...")
    contexto_para_ia = ""
    fuente_unica = ""
    if contexto_items:
        fuentes_encontradas = set()
        for item in contexto_items:
            fuente = f"{item['capitulo']} (Parte: {item['parte']}, Pág {item['pagina']})"
            fuentes_encontradas.add(fuente)
            contexto_para_ia += f"--- Contexto de TEXTO de '{fuente}' ---\n{item['contenido']}\n\n"
        if fuentes_encontradas:
            fuente_unica = ", ".join(fuentes_encontradas)
    
    prompt_para_ia = f"""
    Eres un asistente experto del manual de Blender. Basándote ESTRICTAMENTE en el 'Contexto' provisto, responde la 'Pregunta'.
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


# --- Inicializamos la aplicación Flask ---
app = Flask(__name__)
CORS(app)


# --- ENDPOINT 1: ASISTENTE DEL MANUAL ---
@app.route("/preguntar", methods=["POST"])
def manejar_pregunta():
    
    # 1. AUTENTICACIÓN Y EXTRACCIÓN DE DATOS DE USUARIO
    user_id, auth_error = get_auth_user_id(request)
    if auth_error:
        return jsonify({"error": f"Acceso denegado: {auth_error}"}), 401
    
    driver = None
    respuesta_dict = {}
    pregunta = ""
    session_id = None
    
    try:
        data = request.json
        if not data or 'pregunta' not in data or 'session_id' not in data:
            return jsonify({"error": "Faltan datos requeridos (pregunta, session_id)"}), 400
            
        pregunta = data['pregunta']
        session_id = data['session_id']

        print(f"Manejando petición en /preguntar para user: {user_id}...")
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD), max_connection_lifetime=60)
        driver.verify_connectivity()
        
        genai.configure(api_key=GOOGLE_API_KEY)
        modelo_gemini = genai.GenerativeModel(generative_model_name)
        
        plan = get_plan_de_busqueda(pregunta, driver, modelo_gemini)
        contexto_items = buscar_en_grafo(driver, plan)
        
        if not contexto_items:
            respuesta_dict = {
                "respuesta_principal": "Lo siento, no pude encontrar información sobre eso en mi base de conocimientos del manual.",
                "puntos_clave": [], "fuente": ""
            }
        else:
            respuesta_dict = generar_respuesta_final(pregunta, contexto_items, modelo_gemini)
            
        # 2. LOGGING DE CONVERSACIÓN (¡NUEVO!)
        log_conversation_to_supabase(
            user_id=user_id, 
            session_id=session_id, 
            pregunta=pregunta, 
            respuesta=json.dumps(respuesta_dict),
            model_used=generative_model_name
        )
        
        return jsonify(respuesta_dict)
    
    except Exception as e:
        print(f"--- ¡ERROR FATAL EN /preguntar! ---: {e}")
        # Si falla el proceso RAG, aún intentamos loggear el error
        if pregunta and user_id and session_id:
             log_conversation_to_supabase(
                user_id=user_id, 
                session_id=session_id, 
                pregunta=pregunta, 
                respuesta=f"ERROR INTERNO: {e}",
                model_used="FALLO_RAG"
            )
        return jsonify({
            "respuesta_principal": "Ocurrió un error interno mayor al procesar tu solicitud.",
            "puntos_clave": [], "fuente": ""
        }), 500
    finally:
        if driver:
            driver.close()
            print("Manejando petición en /preguntar: Conexión a Neo4j cerrada.")


# ==============================================================================
# === FLUJO 2: GENERADOR DE CÓDIGO BLENDER (bpy) ===
# ==============================================================================

# --- NUEVA FUNCIÓN "EXPERTA EN BPY" (Modo JSON y PURO) ---
def generar_script_blender(prompt_usuario, modelo_gemini):
    """
    Toma un prompt de usuario y devuelve un JSON que contiene
    un script de Python (bpy) válido.
    Formato de salida: {"script": "..."}
    """
    print(f"Paso 2.1: Generador de Script PURO - Creando script para: '{prompt_usuario}'")
    
    prompt_generador_bpy = f"""
    Eres un asistente experto en el API de Python (bpy) de Blender (versión 3.4 en adelante).
    La solicitud del usuario es: "{prompt_usuario}"

    Tu tarea es generar un script de Python (bpy) que cumpla con la solicitud.
    Sigue estas reglas OBLIGATORIAMENTE:
    1.  Importa `bpy`.
    2.  Limpia SIEMPRE la escena por defecto al inicio (cubo, luz, cámara).
    3.  Usa métodos modernos (versión 3.4+). Por ejemplo, prefiere manipular datos directamente (ej. `obj.location`, `obj.select_set(True)`) en lugar de operadores de contexto (`bpy.ops.transform.translate()`, `bpy.ops.object.select_all()`), a menos que sea estrictamente necesario.
    4.  Crea los objetos, materiales, luces y cámara necesarios.
    5.  Coloca la cámara en una posición razonable para ver los objetos creados.
    6.  Configura el motor de render a 'CYCLES' y 'GPU' si es posible.
    7.  Configura la ruta de salida a '/tmp/render_result.png'.
    8.  NO uses el parámetro `use_confirm=True`. Está obsoleto.
    9.  REGLA DE SELECCIÓN: Si usas `bpy.ops.object.select_all()`, el parámetro es `action='SELECT'` o `action='DESELECT'`. El enum 'SELECTALL' es incorrecto y obsoleto.

    Responde SÓLO con un JSON que tenga esta estructura ÚNICA:
    {{"script": "import bpy\\n\\n# (Tu código python aquí)\\n\\n"}}
    
    NO incluyas explicaciones.
    NO incluyas advertencias.
    NO incluyas nada fuera de la estructura JSON.
    El campo "script" debe ser un string de Python válido, con saltos de línea \\n.
    """
    
    try:
        config_json = {"response_mime_type": "application/json"}
        
        response = modelo_gemini.generate_content(
            prompt_generador_bpy,
            generation_config=config_json
        )
        
        raw_text = response.text
        clean_json_text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', raw_text)
        respuesta_dict = json.loads(clean_json_text)
        
        print("Paso 2.1: Generador de Script PURO - Script generado exitosamente.")
        return respuesta_dict
        
    except Exception as e:
        print(f"Error en Generador de Script (Paso 2.1): {e}")
        error_message = re.sub(r'[\x00-\x1F\x7F]', '', str(e))
        return {
            "script": f"# Error al generar el script.\n# {error_message}"
        }

# --- NUEVO ENDPOINT 2: GENERADOR DE SCRIPT ---
@app.route("/generar-script", methods=["POST"])
def manejar_generacion_script():
    
    # 1. AUTENTICACIÓN Y EXTRACCIÓN DE DATOS DE USUARIO
    user_id, auth_error = get_auth_user_id(request)
    if auth_error:
        return jsonify({"error": f"Acceso denegado: {auth_error}"}), 401
    
    driver = None
    script_code = ""
    prompt_usuario = ""
    session_id = None
    
    try:
        data = request.json
        if not data or 'pregunta' not in data or 'session_id' not in data:
            return jsonify({"error": "Faltan datos requeridos (pregunta, session_id)"}), 400
            
        prompt_usuario = data['pregunta']
        session_id = data['session_id']

        print("Manejando petición en /generar-script (Fase 2: Guardado de Asset)...")
        # Conexión a AI
        genai.configure(api_key=GOOGLE_API_KEY)
        modelo_gemini = genai.GenerativeModel(generative_model_name)
        
        # 1. Generar el script
        script_dict = generar_script_blender(prompt_usuario, modelo_gemini)
        script_code = script_dict.get("script", "# No se generó código.")
        
        # Si la IA falló, devolvemos el error inmediatamente
        if "# Error al generar el script" in script_code:
            # 2a. LOGGING DE FALLO
            log_conversation_to_supabase(user_id, session_id, prompt_usuario, script_code, "FALLO_BPY_GEN")
            return jsonify(script_dict), 500

        # 2b. Conexión a Neo4j 
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD), max_connection_lifetime=60)
        driver.verify_connectivity()
        
        # 3. Guardar el Asset PENDIENTE en Neo4j
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        
        query = """
        CREATE (a:Asset:GeneratedAsset {
            prompt: $prompt,
            script: $script,
            status: 'PENDIENTE',
            createdAt: $timestamp,
            type: 'BLENDER_SCRIPT'
        })
        RETURN ID(a) AS asset_id
        """
        
        with driver.session(database=NEO4J_DATABASE) as session:
            result = session.run(query, 
                prompt=prompt_usuario, 
                script=script_code, 
                timestamp=timestamp
            )
            asset_id = result.single()["asset_id"]
            print(f"Asset PENDIENTE creado en Neo4j. ID: {asset_id}")

        # 4. LOGGING DE CONVERSACIÓN (¡NUEVO!)
        log_conversation_to_supabase(
            user_id=user_id, 
            session_id=session_id, 
            pregunta=prompt_usuario, 
            respuesta=script_code,
            model_used=generative_model_name
        )

        # 5. Devolver la respuesta al cliente con el ID de rastreo
        return jsonify({
            "script": script_code,
            "asset_id": asset_id,
            "status": "PENDIENTE",
            "message": "Script generado y guardado. Esperando ejecución por el módulo Worker."
        })

    except Exception as e:
        print(f"--- ¡ERROR FATAL EN /generar-script! ---: {e}")
        # Intentamos loggear el error del servidor
        log_conversation_to_supabase(user_id, session_id, prompt_usuario, f"ERROR CRÍTICO SERVER: {e}", "FALLO_SERVER")

        return jsonify({
            "script": f"# Error fatal en el servidor: {e}",
            "asset_id": None,
            "status": "FALLO"
        }), 500
    finally:
        if driver:
            driver.close()
            print("Manejando petición en /generar-script: Conexión a Neo4j cerrada.")


# --- 5. Arrancar el servidor ---
if __name__ == "__main__":
    app.run(debug=True, port=5000)
