import os
from dotenv import load_dotenv
from supabase import create_client, Client
import google.generativeai as genai
import json
from flask import Flask, request, jsonify  # ¡Nuevas importaciones de Flask!
from flask_cors import CORS             # ¡Nueva importación de CORS!

# --- 1. Cargar "secretos" y configurar clientes ---
# (Esto se ejecuta UNA SOLA VEZ cuando el servidor arranca)
load_dotenv()

# Configurar Google AI
google_api_key = os.getenv('GOOGLE_API_KEY')
if not google_api_key:
    raise ValueError("No se encontró la GOOGLE_API_KEY en el archivo .env")
genai.configure(api_key=google_api_key)

# Configurar Supabase
supabase_url = os.getenv('SUPABASE_URL')
supabase_key = os.getenv('SUPABASE_KEY')
if not supabase_url or not supabase_key:
    raise ValueError("No se encontraron las variables de Supabase en el archivo .env")
supabase: Client = create_client(supabase_url, supabase_key)

# Configurar el modelo de IA (cárgalo una vez)
try:
    # Usamos el modelo exacto que nos funcionó
    modelo_gemini = genai.GenerativeModel('models/gemini-2.5-flash')
except Exception as e:
    print(f"Error al cargar el modelo de Gemini: {e}")
    modelo_gemini = None

print("Clientes y modelo de IA configurados. Servidor listo para arrancar.")

# --- 2. Lógica de RAG (convertida en una función) ---

def obtener_respuesta_ia(pregunta_usuario):
    """Toma la pregunta, busca en RAG y devuelve la respuesta de la IA."""
    
    if not modelo_gemini:
        return "Error: El modelo de IA no pudo ser cargado."

    try:
        # --- PASO DE BÚSQUEDA (Retrieval) ---
        print(f"Recibida pregunta: '{pregunta_usuario}'")
        
        result = genai.embed_content(
            model="models/text-embedding-004",
            content=pregunta_usuario,
            task_type="RETRIEVAL_QUERY"
        )
        query_vector = result['embedding']
        print("Pregunta vectorizada.")

        response = supabase.rpc('match_documentos', {
            'query_embedding': query_vector,
            'match_threshold': 0.6,
            'match_count': 3
        }).execute()
        print(f"Búsqueda en Supabase completada. Documentos encontrados: {len(response.data)}")

        if not response.data:
            return "Lo siento, no encontré información en mis documentos para responder a esa pregunta."

        contexto = ""
        for doc in response.data:
            contexto += doc['contenido'] + "\n---\n"
        
        # --- PASO DE GENERACIÓN (Generation) ---
        
        prompt_para_ia = f"""
        Eres un asistente de ayuda para una plataforma educativa.
        Responde la pregunta del usuario de forma clara y amable, basándote ÚNICAMENTE en el siguiente contexto.
        Si la respuesta no está en el contexto, di que no tienes esa información.

        Contexto:
        {contexto}

        Pregunta:
        {pregunta_usuario}

        Respuesta:
        """

        print("Generando respuesta con Gemini...")
        respuesta_ia = modelo_gemini.generate_content(prompt_para_ia)
        
        return respuesta_ia.text

    except Exception as e:
        print(f"--- ¡ERROR DURANTE EL PROCESO! ---")
        print(f"Ocurrió un error: {e}")
        return "Ocurrió un error interno al procesar tu solicitud."

# --- 3. Configurar el Servidor Flask ---

app = Flask(__name__)  # Crea la aplicación web
CORS(app)              # Habilita CORS para permitir peticiones desde tu web

# --- 4. Crear el "Endpoint" de la API ---

@app.route("/preguntar", methods=["POST"])  # Esta es la URL (ej. .../preguntar)
def manejar_pregunta():
    """Escucha peticiones POST, procesa la pregunta y devuelve una respuesta."""
    
    # Obtenemos el JSON que nos envía el frontend
    # Esperamos un formato como: {"pregunta": "¿Cuándo son las clases?"}
    data = request.json
    
    if not data or 'pregunta' not in data:
        # Devolvemos un error si no nos envían el formato correcto
        return jsonify({"error": "No se proporcionó una 'pregunta' en el JSON"}), 400

    pregunta = data['pregunta']
    
    # ¡Llamamos a nuestra función de RAG!
    respuesta = obtener_respuesta_ia(pregunta)
    
    # Devolvemos la respuesta de la IA en formato JSON
    return jsonify({"respuesta": respuesta})

# --- 5. Arrancar el servidor ---

if __name__ == "__main__":
    # Esto permite ejecutar el servidor con "python app.py"
    # El 'debug=True' hace que el servidor se reinicie solo si haces cambios.
    app.run(debug=True, port=5000)