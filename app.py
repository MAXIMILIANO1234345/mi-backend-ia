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

# Configurar el modelo de IA
try:
    modelo_gemini = genai.GenerativeModel('models/gemini-2.5-flash')
except Exception as e:
    print(f"Error al cargar el modelo de Gemini: {e}")
    modelo_gemini = None

print("Clientes y modelo de IA (Estructurado) configurados. Servidor listo.")

# --- 2. Lógica de RAG (convertida en una función) ---

def obtener_respuesta_ia(pregunta_usuario):
    """Toma la pregunta, busca en RAG y devuelve la respuesta de la IA."""
    
    if not modelo_gemini:
        return "Error: El modelo de IA no pudo ser cargado."

    try:
        # --- PASO 1: BÚSQUEDA VECTORIAL ---
        print(f"Recibida pregunta: '{pregunta_usuario}'")
        
        result = genai.embed_content(
            model="models/text-embedding-004",
            content=pregunta_usuario,
            task_type="RETRIEVAL_QUERY"
        )
        query_vector = result['embedding']
        print("Pregunta vectorizada.")

        # Buscamos en la tabla 'secciones'
        response = supabase.rpc('match_documentos', {
            'query_embedding': query_vector,
            'match_threshold': 0.6, # Umbral de similitud
            'match_count': 5        # Traemos los 5 mejores chunks
        }).execute()

        contexto_para_ia = ""
        fuentes_encontradas = set() # Para evitar duplicados

        if response.data:
            print(f"Búsqueda vectorial completada. {len(response.data)} chunks encontrados.")
            
            # --- PASO 2: ¡EL "RAZONAMIENTO" SQL! ---
            for chunk in response.data:
                chunk_id = chunk['id']
                chunk_contenido = chunk['contenido']
                
                try:
                    # Esta consulta SQL une las 3 tablas
                    info_estructural = supabase.table('secciones').select(
                        'capitulos ( titulo, partes ( titulo ) )'
                    ).eq('id', chunk_id).single().execute()
                    
                    # --- MEJORA DE ROBUSTEZ ---
                    # Verificamos que los datos existen antes de acceder a ellos
                    if info_estructural.data and info_estructural.data.get('capitulos'):
                        capitulo_info = info_estructural.data['capitulos']
                        cap_titulo = capitulo_info.get('titulo', 'Capítulo Desconocido')
                        
                        if capitulo_info.get('partes'):
                            parte_titulo = capitulo_info['partes'].get('titulo', 'Parte Desconocida')
                        else:
                            parte_titulo = 'Parte Desconocida' # Si el capítulo no tiene parte
                        
                        fuente_completa = f"{cap_titulo} (Parte: {parte_titulo})"
                        fuentes_encontradas.add(fuente_completa)
                        
                        contexto_para_ia += f"--- Contexto de '{fuente_completa}' ---\n"
                        contexto_para_ia += f"{chunk_contenido}\n\n"
                        
                    else:
                        contexto_para_ia += f"--- Contexto (Sin Categorizar) ---\n"
                        contexto_para_ia += f"{chunk_contenido}\n\n"

                except Exception as e:
                    print(f"Error al buscar info estructural para chunk {chunk_id}: {e}")
                    contexto_para_ia += f"--- Contexto (Error de Búsqueda) ---\n"
                    contexto_para_ia += f"{chunk_contenido}\n\n"
        
        else:
            print("No se encontró contexto en los documentos (búsqueda vectorial vacía).")


        # --- PASO 3: GENERACIÓN (con el prompt Híbrido) ---
        
        if fuentes_encontradas:
            fuentes_str = ", ".join(fuentes_encontradas)
            fuente_para_prompt = f"(Fuente: {fuentes_str})"
        else:
            fuente_para_prompt = "(Fuente: Conocimiento General)"
            
        
        prompt_para_ia = f"""
        Eres un asistente de ayuda experto en el manual de Blender. Tu tarea es responder la pregunta del usuario.
        
        Sigue estas instrucciones:
        
        1.  Primero, revisa el 'Contexto' provisto. El contexto está organizado por capítulos y partes del manual.
        2.  Si la respuesta se encuentra CLARAMENTE en el 'Contexto', responde la pregunta basándote en él.
        3.  Si la respuesta NO se encuentra en el 'Contexto', o el contexto está vacío, usa tu conocimiento general para responder.
        4.  **IMPORTANTE:** Al final de tu respuesta, debes indicar de dónde provino la información.
            * Si usaste el contexto, termina tu respuesta con:
                "{fuente_para_prompt}"
            * Si usaste tu conocimiento general, termina tu respuesta con:
                "(Fuente: Conocimiento General)"

        Contexto:
        {contexto_para_ia}

        Pregunta:
        {pregunta_usuario}

        Respuesta:
        """

        print("Generando respuesta estructurada con Gemini...")
        respuesta_ia = modelo_gemini.generate_content(prompt_para_ia)
        
        return respuesta_ia.text

    except Exception as e:
        print(f"--- ¡ERROR DURANTE EL PROCESO! ---")
        print(f"Ocurrió un error: {e}")
        return "Ocurrió un error interno al procesar tu solicitud."

# --- 3. Configurar el Servidor Flask ---

app = Flask(__name__)
CORS(app)

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
