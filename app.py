import os
import json
import re  # IMPORTANTE: Para la limpieza avanzada de JSON
import google.generativeai as genai
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from supabase import create_client, Client

# --- 1. CONFIGURACIÓN ---
print("--- Iniciando API Proyecto 17 (Modo Debug & Robust) ---")
load_dotenv()

GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

if not all([GOOGLE_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
    raise ValueError("⚠️ Faltan variables de entorno (.env)")

# Configuración de Clientes
genai.configure(api_key=GOOGLE_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# MODELOS
# Usa 'text-embedding-004' para vectores (768 dim)
# Usa 'gemini-1.5-flash' para respuestas rápidas
EMBEDDING_MODEL = "models/text-embedding-004"
GENERATIVE_MODEL = "models/gemini-1.5-flash" 

app = Flask(__name__)
CORS(app)

# --- 2. FUNCIONES DE LIMPIEZA JSON (BLINDAJE) ---

def extraer_json_con_regex(texto):
    """
    Busca el primer bloque que parezca un objeto JSON { ... }
    dentro del texto, ignorando la basura al inicio o final.
    """
    try:
        # Busca desde la primera { hasta la última }
        match = re.search(r'(\{.*\})', texto, re.DOTALL)
        if match:
            return match.group(1)
        return texto
    except:
        return texto

def limpiar_respuesta_json(texto_bruto):
    """Limpia etiquetas Markdown comunes."""
    texto = texto_bruto.strip()
    if texto.startswith("```json"): texto = texto[7:]
    elif texto.startswith("```"): texto = texto[3:]
    if texto.endswith("```"): texto = texto[:-3]
    return texto.strip()

# --- 3. FUNCIONES DE IA (RAG) ---

def get_text_embedding(text):
    """Genera vector optimizado para BÚSQUEDA."""
    try:
        result = genai.embed_content(
            model=EMBEDDING_MODEL,
            content=text,
            task_type="RETRIEVAL_QUERY" 
        )
        return result['embedding']
    except Exception as e:
        print(f"❌ Error vectorizando: {e}")
        return None

def buscar_contexto_rag(pregunta_usuario):
    # 1. Vectorizar
    vector_busqueda = get_text_embedding(pregunta_usuario)
    if not vector_busqueda:
        return [], []

    nodos_encontrados = []
    
    try:
        # 2. RPC a Supabase
        # Umbral 0.25 para asegurar que traiga datos aunque no sean exactos
        print(f"🔍 Buscando en BD: '{pregunta_usuario}'")
        response = supabase.rpc('buscar_nodos', {
            'query_embedding': vector_busqueda,
            'match_threshold': 0.25,
            'match_count': 10
        }).execute()
        
        nodos_encontrados = response.data or []

        # --- DEBUG LOGS ---
        print(f"📊 Nodos recuperados: {len(nodos_encontrados)}")
        for n in nodos_encontrados:
            similitud = n.get('similitud', 0) # Si falla la clave, usa 0
            print(f"   -> [{n.get('id')}] {n.get('nombre')} (Similitud: {similitud:.4f})")
        print("--------------------")
        # ------------------

    except Exception as e:
        print(f"❌ Error SQL/RPC: {e}")
        return [], []

    # 3. Traer Relaciones
    relaciones_contexto = []
    if nodos_encontrados:
        ids_nodos = [n['id'] for n in nodos_encontrados]
        try:
            rel_response = supabase.table('relaciones')\
                .select('relacion, origen_id, destino_id, nodo_origen:nodos!origen_id(nombre), nodo_destino:nodos!destino_id(nombre, descripcion)')\
                .in_('origen_id', ids_nodos)\
                .limit(15)\
                .execute()
            
            if rel_response.data:
                for r in rel_response.data:
                    origen = r['nodo_origen']['nombre'] if r['nodo_origen'] else "X"
                    destino = r['nodo_destino']['nombre'] if r['nodo_destino'] else "Y"
                    relaciones_contexto.append(f"{origen} --[{r['relacion']}]--> {destino}")
        except Exception as e:
            print(f"⚠️ Error relaciones: {e}")

    return nodos_encontrados, relaciones_contexto

def generar_respuesta(pregunta, nodos, relaciones):
    modelo = genai.GenerativeModel(GENERATIVE_MODEL)

    # Formatear contexto para el prompt
    txt_nodos = "\n".join([f"- {n.get('nombre')}: {n.get('descripcion')}" for n in nodos])
    txt_rels = "\n".join(relaciones)
    
    fuente_str = "Grafo" if nodos else "Conocimiento General"

    prompt = f"""
    Eres un experto en Blender. Responde a la pregunta del usuario.

    CONTEXTO RECUPERADO (Base de Datos):
    {txt_nodos}
    
    RELACIONES:
    {txt_rels}

    PREGUNTA: "{pregunta}"

    INSTRUCCIONES:
    1. Si el contexto tiene la respuesta, ÚSALO.
    2. Si no, responde con tu conocimiento general pero empieza diciendo "Nota: No encontré información en tu base de datos, pero...".
    3. Responde ESTRICTAMENTE en JSON. No hables antes ni después del JSON.

    FORMATO JSON:
    {{
        "respuesta_principal": "Texto de la respuesta...",
        "puntos_clave": [
            {{ "titulo": "Concepto", "descripcion": "Detalle breve" }}
        ],
        "fuente": "{fuente_str}"
    }}
    """

    try:
        # Configuración para forzar JSON
        res = modelo.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        texto_generado = res.text
        
        # --- ESTRATEGIA DE DEFENSA CONTRA ERRORES JSON ---
        try:
            # Intento 1: Limpieza básica
            return json.loads(limpiar_respuesta_json(texto_generado))
        except json.JSONDecodeError:
            print("⚠️ JSON sucio detectado, aplicando Regex...")
            # Intento 2: Extracción quirúrgica con Regex
            json_str = extraer_json_con_regex(texto_generado)
            return json.loads(json_str)

    except Exception as e:
        print(f"❌ Error fatal generando respuesta: {e}")
        # Retorno de emergencia para que el Frontend no explote
        return {
            "respuesta_principal": "Hubo un error técnico procesando la respuesta. Por favor intenta reformular la pregunta.",
            "puntos_clave": [],
            "fuente": "Error de Sistema"
        }

# --- 4. ENDPOINTS ---

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "online", "mode": "Robust GraphRAG"}), 200

@app.route("/preguntar", methods=["POST"])
def endpoint_preguntar():
    data = request.json
    pregunta = data.get('pregunta', '')
    
    if not pregunta:
        return jsonify({"respuesta_principal": "Por favor escribe una pregunta.", "puntos_clave": []})

    # RAG
    nodos, relaciones = buscar_contexto_rag(pregunta)
    
    # Generación
    respuesta = generar_respuesta(pregunta, nodos, relaciones)
    
    return jsonify(respuesta)

@app.route("/generar-script", methods=["POST"])
def endpoint_script():
    # Endpoint simplificado para scripts
    data = request.json
    pregunta = data.get('pregunta', '')
    modelo = genai.GenerativeModel(GENERATIVE_MODEL)
    
    try:
        prompt = f'Genera script Python Blender (bpy) para: "{pregunta}". JSON: {{ "script": "import bpy..." }}'
        res = modelo.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        return jsonify(json.loads(limpiar_respuesta_json(res.text)))
    except:
        return jsonify({"script": "# Error generando script"})

if __name__ == "__main__":
    app.run(debug=True, port=5000)
