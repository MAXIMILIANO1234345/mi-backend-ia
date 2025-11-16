import os
from dotenv import load_dotenv
import google.generativeai as genai
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
from neo4j import GraphDatabase

# --- 1. Cargar "secretos" y configurar clientes ---
print("Iniciando API (Versión Grafo 9.0 - ENFOQUE CON BOOST)... Cargando variables.")
load_dotenv()

GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
NEO4J_URI = os.getenv('NEO4J_URI')
NEO4J_USERNAME = os.getenv('NEO4J_USERNAME')
NEO4J_PASSWORD = os.getenv('NEO4J_PASSWORD')
NEO4J_DATABASE = os.getenv('NEO4J_DATABASE')

embedding_model = "models/text-embedding-004"
generative_model_name = "models/gemini-2.5-flash"

# --- 2. Funciones de Ayuda ---

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

# PASO 1: EL PLANIFICADOR (Sin cambios, está bien)
def get_plan_de_busqueda(pregunta_usuario, driver, modelo_gemini):
    """
    Paso 1: La IA "piensa" qué capítulos son relevantes.
    """
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
    
    El manual tiene los siguientes capítulos:
    {json.dumps(lista_capitulos)}

    Tu tarea es doble:
    1.  **Analiza la pregunta:** ¿A cuál de estos capítulos pertenece MÁS PROBABLEMENTE esta pregunta? Elige solo el MEJOR capítulo. Si la pregunta es muy general (ej. "qué es Blender"), elige un capítulo de introducción.
    2.  **Genera Consultas:** Genera 2 consultas de búsqueda optimizadas para encontrar la respuesta *dentro* de ese capítulo.

    Responde SÓLO con un JSON.
    Ejemplo para "como modelar":
    {{"capitulo_enfocado": "2.2. Herramientas Clave de Modelado Poligonal", "consultas_busqueda": ["herramientas de modelado poligonal", "cómo extruir y biselar en Blender"]}}
    
    Ejemplo para "qué es una colección":
    {{"capitulo_enfocado": "1.4. Gestión de Escena y Colecciones", "consultas_busqueda": ["qué es una colección en Blender", "propiedades de las colecciones"]}}
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

# PASO 2: EL INVESTIGADOR (¡CORREGIDO!)
def buscar_en_grafo(db_driver, plan):
    """
    Busca en Neo4j, usando el capítulo enfocado y las consultas del plan.
    ¡USA "BOOST" EN LUGAR DE "FILTER"!
    """
    consultas = plan.get('consultas_busqueda', [])
    capitulo_enfocado = plan.get('capitulo_enfocado')
    
    print(f"Paso 2: Investigador - Buscando consultas: {consultas} (Enfocado en: {capitulo_enfocado})")
    
    contexto_encontrado = []
    
    # 1. Vectorizamos las consultas generadas por la IA
    vectores_de_busqueda = []
    for consulta in consultas:
        vec = get_text_embedding(consulta, task_type="RETRIEVAL_QUERY") 
        if vec:
            vectores_de_busqueda.append(vec)
    
    if not vectores_de_busqueda:
        print("Error: No se pudieron generar vectores de búsqueda.")
        return []

    
    with db_driver.session(database=NEO4J_DATABASE) as session:
        
        params = {
            "vectors": vectores_de_busqueda,
            "capitulo_enfocado": capitulo_enfocado
        }
        
        # --- ¡LA CONSULTA "ANTI-CAOS" V2 (CON BOOST) ---
        # 1. Desenrolla (UNWIND) nuestra lista de vectores de búsqueda.
        # 2. Por CADA vector, busca los 3 chunks más parecidos (CALL db.index.vector.queryNodes).
        # 3. CONECTA (MATCH) esos chunks a sus capítulos.
        # 4. CREA "boosted_score":
        #    - Si el capítulo coincide con el plan, recibe un bonus (score + 0.1).
        #    - Si no coincide, o no hay plan, se queda con su score original.
        # 5. Devuelve los 3 mejores resultados únicos, ordenados por "boosted_score".
        
        cypher_query = """
            UNWIND $vectors AS vector
            CALL db.index.vector.queryNodes('chunk_vector_index', 3, vector) YIELD node AS item, score
            
            MATCH (item)-[:PERTENECE_A]->(c:Capitulo)-[:PERTENECE_A]->(p:Parte)
            
            // --- LA LÓGICA DE "BOOST" (ANTI-CAOS V2) ---
            // En lugar de filtrar, damos un "bonus" a los chunks que SÍ coinciden con el plan.
            WITH item, score, c, p,
                 CASE
                   WHEN $capitulo_enfocado IS NULL THEN score
                   WHEN c.titulo = $capitulo_enfocado THEN score + 0.1  // ¡Bonus!
                   ELSE score
                 END AS boosted_score

            RETURN 'Texto' AS tipo, 
                   item.texto AS contenido, 
                   item.pagina AS pagina, 
                   c.titulo AS capitulo, 
                   p.titulo AS parte, 
                   boosted_score AS score // Usamos la puntuación "boosted"
            ORDER BY score DESC
            LIMIT 3 // Devolvemos los 3 mejores chunks en total
        """

        result = session.run(cypher_query, params)
        for record in result:
            contexto_encontrado.append(dict(record))

    # Limpiamos duplicados (si varias consultas encontraron el mismo chunk)
    contexto_limpio = []
    ids_vistos = set()
    for item in contexto_encontrado:
        # Usamos 'contenido' como ID único para el chunk
        item_id = item['contenido']
        if item_id not in ids_vistos:
            contexto_limpio.append(item)
            ids_vistos.add(item_id)
            
    print(f"Paso 2: Investigador - Búsqueda completada. {len(contexto_limpio)} items únicos encontrados.")
    return contexto_limpio

# PASO 3: EL REDACTOR (Sin cambios, está bien)
def generar_respuesta_final(pregunta_usuario, contexto_items, modelo_gemini):
    print("Paso 3: Redactor - Generando respuesta final...")
    
    contexto_para_ia = ""
    fuentes_encontradas = set()

    if contexto_items:
        for item in contexto_items:
            fuente = f"{item['capitulo']} (Parte: {item['parte']}, Pág. {item['pagina']})"
            fuentes_encontradas.add(fuente)
            
            if item['tipo'] == 'Texto':
                contexto_para_ia += f"--- Contexto de TEXTO de '{fuente}' ---\n"
                contexto_para_ia += f"{item['contenido']}\n\n"
            elif item['tipo'] == 'Imagen':
                contexto_para_ia += f"--- Contexto de IMAGEN de '{fuente}' ---\n"
                contexto_para_ia += f"[Imagen relevante disponible en: {item['contenido']}]\n\n"
    
    if fuentes_encontradas:
        fuentes_str = ", ".join(fuentes_encontradas)
        fuente_para_prompt = f"(Fuente: {fuentes_str})"
    else:
        # Esto no debería pasar si la lógica de manejo de "if not contexto_items" funciona
        fuente_para_prompt = "" 
    
    prompt_para_ia = f"""
    Eres un asistente experto del manual de Blender. Tu ÚNICA tarea es responder la pregunta del usuario basándote ESTRICTA Y EXCLUSIVAMENTE en el 'Contexto' provisto.

    Sigue estas reglas AL PIE DE LA LETRA:
    
    1.  Revisa el 'Contexto'.
    2.  Si la respuesta a la pregunta del usuario se encuentra en el 'Contexto', responde la pregunta.
    3.  **REGLA CRÍTICA:** Si la respuesta a la pregunta NO se encuentra en el 'Contexto', DEBES responder EXACTAMENTE:
        "Lo siento, encontré información relacionada, pero no pude hallar una respuesta específica a tu pregunta en el manual."
    4.  NO uses ningún conocimiento externo o general.
    5.  Si el contexto incluye una URL de IMAGEN relevante, MENCIONA la imagen y su URL.
    6.  Al final de tu respuesta (si la encontraste en el contexto), cita tu fuente:
        "{fuente_para_prompt}"

    Contexto:
    {contexto_para_ia}
    Pregunta:
    {pregunta_usuario}
    Respuesta:
    """
    print(f"Paso 3: Redactor - Enviando prompt ESTRICTO a {generative_model_name}...")
    try:
        respuesta_ia = modelo_gemini.generate_content(prompt_para_ia)
        return respuesta_ia.text
    except Exception as e:
        print(f"Error en el Redactor (Paso 3): {e}")
        if "429" in str(e):
            return "El motor de IA está procesando demasiadas solicitudes. Por favor, espera un minuto e inténtalo de nuevo."
        return "Ocurrió un error al generar la respuesta."

# --- 4. El "Endpoint" de Flask (Sin cambios, está bien) ---

app = Flask(__name__)
CORS(app)

@app.route("/preguntar", methods=["POST"])
def manejar_pregunta():
    
    driver = None
    
    try:
        # 1. Conectar a Neo4j (FRESCO)
        print("Manejando petición: Conectando a Neo4j...")
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD), max_connection_lifetime=60)
        driver.verify_connectivity()
        print("Conexión a Neo4j establecida.")
        
        # 2. Conectar a IA (FRESCO)
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
        
        # PASO 1: Planificar
        plan = get_plan_de_busqueda(pregunta, driver, modelo_gemini)
        
        # PASO 2: Investigar (con la nueva lógica de BOOST)
        contexto_items = buscar_en_grafo(driver, plan)
        
        # REGLA DE ORO (Anti-Caos)
        if not contexto_items:
            print("No se encontró contexto en el grafo. Respondiendo directamente.")
            # Esta respuesta es mejor que la del Redactor, es más directa.
            return jsonify({"respuesta": "Lo siento, no pude encontrar información sobre eso en mi base de conocimientos del manual."})
        
        # PASO 3: Redactar
        respuesta = generar_respuesta_final(pregunta, contexto_items, modelo_gemini)
        
        return jsonify({"respuesta": respuesta})
    
    except Exception as e:
        print(f"--- ¡ERROR FATAL EN /preguntar! ---")
        print(f"Ocurrió un error: {e}")
        if "defunct connection" in str(e) or "Connection reset" in str(e):
             return jsonify({"respuesta": "Error de conexión a la base de datos. Por favor, reintenta."}), 503
        return jsonify({"respuesta": "Ocurrió un error interno mayor al procesar tu solicitud."}), 500
    
    finally:
        # 5. CERRAR LA CONEXIÓN
        if driver:
            driver.close()
            print("Manejando petición: Conexión a Neo4j cerrada.")

# --- 5. Arrancar el servidor ---
if __name__ == "__main__":
    app.run(debug=True, port=5000)
