import os
from flask import Flask, request, jsonify
from flask_cors import CORS
import google.generativeai as genai
from dotenv import load_dotenv # Solo para uso local

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
        
        # PROMPT MEJORADO: Forzando complejidad procedural
        prompt = f"""
        Actúa como el motor avanzado del Proyecto Génesis 3B. 
        Tu objetivo crítico es generar geometría 3D compleja, detallada y paramétrica. 
        ESTÁ ESTRICTAMENTE PROHIBIDO entregar simples primitivas aisladas (cubos o esferas básicas).

        Directrices para 'blender_python':
        1. Utiliza programación procedural avanzada: 'bmesh' para manipulación de vértices/caras, o bucles for/while para generar estructuras repetitivas o fractales.
        2. Aplica modificadores: Subdivision Surface, Bevel, Array, o Displace (con texturas de ruido generadas por código).
        3. Crea materiales procedurales realistas usando el sistema de nodos (Principled BSDF, Noise Texture, ColorRamp, Bump/Normal maps).
        4. El código debe ser limpio, estar comentado y no requerir dependencias externas.

        Directrices para 'aframe_html':
        1. Construye modelos compuestos (prefabs) anidando múltiples etiquetas <a-entity> con diferentes geometrías para formar un objeto complejo.
        2. Aplica materiales detallados (roughness, metalness, env-map).
        3. Incluye iluminación (luces puntuales o direccionales con sombras) y animaciones sutiles (rotación, flotación) mediante <a-animation> o el componente 'animation'.

        Responde estrictamente en este formato JSON:
        {{
            "blender_python": "código python avanzado y robusto aquí",
            "explicacion": "Explica brevemente la lógica matemática o procedural usada para la complejidad del modelo",
            "aframe_html": "entidades aframe complejas y anidadas aquí",
            "narracion_voz": "guion inmersivo y detallado para voz sintética"
        }}
        
        Comando de usuario: {pregunta}
        """
        
        response = model.generate_content(prompt)
        return response.text, 200, {'Content-Type': 'application/json'}

    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)


