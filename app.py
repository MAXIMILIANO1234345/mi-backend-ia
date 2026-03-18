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
# Optimizamos para estabilidad y precisión estructural
model = genai.GenerativeModel(
    model_name='gemini-2.5-flash', 
    generation_config={
        "temperature": 0.4, # Precisión técnica
        "max_output_tokens": 4096, 
        "response_mime_type": "application/json",
    }
)

# 3. Prompt Maestro de Arquitectura Virtual
SISTEMA_PROMPT = """
Actúa como el Arquitecto de Sistemas de Génesis 3B. Tu objetivo es generar escenas de A-Frame (WebVR) de nivel profesional.
REGLAS DE CONSTRUCCIÓN:
1. COMPOSICIÓN: No uses figuras básicas solas. Construye objetos complejos anidando <a-entity> (ej. un radar son múltiples círculos y líneas con diferentes rotaciones).
2. MATERIALES PRO: Usa atributos como 'metalness: 0.9; roughness: 0.1' para superficies metálicas y 'emissive: #color; emissiveIntensity: 2' para luces integradas.
3. ANIMACIÓN: Todo objeto debe tener vida. Usa el componente 'animation' para rotaciones sutiles o pulsaciones.
4. LUCES: Incluye luces puntuales (<a-light type="point">) dentro de tus objetos para crear atmósfera.

ESTRUCTURA JSON OBLIGATORIA:
{
    "aframe_html": "HTML de las entidades (pulcro y anidado)",
    "explicacion": "Lógica arquitectónica del objeto",
    "narracion_voz": "Guion inmersivo de la IA de mando"
}
"""

@app.route("/preguntar", methods=["POST"])
def preguntar():
    try:
        data = request.json
        pregunta = data.get('pregunta', 'Genera una estructura avanzada')
        
        # Llamada a la IA
        full_prompt = f"{SISTEMA_PROMPT}\n\nComando de Usuario: {pregunta}"
        response = model.generate_content(full_prompt)
        
        # --- SISTEMA ANTIRROTURA DE JSON ---
        raw_text = response.text.strip()
        
        # Buscamos el bloque JSON real con expresiones regulares
        # Esto ignora cualquier texto que la IA ponga antes o después del JSON
        match = re.search(r'(\{.*\}|\[.*\])', raw_text, re.DOTALL)
        
        if not match:
            raise ValueError("La respuesta de la IA no contiene un JSON válido.")
            
        json_clean = match.group(0)

        # Validamos que el JSON sea parseable
        try:
            final_data = json.loads(json_clean)
            return jsonify(final_data), 200
        except json.JSONDecodeError as e:
            # Si el JSON está cortado, intentamos una reparación de emergencia
            print(f"Error de parseo: {e}. Intentando reparación...")
            if not json_clean.endswith("}"):
                json_clean += '" }' # Cierre de emergencia para strings y objetos
            return jsonify(json.loads(json_clean)), 200

    except Exception as e:
        print(f"🔥 Error en el Backend: {e}")
        return jsonify({
            "error": "Error interno del motor",
            "detalle": str(e)
        }), 500

if __name__ == "__main__":
    # IMPORTANTE: En Render, asegúrate que el Start Command sea:
    # gunicorn --timeout 90 app:app

    # Render usa la variable de entorno PORT. Si no existe, usamos 10000 por defecto.
    port = int(os.environ.get("PORT", 10000))
    
    # IMPORTANTE: host='0.0.0.0' es obligatorio para que sea visible externamente
    app.run(host='0.0.0.0', port=port, debug=False)
