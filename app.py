import os
from dotenv import load_dotenv
import google.generativeai as genai
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
from neo4j import GraphDatabase

# --- 1. Cargar "secretos" y configurar clientes ---
print("Iniciando API (Versión Grafo 6.0 - FILTRADO)... Cargando variables.")
load_dotenv()

# Cargar todas las claves (se usarán dentro de la función)
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
NEO4J_URI = os.getenv('NEO4J_URI')
NEO4J_USERNAME = os.getenv('NEO4J_USERNAME')
NEO4J_PASSWORD = os.getenv('NEO4J_PASSWORD')
NEO4J_DATABASE = os.getenv('NEO4J_DATABASE')

# Modelos de IA
embedding_model = "models/text-embedding-004"
generative_model_name = "models/gemini-2.5-flash" # Rápido y con alto límite de peticiones # Intermedio

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

# --- 3. LÓGICA DE IA DE MÚLTIPLES PASOS (LA VERSIÓN BUENA) ---

# PASO 1: EL PLANIFICADOR (DECIDE QUÉ CAPÍTULOS USAR)
def get_plan_de_busqueda(pregunta_usuario, driver, modelo_gemini):
    """
    Paso 1: La IA "piensa" qué capítulos son relevantes.
    """
    print(f"Paso 1: Planificador - Creando plan para: '{pregunta_usuario}'")
    lista_capitulos = []
    try:
        # Obtenemos la lista de TODOS los capítulos desde el grafo
        with driver.session(database=NEO4J_DATABASE) as session:
            result = session.run("MATCH (c:Capitulo) RETURN c.titulo AS titulo")
            lista_capitulos = [record['titulo'] for record in result]
    except Exception as e:
        print(f"Error obteniendo lista de capítulos: {e}")
        # Si falla, el planificador no tendrá la lista, pero puede continuar
    
    # ¡NUEVO PROMPT! Le pedimos que ELIJA de la lista de capítulos.
    prompt_planificador = f"""
    Eres un bibliotecario experto del manual de Blender. La pregunta de un usuario es: "{pregunta_usuario}"
    
    El manual tiene los siguientes capítulos:
    {json.dumps(lista_capitulos)}

    Tu tarea es decidir en qué capítulos debo buscar la respuesta.
    Por favor, devuelve una lista JSON de los **títulos de capítulo exactos** que sean más relevantes.
    Si la pregunta es general (como 'hola' o 'qué es Blender'), busca en la introducción.
    
    Responde SÓLO con un JSON. Ejemplo:
    {{"capitulos_objetivo": ["1.2. El Entorno de Trabajo: Áreas y Editores", "1.3. Navegación 3D Esencial"]}}
    """
    
    try:
        response = modelo_gemini.generate_content(prompt_planificador)
        json_text = response.text.replace("```json", "").replace("```", "").strip()
        plan = json.loads(json_text)
        print(f"Paso 1: Planificador - Plan obtenido: {plan}")
        return plan
    except Exception as e:
        print(f"Error en el Planificador (Paso 1): {e}.")
        # Plan de respaldo: Si el planificador falla, no filtramos (lista vacía)
        return {"capitulos_objetivo": []}

# PASO 2: EL INVESTIGADOR (FILTRA POR GRAFO, LUEGO BUSCA POR VECTOR)
def buscar_en_grafo(db_driver, vector_pregunta, capitulos_objetivo, buscar_imagenes=False):
    """
    Busca en Neo4j, PERO filtra los resultados solo a los capítulos relevantes.
    """
    print(f"Paso 2: Investigador - Buscando en capítulos: {capitulos_objetivo}")
    contexto_encontrado = []
    
    with db_driver.session(database=NEO4J_DATABASE) as session:
        
        # --- ¡LA CONSULTA MÁGICA (FILTRADO DE GRAFO)! ---
        # 1. Busca los 10 chunks más parecidos en TODA la base de datos (Búsqueda Vectorial)
        # 2. LUEGO, filtra esa lista para quedarse solo con los chunks que
        #    pertenecen a los capítulos que el Planificador eligió.
        # 3. Devuelve los 3 mejores de esa lista filtrada.
        
        # Si la lista de capítulos está vacía (porque el planificador falló o no encontró),
        # hacemos una búsqueda simple sin filtro.
        if not capitulos_objetivo:
            print("Paso 2: Investigador - Planificador no dio capítulos, haciendo búsqueda simple.")
            cypher_base = """
                CALL db.index.vector.queryNodes('chunk_vector_index', 3, $vector) YIELD node AS item, score
            """
        else:
            print("Paso 2: Investigador - Aplicando filtro de capítulos.")
            cypher_base = """
                CALL db.index.vector.queryNodes('chunk_vector_index', 10, $vector) YIELD node AS item, score
                MATCH (item)-[:PERTENECE_A]->(c:Capitulo)
                WHERE c.titulo IN $capitulos_objetivo
            """

        # Consulta de Texto
        cypher_texto = cypher_base + """
            MATCH (item)-[:PERTENECE_A]->(c:Capitulo)-[:PERTENECE_A]->(p:Parte)
            RETURN 'Texto' AS tipo, 
                   item.texto AS contenido, 
                   item.pagina AS pagina, 
                   c.titulo AS capitulo, 
                   p.titulo AS parte, 
                   score
            ORDER BY score DESC
            LIMIT 3
        """
        
        result_texto = session.run(cypher_texto, vector=vector_pregunta, capitulos_objetivo=capitulos_objetivo)
        for record in result_texto:
            contexto_encontrado.append(dict(record))

        # (La lógica de imágenes se mantiene, por si las añades en el futuro)
        if buscar_imagenes:
            cypher_img = """
                CALL db.index.vector.queryNodes('imagen_vector_index', 2, $vector) YIELD node AS item, score
                MATCH (item)-[:SE_ENCUENTRA_EN]->(c:Capitulo)
                WHERE c.titulo IN $capitulos_objetivo
                MATCH (c)-[:PERTENECE_A]->(p:Parte)
                RETURN 'Imagen' AS tipo, 
                       item.url AS contenido, 
                       item.pagina AS pagina, 
                       c.titulo AS capitulo, 
                       p.titulo AS parte, 
                       score
                ORDER BY score DESC
                LIMIT 2
            """
            result_img = session.run(cypher_img, vector=vector_pregunta, capitulos_objetivo=capitulos_objetivo)
            for record in result_img:
                contexto_encontrado.append(dict(record))

    print(f"Paso 2: Investigador - Búsqueda completada. {len(contexto_encontrado)} items filtrados encontrados.")
    return contexto_encontrado

# PASO 3: EL REDACTOR (Ahora recibe contexto 100% limpio)
def generar_respuesta_final(pregunta_usuario, contexto_items, modelo_gemini):
    """Toma el contexto LIMPIO y genera la respuesta final."""
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
        fuente_para_prompt = "" 
    
    # PROMPT ESTRICTO
    prompt_para_ia = f"""
    Eres un asistente experto del manual de Blender. Tu ÚNICA tarea es responder la pregunta del usuario basándote ESTRICTA Y EXCLUSIVAMENTE en el 'Contexto' provisto.

    Sigue estas reglas AL PIE DE LA LETRA:
    
    1.  Revisa el 'Contexto' (que incluye texto e imágenes).
    2.  Si la respuesta a la pregunta del usuario se encuentra en el 'Contexto', responde la pregunta.
    3.  **REGLA CRÍTICA:** Si la respuesta a la pregunta NO se encuentra en el 'Contexto', DEBES responder EXACTAMENTE:
        "Lo siento, encontré información relacionada, pero no pude hallar una respuesta específica a tu pregunta en el manual."
    4.  NO uses ningún conocimiento externo o general. No inventes nada. Tu conocimiento se limita al 100% al contexto.
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

# --- 4. El "Endpoint" de Flask ---

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
        plan = get_plan_de_busqueda(pregunta, driver, modelo_gemini)
        
        capitulos = plan.get('capitulos_objetivo', []) 
        buscar_img = "Imagenes" in plan.get('buscar', 'Ambos') # (preparado para el futuro)

        # Vectorizamos la pregunta original
        vector_pregunta = get_text_embedding(pregunta)
        if not vector_pregunta:
            return jsonify({"respuesta": "Error: No se pudo procesar tu pregunta."}), 500

        # Buscamos en el grafo CON el filtro de capítulos
        contexto_items = buscar_en_grafo(driver, vector_pregunta, capitulos, buscar_img)
        
        # REGLA DE ORO 1
        if not contexto_items:
            print("No se encontró contexto en el grafo. Respondiendo directamente.")
            return jsonify({"respuesta": "Lo siento, no pude encontrar información sobre eso en mi base de conocimientos del manual."})
        
        # 5. Redactar (SOLO si hay contexto)
        respuesta = generar_respuesta_final(pregunta, contexto_items, modelo_gemini)
        
        return jsonify({"respuesta": respuesta})
    
    except Exception as e:
        print(f"--- ¡ERROR FATAL EN /preguntar! ---")
        print(f"Ocurrió un error: {e}")
        if "defunct connection" in str(e) or "Connection reset" in str(e):
             return jsonify({"respuesta": "Error de conexión a la base de datos. Por favor, reintenta."}), 503
        return jsonify({"respuesta": "Ocurrió un error interno mayor al procesar tu solicitud."}), 500
    
    finally:
        # 6. CERRAR LA CONEXIÓN
        if driver:
            driver.close()
            print("Manejando petición: Conexión a Neo4j cerrada.")

# --- 5. Arrancar el servidor ---
if __name__ == "__main__":
    app.run(debug=True, port=5000)

