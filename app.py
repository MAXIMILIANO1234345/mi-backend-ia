import os
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

# Configura tu API Key
genai.configure(api_key=os.getenv("GEMINI_API_KEY", "AIzaSyBTS2qq7Cw8YDv34Pc5uXxtPx2uhr2qJdA"))

# Usamos la configuración de respuesta JSON
generation_config = {
    "temperature": 0.2, # Bajamos la temperatura para que sea más preciso
    "response_mime_type": "application/json",
}

model = genai.GenerativeModel('gemini-2.5-flash', generation_config=generation_config)

@app.route("/preguntar", methods=["POST"])
def preguntar():
    try:
        data = request.json
        pregunta = data.get('pregunta', '')
        
        prompt = f"""
        Responde estrictamente en formato JSON con estas llaves:
        "blender_python", "explicacion", "aframe_html", "narracion_voz".
        
        En 'blender_python', asegúrate de que el código sea funcional y esté limpio.
        En 'aframe_html', genera solo las entidades necesarias.
        
        Pregunta: {pregunta}
        """
        
        response = model.generate_content(prompt)
        
        # PASO CRÍTICO: Convertimos el texto de la IA a un objeto Python
        # y luego Flask lo convierte en un JSON seguro para la web.
        json_response = json.loads(response.text)
        
        return jsonify(json_response) # Esto escapa automáticamente comillas y saltos de línea

    except Exception as e:
        print(f"Error en el servidor: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
