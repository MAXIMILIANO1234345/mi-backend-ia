import os
import json
import google.generativeai as genai
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
CORS(app)

GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

if not GEMINI_KEY:
    print("⚠️ ERROR: No se encontró GEMINI_API_KEY.")
else:
    genai.configure(api_key=GEMINI_KEY)
    print("✅ Motor Génesis 3B Conectado.")

# Configuración optimizada
model = genai.GenerativeModel(
    model_name='gemini-2.5-flash', # Cambiamos a 1.5-flash para máxima compatibilidad de cuota y JSON
    generation_config={
        "temperature": 0.2, # Bajamos a 0.2 para que sea más determinista y no invente formatos
        "response_mime_type": "application/json",
    }
)

SISTEMA_PROMPT = """
Eres el Arquitecto de Sistemas de Génesis 3B. Generas escenas de A-Frame (WebVR).
RESPONDE EXCLUSIVAMENTE EN FORMATO JSON con estas llaves:
{
    "aframe_html": "HTML de entidades anidadas y complejas",
    "explicacion": "Lógica del diseño",
    "narracion_voz": "Guion para el usuario"
}
"""

@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "online", "motor": "Génesis 3B"}), 200

@app.route("/preguntar", methods=["POST"])
def preguntar():
    try:
        data = request.json
        pregunta = data.get('pregunta', 'Genera una estructura base')
        
        prompt_final = f"{SISTEMA_PROMPT}\n\nComando: {pregunta}"
        response = model.generate_content(prompt_final)
        
        # Al usar response_mime_type: "application/json", no necesitamos REGEX.
        # El modelo DEBE entregar un JSON válido por defecto.
        try:
            res_json = json.loads(response.text)
            return jsonify(res_json), 200
        except json.JSONDecodeError:
            # Plan B: Si falla, intentamos limpiar solo por si acaso
            import re
            match = re.search(r'\{.*\}', response.text, re.DOTALL)
            if match:
                return jsonify(json.loads(match.group(0))), 200
            raise ValueError("La IA entregó un formato ilegible.")

    except Exception as e:
        print(f"🔥 Error: {e}")
        return jsonify({"error": "Fallo en el motor", "detalle": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
