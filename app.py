import os
import json
import google.generativeai as genai
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

# 1. Configuración Inicial
load_dotenv()
app = Flask(__name__)
CORS(app)

# Configuración de la API Key
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

if not GEMINI_KEY:
    print("⚠️ ERROR: No se encontró GEMINI_API_KEY en las variables de entorno.")
else:
    print("✅ Motor Génesis 3B conectado y listo.")
    genai.configure(api_key=GEMINI_KEY)

# 2. Configuración del Modelo (Maximizamos creatividad y capacidad de respuesta)
generation_config = {
    "temperature": 0.75,         # Un poco más alto para mayor variedad visual
    "max_output_tokens": 8192,  # Vital para que el código largo no se corte
    "top_p": 0.95,
    "response_mime_type": "application/json",
}

# Usamos el modelo más capaz para tareas técnicas
model = genai.GenerativeModel(
    model_name='gemini-2.5-flash', 
    generation_config=generation_config
)

# 3. Prompt Maestro (El "cerebro" de la complejidad)
SISTEMA_PROMPT = """
Actúa como el motor de síntesis procedural 'Génesis 3B'. Tu especialidad es el modelado técnico-artístico.
Tu objetivo es generar activos 3D de alta fidelidad mediante código.

REGLAS DE ORO PARA BLENDER (blender_python):
- PROHIBIDO: Usar solo primitivas básicas sin modificar.
- OBLIGATORIO: 
    1. Uso de 'bmesh' para crear geometrías personalizadas vértice a vértice si es necesario.
    2. Aplicación de modificadores en cadena (ej: Bevel -> Subdivision -> Displace con Noise).
    3. Creación de materiales realistas usando 'nodes'. Configura el Principled BSDF con valores de Metallic, Roughness y Clearcoat.
    4. Usa bucles (for i in range) para crear patrones, estructuras fractales o detalles repetitivos (tornillos, rejillas, paneles).

REGLAS DE ORO PARA AFRAME (aframe_html):
- Crea estructuras jerárquicas: Un objeto principal con múltiples sub-componentes.
- Usa luces dinámicas (point o spot) vinculadas al objeto para que brille.
- Agrega animaciones sutiles que den vida al objeto (flotación suave, rotación de partes).

RESPUESTA ESTRICTA EN JSON:
{
    "blender_python": "código python completo, con imports, que genera el objeto y su material",
    "explicacion": "Análisis técnico de la estructura generada",
    "aframe_html": "HTML de A-Frame con entidades anidadas y luces",
    "narracion_voz": "Descripción inmersiva del objeto para el usuario"
}
"""

@app.route("/preguntar", methods=["POST"])
def preguntar():
    try:
        data = request.json
        pregunta = data.get('pregunta', 'Genera algo sorprendente')
        
        # Construcción del mensaje enviado a la IA
        full_prompt = f"{SISTEMA_PROMPT}\n\nComando del Usuario: {pregunta}"
        
        response = model.generate_content(full_prompt)
        
        # --- LIMPIEZA DE SEGURIDAD PARA JSON ---
        raw_text = response.text.strip()
        
        # Eliminar posibles etiquetas de bloques de código markdown
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]
        raw_text = raw_text.strip()

        # Intentamos parsear para asegurar que el JSON sea válido antes de enviarlo
        try:
            cleaned_json = json.loads(raw_text)
            return jsonify(cleaned_json), 200
        except json.JSONDecodeError as je:
            print(f"Error de parsing: {je}")
            # Si falla el parseo, intentamos devolver el texto crudo pero con aviso
            return jsonify({
                "error": "Respuesta malformada", 
                "detalles": str(je),
                "raw": raw_text[:500]
            }), 500

    except Exception as e:
        print(f"Error General: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    # Render usa la variable PORT, si no usamos 10000 por defecto
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
