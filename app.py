import os
from dotenv import load_dotenv
import google.generativeai as genai
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
from neo4j import GraphDatabase

# --- 1. Cargar "secretos" y configurar clientes ---
print("Iniciando API (Versión 14.0 - Fix 'SELECTALL')... Cargando variables.")
load_dotenv()

# --- ¡¡¡AÑADIR ESTAS LÍNEAS FALTANTES!!! ---
# Leemos las variables de entorno (que Render provee) y las guardamos en variables de Python
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
NEO4J_URI = os.getenv('NEO4J_URI')
NEO4J_USERNAME = os.getenv('NEO4J_USERNAME')
NEO4J_PASSWORD = os.getenv('NEO4J_PASSWORD')
NEO4J_DATABASE = os.getenv('NEO4J_DATABASE')

embedding_model = "models/text-embedding-004"
generative_model_name = "models/gemini-2.5-flash"
# -----------------------------------------------

# ... (Se omite el Flujo 1 idéntico) ...
# ==============================================================================
# === FLUJO 1: ASISTENTE DEL MANUAL (RAG + NEO4J) ===
# ==============================================================================
# ... (Todo el Flujo 1 es idéntico, lo omito por brevedad) ...
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
        plan = json.loads(response.text)
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
            fuente = f"{item['capitulo']} (Parte: {item['parte']}, Pág. {item['pagina']})"
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
        return json.loads(respuesta_ia.text)
    except Exception as e:
        print(f"Error en el Redactor (Paso 1.3): {e}")
        return {"respuesta_principal": "Ocurrió un error al generar la respuesta JSON.", "puntos_clave": [], "fuente": ""}

# --- ¡¡¡CORRECCIÓN AQUÍ!!! ---
# Inicializamos la aplicación Flask ANTES de definir las rutas.
app = Flask(__name__)
CORS(app)


# --- ENDPOINT 1: ASISTENTE DEL MANUAL ---
@app.route("/preguntar", methods=["POST"])
def manejar_pregunta():
    
    driver = None
    try:
        print("Manejando petición en /preguntar...")
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD), max_connection_lifetime=60)
        driver.verify_connectivity()
        
        genai.configure(api_key=GOOGLE_API_KEY)
        modelo_gemini = genai.GenerativeModel(generative_model_name)
        
        data = request.json
        if not data or 'pregunta' not in data:
            return jsonify({"error": "No se proporcionó una 'pregunta'"}), 400
        pregunta = data['pregunta']
    
        plan = get_plan_de_busqueda(pregunta, driver, modelo_gemini)
        contexto_items = buscar_en_grafo(driver, plan)
        
        if not contexto_items:
            return jsonify({
                "respuesta_principal": "Lo siento, no pude encontrar información sobre eso en mi base de conocimientos del manual.",
                "puntos_clave": [], "fuente": ""
            })
        
        respuesta_dict = generar_respuesta_final(pregunta, contexto_items, modelo_gemini)
        return jsonify(respuesta_dict)
    
    except Exception as e:
        print(f"--- ¡ERROR FATAL EN /preguntar! ---: {e}")
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
# Esta sección es para el generador de código puro.
# Es 100% INDEPENDIENTE del Flujo 1. No usa Neo4j.

# --- NUEVA FUNCIÓN "EXPERTA EN BPY" (Modo JSON y PURO) ---
def generar_script_blender(prompt_usuario, modelo_gemini):
    """
    Toma un prompt de usuario y devuelve un JSON que contiene
    un script de Python (bpy) válido.
    Formato de salida: {"script": "..."}
    """
    print(f"Paso 2.1: Generador de Script PURO - Creando script para: '{prompt_usuario}'")
    
    # Este prompt es la clave. Es muy estricto y pide SÓLO el script.
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
    {{
      "script": "import bpy\n\n# (Tu código python aquí)\n\n"
    }}
    
    NO incluyas explicaciones.
    NO incluyas advertencias.
    NO incluyas nada fuera de la estructura JSON.
    El campo "script" debe ser un string de Python válido, con saltos de línea \\n.
    """
    
    try:
        # Forzamos la respuesta a ser JSON para evitar errores de sintaxis
        config_json = {"response_mime_type": "application/json"}
        
        response = modelo_gemini.generate_content(
            prompt_generador_bpy,
            generation_config=config_json
        )
        
        # La IA nos da un JSON perfecto, lo cargamos en un dict
        respuesta_dict = json.loads(response.text)
        print("Paso 2.1: Generador de Script PURO - Script generado exitosamente.")
        return respuesta_dict
        
    except Exception as e:
        print(f"Error en Generador de Script (Paso 2.1): {e}")
        return {
            "script": f"# Error al generar el script.\n# {e}"
        }

# --- NUEVO ENDPOINT 2: GENERADOR DE SCRIPT ---
@app.route("/generar-script", methods=["POST"])
def manejar_generacion_script():
    """
    Este endpoint es para la nueva página.
    Recibe: { "pregunta": "Crea un cubo rojo" }
    Devuelve: { "script": "import bpy..." }
    """
    try:
        print("Manejando petición en /generar-script...")
        genai.configure(api_key=GOOGLE_API_KEY)
        modelo_gemini = genai.GenerativeModel(generative_model_name)
        
        data = request.json
        if not data or 'pregunta' not in data:
            return jsonify({"error": "No se proporcionó una 'pregunta'"}), 400
        
        prompt_usuario = data['pregunta']
        
        # Llamamos a nuestra nueva función "experta en bpy"
        respuesta_dict = generar_script_blender(prompt_usuario, modelo_gemini)
        
        # Devolvemos el JSON que nos dio la IA (sólo contiene el script)
        return jsonify(respuesta_dict)

    except Exception as e:
        print(f"--- ¡ERROR FATAL EN /generar-script! ---: {e}")
        return jsonify({
            "script": f"# Error fatal en el servidor: {e}"
        }), 500
    finally:
        print("Manejando petición en /generar-script: Finalizado.")


# --- 5. Arrancar el servidor ---
if __name__ == "__main__":
    # app.run se ejecuta en un solo hilo, manejando ambos endpoints
    app.run(debug=True, port=5000)
