import os
import json
import re
import google.generativeai as genai
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

# 1. Configuración Inicial
load_dotenv()
app = Flask(__name__)
CORS(app)

# Configuración de API Key
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

if not GEMINI_KEY:
    print("⚠️ ERROR: No se encontró GEMINI_API_KEY.")
else:
    print("✅ Motor Génesis 3B (A-Frame Edition) conectado.")
    genai.configure(api_key=GEMINI_KEY)

# 2. Configuración del Modelo
model = genai.GenerativeModel(
    model_name='gemini-2.5-flash', 
    generation_config={
        "temperature": 0.4, 
        "max_output_tokens": 4096, 
        "response_mime_type": "application/json",
    }
)

# 3. Prompt Maestro
SISTEMA_PROMPT = """
Actúa como el Arquitecto de Sistemas de Génesis 3B. Tu objetivo es generar escenas de A-Frame (WebVR) de nivel profesional.
REGLAS DE CONSTRUCCIÓN:
1. COMPOSICIÓN: No uses figuras básicas solas. Construye objetos complejos anidando <a-entity>.
2. MATERIALES PRO: Usa atributos como 'metalness: 0.9; roughness: 0.1'.
3. ANIMACIÓN: Todo objeto debe tener vida.
4. LUCES: Incluye luces puntuales.

ESTRUCTURA JSON OBLIGATORIA:
{
    "aframe_html": "HTML de las entidades",
    "explicacion": "Lógica arquitectónica",
    "narracion_voz": "Guion inmersivo"
}
"""

@app.route("/preguntar", methods=["POST"])
def preguntar():
    try:
        data = request.json
        pregunta = data.get('pregunta', 'Genera una estructura avanzada')
        
        full_prompt = f"{SISTEMA_PROMPT}\n\nComando de Usuario: {pregunta}"
        response = model.generate_content(full_prompt)
        
        raw_text = response.text.strip()
        match = re.search(r'(\{.*\}|\[.*\])', raw_text, re.DOTALL)
        
        if not match:
            raise ValueError("La respuesta de la IA no contiene un JSON válido.")
            
        json_clean = match.group(0)

        try:
            final_data = json.loads(json_clean)
            return jsonify(final_data), 200
        except json.JSONDecodeError as e:
            print(f"Error de parseo: {e}. Intentando reparación...")
            if not json_clean.endswith("}"):
                json_clean += '" }'
            return jsonify(json.loads(json_clean)), 200

    except Exception as e:
        print(f"🔥 Error en el Backend: {e}")
        return jsonify({
            "error": "Error interno del motor",
            "detalle": str(e)
        }), 500

# Elimina cualquier otro bloque 'if __name__ == "__main__":' que esté arriba de este.
# Este debe ser el ÚNICO al final de tu archivo.

if __name__ == "__main__":
    # Render inyecta el puerto en la variable PORT. 
    # Si no existe (local), usa el 10000.
    port = int(os.environ.get("PORT", 10000))
    
    # IMPORTANTE: host debe ser '0.0.0.0'
    # debug=False es vital en producción para evitar fugas de memoria
    app.run(host='0.0.0.0', port=port, debug=False)
