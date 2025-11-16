import os
from dotenv import load_dotenv
from supabase import create_client, Client
import google.generativeai as genai
import json
from flask import Flask, request, jsonify
from flask_cors import CORS

# --- 1. Cargar "secretos" y configurar clientes ---
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
            'match_threshold': 0.6, # Puedes ajustar este umbral si es necesario
            'match_count': 3
        }).execute()

        # --- CAMBIO 1: LÓGICA HÍBRIDA ---
        # Ahora, en lugar de detenernos, solo preparamos el contexto
        # si es que lo encontramos.
        contexto = ""
        if response.data:
            print(f"Búsqueda en Supabase completada. Documentos encontrados: {len(response.data)}")
            for doc in response.data:
                contexto += doc['contenido'] + "\n---\n"
        else:
            # Si no hay datos, el 'contexto' se queda vacío ""
            print("No se encontró contexto en los documentos.")
        
        # --- PASO DE GENERACIÓN (Generation) ---
        
        # --- CAMBIO 2: PROMPT HÍBRIDO ---
        # Este es el nuevo prompt que prioriza el contexto pero
        # permite usar conocimiento general y cita sus fuentes.
        prompt_para_ia = f"""
        Eres un asistente de ayuda para una plataforma educativa. Tu tarea es responder la pregunta del usuario.
        
        Sigue estas instrucciones:
        
        1.  Primero, revisa el 'Contexto' provisto y busca la respuesta allí. El contexto es tu fuente principal de verdad.
        2.  Si la respuesta se encuentra CLARAMENTE en el 'Contexto', responde la pregunta basándote en él.
        3.  Si la respuesta NO se encuentra en el 'Contexto', o el contexto está vacío, usa tu conocimiento general para responder.
        4.  **IMPORTANTE:** Al final de tu respuesta, debes indicar de dónde provino la información.
            * Si usaste el contexto, termina tu respuesta con:
                "(Fuente: Documentos de la Plataforma)"
            * Si usaste tu conocimiento general, termina tu respuesta con:
                "(Fuente: Conocimiento General)"

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
CORS(app)              # Habilita CORS

# --- 4. Crear el "Endpoint" de la API ---

@app.route("/preguntar", methods=["POST"])
def manejar_pregunta():
    data = request.json
    
    if not data or 'pregunta' not in data:
        return jsonify({"error": "No se proporcionó una 'pregunta' en el JSON"}), 400

    pregunta = data['pregunta']
    respuesta = obtener_respuesta_ia(pregunta)
    return jsonify({"respuesta": respuesta})

# --- 5. Arrancar el servidor ---

if __name__ == "__main__":
    app.run(debug=True, port=5000)
