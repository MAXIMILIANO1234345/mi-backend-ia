import os
from flask import Flask, request, jsonify
from flask_cors import CORS
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

# Configurar Gemini
genai.configure(api_key=os.getenv("AIzaSyDWQx5l5T_l3X3Ld-W0AlXjROMXbjZ8SWQ"))
model = genai.GenerativeModel('gemini-2.5-flash') # El más rápido y gratis

@app.route("/preguntar", methods=["POST"])
def preguntar():
    data = request.json
    pregunta = data.get('pregunta', '')
    
    prompt = f"""
    Eres un experto en Blender Python y A-Frame.
    Responde estrictamente en formato JSON con estas llaves:
    {{
        "blender_python": "código aquí",
        "explicacion": "explicación aquí",
        "aframe_html": "código a-frame aquí"
    }}
    Pregunta: {pregunta}
    """
    
    try:
        response = model.generate_content(prompt)
        # Limpiamos la respuesta por si Gemini pone ```json ... ```
        texto_limpio = response.text.replace("```json", "").replace("```", "").strip()
        return texto_limpio, 200, {'Content-Type': 'application/json'}
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
