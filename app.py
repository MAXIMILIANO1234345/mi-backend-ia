import os
from dotenv import load_dotenv
import google.generativeai as genai
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
from neo4j import GraphDatabase

# --- 1. Cargar "secretos" y configurar clientes ---
print("Iniciando API (Versión Grafo)... Cargando variables de entorno.")
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
    raise ValueError("Faltan variables de entorno de Neo4j (URI, USERNAME, PASSWORD, DATABASE).")

# Modelos de IA
embedding_model = "models/text-embedding-004"
generative_model = "models/gemini-2.5-pro" # Usamos el PRO para razonar

# Iniciar el "driver" de Neo4j (la conexión principal)
try:
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
    driver.verify_connectivity()
    print("Conectado a Neo4j.")
except Exception as e:
    print(f"Error fatal conectando a Neo4j: {e}")
    driver = None

# Iniciar el modelo de IA
try:
    modelo_gemini = genai.GenerativeModel(generative_model)
    print(f"Modelo {generative_model} cargado.")
except Exception as e:
    print(f"Error al cargar el modelo de Gemini: {e}")
    modelo_gemini = None

# --- 2. Funciones de Ayuda ---

def get_text_embedding(text_chunk):
    """Vectoriza el texto de la pregunta del usuario."""
    try:
        result = genai.embed_content(
            model=embedding_model,
            content=text_chunk,
            task_type="RETRIEVAL_QUERY" # ¡Importante! Es una consulta
        )
        return result['embedding']
    except Exception as e:
        print(f"Error al vectorizar la pregunta: {e}")
        return None

def buscar_en_grafo(db_driver, vector_pregunta):
    """Busca en Neo4j los chunks e imágenes más relevantes."""
    print("Buscando en el grafo (Neo4j)...")
    contexto_encontrado = []
    
    # Esta es la consulta Cypher "Mágica"
    # Busca 3 chunks de texto Y 2 imágenes, y obtiene su estructura
    cypher_query = """
        // Busca Chunks de Texto
        CALL db.idx.vector.queryNodes('chunk_vector_index', 3, $vector) YIELD node AS item, score
        MATCH (item)-[:PERTENECE_A]->(c:Capitulo)-[:PERTENECE_A]->(p:Parte)
        RETURN 'Texto' AS tipo, 
               item.texto AS contenido, 
               item.pagina AS pagina, 
               c.titulo AS capitulo, 
               p.titulo AS parte, 
               score
        UNION
        // Busca Imágenes
        CALL db.idx.vector.queryNodes('imagen_vector_index', 2, $vector) YIELD node AS item, score
        MATCH (item)-[:SE_ENCUENTRA_EN]->(c:Capitulo)-[:PERTENECE_A]->(p:Parte)
        RETURN 'Imagen' AS tipo, 
               item.url AS contenido, 
               item.pagina AS pagina, 
               c.titulo AS capitulo, 
               p.titulo AS parte, 
               score
        ORDER BY score DESC
    """
    
    with db_driver.session(database=NEO4J_DATABASE) as session:
        result = session.run(cypher_query, vector=vector_pregunta)
        for record in result:
            contexto_encontrado.append(dict(record))
            
    print(f"Búsqueda en grafo completada. {len(contexto_encontrado)} items encontrados.")
    return contexto_encontrado


# --- 3. Lógica Principal de RAG ---

def obtener_respuesta_ia(pregunta_usuario):
    """Toma la pregunta, consulta el grafo y genera una respuesta."""
    
    if not modelo_gemini or not driver:
        return "Error: El backend no está conectado a la IA o a la Base de Datos."

    try:
        # --- PASO 1: VECTORIZAR LA PREGUNTA ---
        pregunta_vector = get_text_embedding(pregunta_usuario)
        if not pregunta_vector:
            return "Error al procesar la pregunta (vectorización fallida)."

        # --- PASO 2: INVESTIGADOR (Consultar el Grafo) ---
        contexto_items = buscar_en_grafo(driver, pregunta_vector)

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
        
        else:
            print("No se encontró contexto en el grafo.")

        # --- PASO 3: REDACTOR (Generar respuesta) ---
        
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

        print("Generando respuesta (Grafo) con Gemini Pro...")
        respuesta_ia = modelo_gemini.generate_content(prompt_para_ia)
        
        return respuesta_ia.text

    except Exception as e:
        print(f"--- ¡ERROR DURANTE EL PROCESO! ---")
        print(f"Ocurrió un error: {e}")
        return "Ocurrió un error interno al procesar tu solicitud."

# --- 4. Configurar el Servidor Flask ---

app = Flask(__name__)
CORS(app)

@app.route("/preguntar", methods=["POST"])
def manejar_pregunta():
    data = request.json
    if not data or 'pregunta' not in data:
        return jsonify({"error": "No se proporcionó una 'pregunta' en el JSON"}), 400
    pregunta = data['pregunta']
    respuesta = obtener_respuesta_ia(pregunta)
    return jsonify({"respuesta": respuesta})

# --- 5. Arrancar el servidor ---
# (El comando de Render `gunicorn app:app` llamará a 'app')
