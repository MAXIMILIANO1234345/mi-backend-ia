import os
from flask import Flask, request, jsonify
from flask_cors import CORS
import google.generativeai as genai
from dotenv import load_dotenv # Solo para uso local
import json # Asegúrate de importar json al inicio
# Carga .env si existe (local), si no existe (Render), no pasa nada
load_dotenv()

app = Flask(__name__)
CORS(app)

# Intentamos obtener la variable del entorno
# ASEGÚRATE que en el panel de Render diga exactamente: GEMINI_API_KEY
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

if not GEMINI_KEY:
    # Esto te ayudará a debuguear en los logs de Render
    print("⚠️ ERROR: La variable GEMINI_API_KEY está vacía en el entorno.")
else:
    print("✅ API Key detectada correctamente.")
    genai.configure(api_key=GEMINI_KEY)

# Configuración para evitar errores de JSON
# y aseguramos que tenga suficientes tokens para escupir scripts largos de Blender.
generation_config = {
    "temperature": 0.6,
    "max_output_tokens": 8192,
    "response_mime_type": "application/json",
}

model = genai.GenerativeModel('gemini-2.5-flash', generation_config=generation_config)

@app.route("/preguntar", methods=["POST"])
def preguntar():
    try:
        data = request.json
        pregunta = data.get('pregunta', '')
        
        prompt = f"..." # Tu prompt aquí
        
        response = model.generate_content(prompt)
        
        # 1. Limpieza de seguridad:
        # Eliminamos posibles bloques de markdown que la IA a veces agrega por error
        raw_text = response.text.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text.replace("```json", "", 1).replace("```", "", 1).strip()
        elif raw_text.startswith("```"):
            raw_text = raw_text.replace("```", "", 1).replace("```", "", 1).strip()

        # 2. Validación: intentamos cargar el JSON para ver si está completo
        try:
            json_data = json.loads(raw_text)
            return jsonify(json_data), 200
        except json.JSONDecodeError as e:
            print(f"JSON incompleto detectado: {e}")
            # Si el JSON está roto, le enviamos un error claro al frontend
            return jsonify({
                "error": "La respuesta de la IA fue demasiado larga y el JSON se cortó.",
                "posicion_error": str(e),
                "raw_partial_text": raw_text[:500] # Para debug
            }), 500

    except Exception as e:
        print(f"Error general: {e}")
        return jsonify({"error": str(e)}), 500
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)


