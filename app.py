import os
from flask import Flask, request, jsonify
from flask_cors import CORS
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

# Configuración de Gemini con tu API Key (idealmente usa un archivo .env)
genai.configure(api_key=os.environ.get("GEMINI_API_KEY", "AIzaSyAGkLIbiw9g1qV-pMIhzpQ3OzT69HQMIb0"))

# MAGIA AQUÍ: Forzamos al modelo a responder en JSON nativo. Adiós a los errores de parseo.
generation_config = {"response_mime_type": "application/json"}
model = genai.GenerativeModel('gemini-2.5-flash', generation_config=generation_config)

@app.route("/preguntar", methods=["POST"])
def preguntar():
    data = request.json
    pregunta = data.get('pregunta', '')
    
    prompt = f"""
    Eres el motor lógico del 'Proyecto Génesis 3B'. Eres un experto en automatización 3D con Blender Python y renderizado web con A-Frame.
    
    Tu tarea es generar la estructura solicitada por el usuario.
    Devuelve EXACTAMENTE esta estructura JSON:
    {{
        "blender_python": "Script de bpy completo. Usa variables claras y comenta cada paso. Limpia la escena al inicio.",
        "explicacion": "Explicación técnica y profesional de qué hace el código.",
        "aframe_html": "Código de <a-entity>. Usa la propiedad 'animation' para que los elementos aparezcan de forma fluida (ej. escalando de 0 a 1).",
        "narracion_voz": "Guion corto, carismático y natural para que el asistente de IA lo narre en voz alta mientras aparece el modelo."
    }}
    
    Petición del usuario: {pregunta}
    """
    
    try:
        response = model.generate_content(prompt)
        # Como forzamos application/json, response.text es directamente un string JSON válido
        return response.text, 200, {'Content-Type': 'application/json'}
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
