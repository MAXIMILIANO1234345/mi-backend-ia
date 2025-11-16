import os
from dotenv import load_dotenv
import google.generativeai as genai
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
from neo4j import GraphDatabase

# --- 1. Cargar "secretos" y configurar clientes ---
print("Iniciando API (Versión Grafo RAZONADO)... Cargando variables.")
load_dotenv()

# Configurar Google AI
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
if not GOOGLE_API_KEY:
    raise ValueError("No se encontró la GOOGLE_API_KEY en el archivo .env")
genai.configure(api_key=GOOGLE_API_KEY)

# Configurar Neo4j
NEO4J_URI = os.getenv('NEO4J_URI')
NEO4J_USERNAME = os.getenv('NEO4J_USERNAME')
NEO4J_PASSWORD = os.getenv('NEO4J_PASSWORD')
NEO4J_DATABASE = os.getenv('NEO4J_DATABASE')
if not all([NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD, NEO4J_DATABASE]):
    raise ValueError("Faltan variables de entorno de Neo4j.")

# Modelos de IA
embedding_model = "models/text-embedding-004"
# Usamos el modelo intermedio
generative_model_name = "models/gemini-pro-latest" 

# Iniciar el "driver" de Neo4j
try:
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
    driver.verify_connectivity()
    print("Conectado a Neo4j.")
except Exception as e:
    print(f"Error fatal conectando a Neo4j: {e}")
    driver = None

# Iniciar el modelo de IA
try:
    modelo_gemini = genai.GenerativeModel(generative_model_name)
    print(f"Modelo {generative_model_name} cargado.")
except Exception as e:
    print(f"Error al cargar el modelo de Gemini: {e}")
    modelo_gemini = None

# --- 2. Funciones de Ayuda ---

def get_text_embedding(text_chunk, task_type="RETRIEVAL_QUERY"):
    """Vectoriza texto. Distingue entre consultas y documentos."""
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

# PASO 1: EL PLANIFICADOR
def get_plan_de_busqueda(pregunta_usuario):
    """
    Primer paso de la IA: "Pensar" qué buscar.
    """
    print(f"Paso 1: Planificador - Creando plan para: '{pregunta_usuario}'")
    
    # Obtenemos la lista de todos los capítulos desde el grafo para darle contexto
    try:
        with driver.session(database=NEO4J_DATABASE) as session:
            result = session.run("MATCH (c:Capitulo) RETURN c.titulo AS titulo")
            lista_capitulos = [record['titulo'] for record in result]
    except Exception as e:
        print(f"Error obteniendo lista de capítulos: {e}")
        lista_capitulos = [] # Continuar sin la lista si falla

    prompt_planificador = f"""
    Eres un experto en el manual de Blender. La pregunta de un usuario es: "{pregunta_usuario}"
    
    El manual tiene los siguientes capítulos:
    {', '.join(lista_capitulos)}

    Basado en la pregunta, ¿cuáles son los 3 conceptos o términos de búsqueda MÁS RELEVANTES que debo usar para encontrar la información en el manual?
    ¿Y debo buscar 'Texto', 'Imagenes' o 'Ambos'?

    Responde SÓLO con un JSON. Ejemplo:
    {{"conceptos_clave": ["Concepto 1", "Concepto 2"], "buscar": "Texto"}}
    """
    
    try:
        response = modelo_gemini.generate_content(prompt_planificador)
        # Limpiar la respuesta para que sea un JSON válido
        json_text = response.text.replace("```json", "").replace("```", "").strip()
        plan = json.loads(json_text)
        print(f"Paso 1: Planificador - Plan obtenido: {plan}")
        return plan
    except Exception as e:
        print(f"Error en el Planificador (Paso 1): {e}. Usando búsqueda simple.")
        # Plan de respaldo si la IA falla
        return {"conceptos_clave": [pregunta_usuario], "buscar": "Texto"}

# PASO 2: EL INVESTIGADOR
def buscar_en_grafo(db_driver, conceptos_clave, buscar_texto=True, buscar_imagenes=False):
    """Busca en Neo4j usando los conceptos clave del Planificador."""
    print(f"Paso 2: Investigador - Buscando conceptos: {conceptos_clave}")
    contexto_encontrado = []
    
    # Vectorizamos CADA concepto clave para una búsqueda más rica
    vectores_de_busqueda = []
    for concepto in conceptos_clave:
        vec = get_text_embedding(concepto, "RETRIEVAL_DOCUMENT") # Usamos "document" para una búsqueda más amplia
        if vec:
            vectores_de_busqueda.append(vec)
    
    # Si no hay vectores, usamos el de la pregunta original (aunque no debería pasar)
    if not vectores_de_busqueda:
        vectores_de_busqueda = [get_text_embedding(conceptos_clave[0], "RETRIEVAL_QUERY")]

    
    with db_driver.session(database=NEO4J_DATABASE) as session:
        # Hacemos una búsqueda por CADA vector y unimos los resultados
        for vector in vectores_de_busqueda:
            if buscar_texto:
                # Busca Chunks de Texto
                cypher_texto = """
                    CALL db.index.vector.queryNodes('chunk_vector_index', 2, $vector) YIELD node AS item, score
                    MATCH (item)-[:PERTENECE_A]->(c:Capitulo)-[:PERTENECE_A]->(p:Parte)
                    RETURN 'Texto' AS tipo, 
                           item.texto AS contenido, 
                           item.pagina AS pagina, 
                           c.titulo AS capitulo, 
                           p.titulo AS parte, 
                           score
                """
                result = session.run(cypher_texto, vector=vector)
                for record in result:
                    contexto_encontrado.append(dict(record))
            
            if buscar_imagenes:
                # Busca Imágenes
                cypher_img = """
                    CALL db.index.vector.queryNodes('imagen_vector_index', 1, $vector) YIELD node AS item, score
                    MATCH (item)-[:SE_ENCUENTRA_EN]->(c:Capitulo)-[:PERTENECE_A]->(p:Parte)
                    RETURN 'Imagen' AS tipo, 
                           item.url AS contenido, 
                           item.pagina AS pagina, 
                           c.titulo AS capitulo, 
                           p.titulo AS parte, 
                           score
                """
                result_img = session.run(cypher_img, vector=vector)
                for record in result_img:
                    contexto_encontrado.append(dict(record))

    # Limpiamos duplicados (si varios conceptos encontraron el mismo chunk)
    contexto_limpio = []
    ids_vistos = set()
    for item in contexto_encontrado:
        item_id = (item['tipo'], item['contenido']) # Usamos el contenido como ID único
        if item_id not in ids_vistos:
            contexto_limpio.append(item)
            ids_vistos.add(item_id)
            
    print(f"Paso 2: Investigador - Búsqueda completada. {len(contexto_limpio)} items únicos encontrados.")
    return contexto_limpio

# PASO 3: EL REDACTOR
def generar_respuesta_final(pregunta_usuario, contexto_items):
    """Toma el contexto LIMPIO y genera la respuesta final."""
    print("Paso 3: Redactor - Generando respuesta final...")
    
    contexto_para_ia = ""
    fuentes_encontradas = set()

    if contexto_items:
        for item in contexto_items:
            # Usamos la paginación y capítulo para la fuente
            fuente = f"{item['capitulo']} (Parte: {item['parte']}, Pág. {item['pagina']})"
            fuentes_encontradas.add(fuente)
            
            if item['tipo'] == 'Texto':
                contexto_para_ia += f"--- Contexto de TEXTO de '{fuente}' ---\n"
                contexto_para_ia += f"{item['contenido']}\n\n"
            elif item['tipo'] == 'Imagen':
                contexto_para_ia += f"--- Contexto de IMAGEN de '{fuente}' ---\n"
                contexto_para_ia += f"[Imagen relevante disponible en: {item['contenido']}]\n\n"
    else:
        print("Paso 3: Redactor - No se encontró contexto relevante.")

    if fuentes_encontradas:
        fuentes_str = ", ".join(fuentes_encontradas)
        fuente_para_prompt = f"(Fuente: {fuentes_str})"
    else:
        fuente_para_prompt = "(Fuente: Conocimiento General)"
    
    prompt_para_ia = f"""
    Eres un asistente de ayuda experto y un instructor del manual de Blender. Tu tarea es responder la pregunta del usuario.
    
    Sigue estas instrucciones:
    
    1.  Revisa el 'Contexto' provisto. El contexto puede incluir TEXTO e IMÁGENES (con sus URLs).
    2.  Si la respuesta está CLARAMENTE en el 'Contexto', responde basándote en él.
    3.  Si el contexto incluye una URL de IMAGEN que es relevante, MENCIONA la imagen y su URL en tu respuesta.
    4.  Si la respuesta NO está en el 'Contexto', o está vacío, usa tu conocimiento general para responder.
    5.  **IMPORTANTE:** Al final de tu respuesta, indica de dónde provino la información:
        * Si usaste el contexto: "{fuente_para_prompt}"
        * Si usaste tu conocimiento general: "(Fuente: Conocimiento General)"

    Contexto:
    {contexto_para_ia}

    Pregunta:
    {pregunta_usuario}

    Respuesta:
    """

    print(f"Paso 3: Redactor - Enviando prompt a {generative_model_name}...")
    try:
        respuesta_ia = modelo_gemini.generate_content(prompt_para_ia)
        return respuesta_ia.text
    except Exception as e:
        print(f"Error en el Redactor (Paso 3): {e}")
        # Manejamos el error 429 (límite de velocidad)
        if "429" in str(e):
            return "El motor de IA está procesando demasiadas solicitudes. Por favor, espera un minuto e inténtalo de nuevo."
        return "Ocurrió un error al generar la respuesta."


# --- 4. El "Endpoint" de Flask ---

app = Flask(__name__)
CORS(app)

@app.route("/preguntar", methods=["POST"])
def manejar_pregunta():
    
    if not modelo_gemini or not driver:
        return jsonify({"error": "El backend no está inicializado (IA o BD no conectadas)."}), 503

    data = request.json
    if not data or 'pregunta' not in data:
        return jsonify({"error": "No se proporcionó una 'pregunta' en el JSON"}), 400

    pregunta = data['pregunta']
    
    try:
        # --- ¡LA NUEVA LÓGICA! ---
        # 1. Planificar
        plan = get_plan_de_busqueda(pregunta)
        conceptos = plan.get('conceptos_clave', [pregunta])
        buscar_txt = "Texto" in plan.get('buscar', 'Texto')
        buscar_img = "Imagenes" in plan.get('buscar', 'Texto') # Cambiado de 'Imagenes' a 'Texto' como default

        # 2. Investigar
        contexto_items = buscar_en_grafo(driver, conceptos, buscar_txt, buscar_img)
        
        # 3. Redactar
        respuesta = generar_respuesta_final(pregunta, contexto_items)
        
        return jsonify({"respuesta": respuesta})
    
    except Exception as e:
        print(f"--- ¡ERROR FATAL EN /preguntar! ---")
        print(f"Ocurrió un error: {e}")
        return jsonify({"respuesta": "Ocurrió un error interno mayor al procesar tu solicitud."}), 500


# --- 5. Arrancar el servidor ---
# (El comando de Render `gunicorn app:app` llamará a 'app')
if __name__ == "__main__":
    # Esto es solo para pruebas locales
    app.run(debug=True, port=5000)
