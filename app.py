import os
import json
import re
from flask import Flask, request, jsonify
from flask_cors import CORS
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

# Configuración de Gemini (Usa variables de entorno para seguridad)
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyAGkLIbiw9g1qV-pMIhzpQ3OzT69HQMIb0")
genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel('gemini-2.5-flash') # Actualizado a la versión estable más eficiente

@app.route("/preguntar", methods=["POST"])
def preguntar():
    data = request.json
    pregunta = data.get('pregunta', '')
    
    # Prompt de ingeniería para resultados estructurados
    prompt = f"""
    Eres un Ingeniero experto en Computación Gráfica, Blender Python y A-Frame.
    Tu tarea es explicar cómo construir objetos 3D.
    
    Responde estrictamente en formato JSON con la siguiente estructura:
    {{
        "blender_python": "Código Python para Blender que use 'bpy'. Incluye limpieza de escena inicial.",
        "explicacion": "Texto breve para el chat sobre la lógica técnica.",
        "aframe_html": "Código A-Frame enriquecido. Usa <a-entity> con animaciones de entrada (property: scale; dur: 1000).",
        "narracion_voz": "Un guion narrativo para un asistente virtual que explique el proceso paso a paso."
    }}

    Pregunta: {pregunta}
    """
    
    try:
        response = model.generate_content(prompt)
        # Limpieza de markdown para asegurar JSON puro
        texto_limpio = re.sub(r'```json\s*|```', '', response.text).strip()
        return texto_limpio, 200, {'Content-Type': 'application/json'}
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
