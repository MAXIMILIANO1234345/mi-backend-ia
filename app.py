import os
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

# 1. ASEGÚRATE DE QUE ESTA KEY SEA VÁLIDA O USA .ENV
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyAGkLIbiw9g1qV-pMIhzpQ3OzT69HQMIb0")
genai.configure(api_key=GEMINI_KEY)

# 2. CONFIGURACIÓN DE RESPUESTA JSON PURA
generation_config = {
    "temperature": 0.7,
    "top_p": 0.95,
    "top_k": 40,
    "max_output_tokens": 8192,
    "response_mime_type": "application/json", # <--- ESTO EVITA EL ERROR 500
}

model = genai.GenerativeModel(
    model_name="gemini-1.5-flash", # Usa 1.5-flash para mayor estabilidad
    generation_config=generation_config,
)

@app.route("/preguntar", methods=["POST"])
def preguntar():
    try:
        data = request.json
        pregunta = data.get('pregunta', '')
        
        prompt = f"""
        Eres un ingeniero experto en Blender y A-Frame.
        Responde estrictamente con este esquema JSON:
        {{
            "blender_python": "código completo de bpy",
            "explicacion": "breve descripción técnica",
            "aframe_html": "código de entidades A-Frame",
            "narracion_voz": "guion para el asistente de voz"
        }}
        
        Pregunta del usuario: {pregunta}
        """
        
        response = model.generate_content(prompt)
        
        # Si Gemini devuelve JSON, lo enviamos tal cual
        return response.text, 200, {'Content-Type': 'application/json'}

    except Exception as e:
        # Esto imprimirá el error real en tu consola de Python/Render
        print(f"CRASH DETECTADO: {str(e)}")
        return jsonify({"error": "Error interno del servidor", "detalle": str(e)}), 500

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
