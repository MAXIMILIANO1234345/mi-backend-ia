import os
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
import google.generativeai as genai
from dotenv import load_dotenv

# Esto carga las variables desde un archivo .env
load_dotenv()

app = Flask(__name__)
CORS(app)

# Aquí ya no pegamos la llave. El sistema la leerá de forma segura.
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_KEY:
    print("⚠️ ERROR: No se encontró la GEMINI_API_KEY. Configúrala en tu archivo .env")
else:
    genai.configure(api_key=GEMINI_KEY)

# Configuración para evitar errores de JSON
generation_config = {
    "temperature": 0.4,
    "response_mime_type": "application/json",
}

model = genai.GenerativeModel('gemini-2.5-flash', generation_config=generation_config)

@app.route("/preguntar", methods=["POST"])
def preguntar():
    try:
        data = request.json
        pregunta = data.get('pregunta', '')
        
        prompt = f"""
        Actúa como el motor del Proyecto Génesis 3B. 
        Responde estrictamente en formato JSON:
        {{
            "blender_python": "código aquí",
            "explicacion": "texto aquí",
            "aframe_html": "entidades aframe aquí",
            "narracion_voz": "guion para voz sintética"
        }}
        Pregunta: {pregunta}
        """
        
        response = model.generate_content(prompt)
        return response.text, 200, {'Content-Type': 'application/json'}

    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)

