import os
import json
import google.generativeai as genai
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from supabase import create_client, Client

# --- 1. CONFIGURACIÓN ---
print("--- Iniciando API Proyecto 17 (Modo Debug) ---")
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
# Usamos el modelo 004 que es el estándar actual. Asegúrate de insertar datos con este mismo.
EMBEDDING_MODEL = "models/text-embedding-004"
GENERATIVE_MODEL = "models/gemini-1.5-flash" 

app = Flask(__name__)
CORS(app)

# --- 2. FUNCIONES DE AYUDA ---

def get_text_embedding(text):
    """Genera vector de 768 dimensiones optimizado para CONSULTAS."""
    try:
        result = genai.embed_content(
            model=EMBEDDING_MODEL,
            content=text,
            task_type="RETRIEVAL_QUERY" # Optimizado para preguntar
        )
        return result['embedding']
    except Exception as e:
        print(f"❌ Error generando embedding: {e}")
        return None

def limpiar_respuesta_json(texto_bruto):
    """Limpia el markdown de la respuesta de Gemini."""
    texto = texto_bruto.strip()
    if texto.startswith("```json"): texto = texto[7:]
    elif texto.startswith("```"): texto = texto[3:]
    if texto.endswith("```"): texto = texto[:-3]
    return texto.strip()

# --- 3. LÓGICA RAG (Con Logs de Depuración) ---

def buscar_contexto_rag(pregunta_usuario):
    # 1. Vectorizar
    vector_busqueda = get_text_embedding(pregunta_usuario)
    if not vector_busqueda:
        return [], []

    nodos_encontrados = []
    
    try:
        # 2. Búsqueda en Supabase
        # ATENCIÓN: Bajamos el threshold a 0.25 para que encuentre SÍ o SÍ
        print(f"🔍 Buscando: '{pregunta_usuario}'...")
        response = supabase.rpc('buscar_nodos', {
            'query_embedding': vector_busqueda,
            'match_threshold': 0.25,  # <--- UMBRAL BAJO PARA ASEGURAR RESULTADOS
            'match_count': 10
        }).execute()
        
        nodos_encontrados = response.data or []

        # --- DEBUG LOGS (Mira esto en tu terminal) ---
        print(f"📊 Resultados encontrados: {len(nodos_encontrados)}")
        for n in nodos_encontrados:
            # Imprimimos qué encontró y qué tan seguro está (0 a 1)
            print(f"   -> ID: {n.get('id')} | {n.get('nombre')} | Similitud: {n.get('similitud', 0):.4f}")
        print("------------------------------------------------")
        # ---------------------------------------------

    except Exception as e:
        print(f"❌ Error CRÍTICO en RPC buscar_nodos: {e}")
        return [], []

    # 3. Traer Relaciones (Expandir contexto)
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
                    origen = r['nodo_origen']['nombre'] if r['nodo_origen'] else "Origen"
                    destino = r['nodo_destino']['nombre'] if r['nodo_destino'] else "Destino"
                    tipo = r['relacion']
                    relaciones_contexto.append(f"{origen} --[{tipo}]--> {destino}")
        except Exception as e:
            print(f"⚠️ Error recuperando relaciones: {e}")

    return nodos_encontrados, relaciones_contexto

def generar_respuesta(pregunta, nodos, relaciones):
    modelo = genai.GenerativeModel(GENERATIVE_MODEL)

    # Preparar texto para el prompt
    txt_nodos = "\n".join([f"- {n['nombre']}: {n['descripcion']}" for n in nodos])
    txt_rels = "\n".join(relaciones)
    
    # Si no hay nodos, avisamos en el prompt pero dejamos que Gemini intente responder si es algo obvio
    aviso_vacio = ""
    if not nodos:
        aviso_vacio = "ADVERTENCIA: No se encontró información en la base de datos. Si respondes, aclara que es conocimiento general."

    prompt = f"""
    Eres un Asistente Experto en Blender 3D.
    
    PREGUNTA: "{pregunta}"
    
    --- INFORMACIÓN DE LA BASE DE DATOS (PRIORIDAD MÁXIMA) ---
    {txt_nodos}
    
    CONEXIONES:
    {txt_rels}
    ----------------------------------------------------------
    {aviso_vacio}

    Instrucciones:
    1. Si la información está arriba, ÚSALA. No la ignores.
    2. Si la información arriba es escasa, compleméntala con tu conocimiento, pero prioriza lo que leíste arriba.
    3. Si NO hay información arriba, responde: "Nota: No encontré información específica en tu base de datos (Grafo), pero..." y responde lo mejor que puedas.
    
    Responde SOLAMENTE en formato JSON:
    {{
        "respuesta_principal": "Texto de la respuesta...",
        "puntos_clave": [
            {{ "titulo": "Concepto", "descripcion": "Detalle..." }}
        ],
        "fuente": "Grafo" (si usaste los datos) o "Conocimiento General"
    }}
    """

    try:
        res = modelo.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        return json.loads(limpiar_respuesta_json(res.text))
    except Exception as e:
        return {
            "respuesta_principal": "Error generando respuesta.",
            "puntos_clave": [],
            "fuente": "Error de Sistema"
        }

# --- ENDPOINTS ---

@app.route("/preguntar", methods=["POST"])
def endpoint_preguntar():
    data = request.json
    pregunta = data.get('pregunta', '')
    
    # 1. Ejecutar RAG
    nodos, relaciones = buscar_contexto_rag(pregunta)
    
    # 2. Generar Respuesta
    # Determinamos la fuente para enviarla al prompt o frontend
    respuesta = generar_respuesta(pregunta, nodos, relaciones)
    
    # Doble chequeo de la fuente
    if not nodos and respuesta.get("fuente") == "Grafo":
        respuesta["fuente"] = "Conocimiento General"

    return jsonify(respuesta)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
