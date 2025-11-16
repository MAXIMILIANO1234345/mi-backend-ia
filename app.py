import os
from dotenv import load_dotenv
import google.generativeai as genai
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
from neo4j import GraphDatabase

# --- 1. Cargar "secretos" y configurar clientes ---
print("Iniciando API (Versión Grafo 10.0 - JSON ESTRUCTURADO)... Cargando variables.")
load_dotenv()

GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
NEO4J_URI = os.getenv('NEO4J_URI')
NEO4J_USERNAME = os.getenv('NEO4J_USERNAME')
NEO4J_PASSWORD = os.getenv('NEO4J_PASSWORD')
NEO4J_DATABASE = os.getenv('NEO4J_DATABASE')

embedding_model = "models/text-embedding-004"
generative_model_name = "models/gemini-2.5-flash"

# --- 2. Funciones de Ayuda (Sin cambios) ---

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

# --- 3. LÓGICA DE IA DE MÚLTIPLES PASOS ---

# PASO 1: EL PLANIFICADOR (Sin cambios)
def get_plan_de_busqueda(pregunta_usuario, driver, modelo_gemini):
    print(f"Paso 1: Planificador - Creando plan para: '{pregunta_usuario}'")
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
    1.  **Analiza la pregunta:** ¿A cuál de estos capítulos pertenece MÁS PROBABLEMENTE esta pregunta? Elige solo el MEJOR capítulo.
    2.  **Genera Consultas:** Genera 2 consultas de búsqueda optimizadas para encontrar la respuesta *dentro* de ese capítulo.
    Responde SÓLO con un JSON.
    Ejemplo: {{"capitulo_enfocado": "2.2. Herramientas Clave de Modelado Poligonal", "consultas_busqueda": ["herramientas de modelado poligonal", "cómo extruir y biselar en Blender"]}}
    """
    try:
        response = modelo_gemini.generate_content(prompt_planificador)
        json_text = response.text.replace("```json", "").replace("```", "").strip()
        plan = json.loads(json_text)
        print(f"Paso 1: Planificador - Plan obtenido: {plan}")
        return plan
    except Exception as e:
        print(f"Error en el Planificador (Paso 1): {e}.")
        return {"capitulo_enfocado": None, "consultas_busqueda": [pregunta_usuario]}

# PASO 2: EL INVESTIGADOR (Sin cambios)
def buscar_en_grafo(db_driver, plan):
    consultas = plan.get('consultas_busqueda', [])
    capitulo_enfocado = plan.get('capitulo_enfocado')
    print(f"Paso 2: Investigador - Buscando consultas: {consultas} (Enfocado en: {capitulo_enfocado})")
    contexto_encontrado = []
    vectores_de_busqueda = []
    for consulta in consultas:
        vec = get_text_embedding(consulta, task_type="RETRIEVAL_QUERY") 
        if vec:
            vectores_de_busqueda.append(vec)
    if not vectores_de_busqueda:
        print("Error: No se pudieron generar vectores de búsqueda.")
        return []
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
            ORDER BY score DESC
            LIMIT 3
        """
        result = session.run(cypher_query, params)
        for record in result:
            contexto_encontrado.append(dict(record))
    contexto_limpio = []
    ids_vistos = set()
    for item in contexto_encontrado:
        item_id = item['contenido']
        if item_id not in ids_vistos:
            contexto_limpio.append(item)
            ids_vistos.add(item_id)
    print(f"Paso 2: Investigador - Búsqueda completada. {len(contexto_limpio)} items únicos encontrados.")
    return contexto_limpio

# --- CAMBIO 1: PASO 3, EL REDACTOR AHORA GENERA JSON ---
def generar_respuesta_final(pregunta_usuario, contexto_items, modelo_gemini):
    print("Paso 3: Redactor - Generando respuesta JSON estructurada...")
    
    contexto_para_ia = ""
    fuentes_encontradas = set()
    fuente_unica = ""

    if contexto_items:
        for item in contexto_items:
            fuente = f"{item['capitulo']} (Parte: {item['parte']}, Pág. {item['pagina']})"
            fuentes_encontradas.add(fuente)
            contexto_para_ia += f"--- Contexto de TEXTO de '{fuente}' ---\n"
            contexto_para_ia += f"{item['contenido']}\n\n"
        
        # Simplificamos la fuente para el JSON
        if fuentes_encontradas:
            fuente_unica = ", ".join(fuentes_encontradas)
    
    # --- ¡NUEVO PROMPT! ---
    # Ahora le pedimos a la IA que devuelva un JSON.
    # También le damos un ejemplo de cómo manejar listas (ej. herramientas de modelado)
    prompt_para_ia = f"""
    Eres un asistente experto del manual de Blender. Tu ÚNICA tarea es responder la pregunta del usuario basándote ESTRICTA Y EXCLUSIVAMENTE en el 'Contexto' provisto.

    Sigue estas reglas AL PIE DE LA LETRA:
    1.  Revisa el 'Contexto'.
    2.  Responde SÓLO con un JSON que tenga esta estructura:
        {{
          "respuesta_principal": "Una respuesta en párrafo que resuma el concepto. Si la pregunta es sobre una lista de cosas (como herramientas), este párrafo será la introducción.",
          "puntos_clave": [
            {{
              "titulo": "Título del punto clave 1 (ej. 'Extrusión (E)')",
              "descripcion": "Descripción del punto clave 1..."
            }},
            {{
              "titulo": "Título del punto clave 2 (ej. 'Insertar Caras (Inset, I)')",
              "descripcion": "Descripción del punto clave 2..."
            }}
          ],
          "fuente": "Cita la fuente aquí (ej. '{fuente_unica}')"
        }}
    
    3.  **Si la pregunta NO es sobre una lista** (ej. "qué es Blender"), pon la respuesta completa en "respuesta_principal" y deja "puntos_clave" como un array vacío [].
    4.  **REGLA CRÍTICA:** Si la respuesta NO se encuentra en el 'Contexto', DEBES responder EXACTAMENTE con este JSON:
        {{
          "respuesta_principal": "Lo siento, encontré información relacionada, pero no pude hallar una respuesta específica a tu pregunta en el manual.",
          "puntos_clave": [],
          "fuente": ""
        }}
    5.  NO uses ningún conocimiento externo.

    Contexto:
    {contexto_para_ia}
    
    Pregunta:
    {pregunta_usuario}
    
    Respuesta JSON:
    """
    
    print(f"Paso 3: Redactor - Enviando prompt JSON a {generative_model_name}...")
    try:
        respuesta_ia = modelo_gemini.generate_content(prompt_para_ia)
        
        # Limpiamos la respuesta para asegurarnos de que es un JSON válido
        json_text = respuesta_ia.text.replace("```json", "").replace("```", "").strip()
        
        # Devolvemos el OBJETO de Python (dict), no el string
        return json.loads(json_text)
        
    except Exception as e:
        print(f"Error en el Redactor (Paso 3) o al parsear JSON: {e}")
        if "429" in str(e):
            return {
                "respuesta_principal": "El motor de IA está procesando demasiadas solicitudes. Por favor, espera un minuto e inténtalo de nuevo.",
                "puntos_clave": [],
                "fuente": ""
            }
        # Devolvemos un JSON de error si falla
        return {
            "respuesta_principal": "Ocurrió un error al generar la respuesta o al procesar el JSON.",
            "puntos_clave": [],
            "fuente": ""
        }

# --- 4. El "Endpoint" de Flask ---

app = Flask(__name__)
CORS(app)

@app.route("/preguntar", methods=["POST"])
def manejar_pregunta():
    
    driver = None
    
    try:
        # 1. Conectar a Neo4j
        print("Manejando petición: Conectando a Neo4j...")
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD), max_connection_lifetime=60)
        driver.verify_connectivity()
        print("Conexión a Neo4j establecida.")
        
        # 2. Conectar a IA
        print("Manejando petición: Conectando a Google AI...")
        genai.configure(api_key=GOOGLE_API_KEY)
        modelo_gemini = genai.GenerativeModel(generative_model_name)
        print("Conexión a Google AI establecida.")
        
        # 3. Procesar la pregunta
        data = request.json
        if not data or 'pregunta' not in data:
            return jsonify({"error": "No se proporcionó una 'pregunta' en el JSON"}), 400

        pregunta = data['pregunta']
    
        # 4. Ejecutar el plan de 3 pasos
        plan = get_plan_de_busqueda(pregunta, driver, modelo_gemini)
        contexto_items = buscar_en_grafo(driver, plan)
        
        if not contexto_items:
            print("No se encontró contexto en el grafo. Respondiendo directamente.")
            return jsonify({
                "respuesta_principal": "Lo siento, no pude encontrar información sobre eso en mi base de conocimientos del manual.",
                "puntos_clave": [],
                "fuente": ""
            })
        
        # PASO 3: Redactar (ahora devuelve un 'dict')
        respuesta_dict = generar_respuesta_final(pregunta, contexto_items, modelo_gemini)
        
        # --- CAMBIO 2: Enviar el dict directamente ---
        # No lo envolvemos en {"respuesta": ...}
        # jsonify() convertirá el 'dict' de Python en una respuesta JSON real
        return jsonify(respuesta_dict)
    
    except Exception as e:
        print(f"--- ¡ERROR FATAL EN /preguntar! ---")
        print(f"Ocurrió un error: {e}")
        return jsonify({
            "respuesta_principal": "Ocurrió un error interno mayor al procesar tu solicitud.",
            "puntos_clave": [],
            "fuente": ""
        }), 500
    
    finally:
        if driver:
            driver.close()
            print("Manejando petición: Conexión a Neo4j cerrada.")

# --- 5. Arrancar el servidor ---
if __name__ == "__main__":
    app.run(debug=True, port=5000
