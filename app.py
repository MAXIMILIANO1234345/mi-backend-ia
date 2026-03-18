import os
import json
import re  # Para limpieza avanzada
import google.generativeai as genai
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
CORS(app)

GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_KEY)

# Usamos flash-2.0 o pro si lo tienes disponible para mejor seguimiento de instrucciones
model = genai.GenerativeModel(
    model_name='gemini-2.5-flash', 
    generation_config={
        "temperature": 0.5, # Bajamos un poco para reducir errores sintácticos
        "max_output_tokens": 8192,
        "response_mime_type": "application/json",
    }
)

SISTEMA_PROMPT = """
Actúa como el motor Génesis 3B. Tu salida DEBE ser un JSON puro y válido.
IMPORTANTE: El código Python dentro de "blender_python" debe tratar las comillas internas con extremo cuidado. 
Usa comillas simples (') para strings dentro del código Python para evitar romper las comillas dobles (") del JSON.

Estructura requerida:
{
    "blender_python": "import bpy; ... (usa bmesh y modificadores)",
    "explicacion": "...",
    "aframe_html": "...",
    "narracion_voz": "..."
}
"""

@app.route("/preguntar", methods=["POST"])
def preguntar():
    try:
        data = request.json
        pregunta = data.get('pregunta', '')
        
        response = model.generate_content(f"{SISTEMA_PROMPT}\n\nPregunta: {pregunta}")
        
        # --- LIMPIEZA NIVEL INDUSTRIAL ---
        texto_sucio = response.text.strip()
        
        # Buscamos donde empieza y termina el objeto JSON real
        try:
            inicio = texto_sucio.find('{')
            fin = texto_sucio.rfind('}') + 1
            if inicio == -1 or fin == 0:
                raise ValueError("No se encontró un objeto JSON en la respuesta.")
            
            json_limpio = texto_sucio[inicio:fin]
            
            # Validamos parseando
            data_final = json.loads(json_limpio)
            return jsonify(data_final), 200

        except Exception as parse_error:
            print(f"DEBUG - Respuesta fallida de la IA: {texto_sucio}") # Ver en logs de Render
            return jsonify({
                "error": "Respuesta malformada",
                "detalle": str(parse_error),
                "ayuda": "La IA envió un formato inválido. Intenta ser más específico con el objeto."
            }), 500

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
