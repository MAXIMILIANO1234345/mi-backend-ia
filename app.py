import os
import json
import re
import google.generativeai as genai
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from supabase import create_client, Client

# --- 1. CONFIGURACIÓN ---
print("--- Iniciando CEREBRO ORQUESTADOR (Modo Estrella) ---")
load_dotenv()

GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

if not all([GOOGLE_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
    raise ValueError("⚠️ Faltan variables de entorno (.env)")

# Configuración de Clientes
genai.configure(api_key=GOOGLE_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# CONSTANTES DEL ORQUESTADOR
ORQUESTADOR_ID = 1 # Asumimos que somos el "Alpha" (ID 1)
EMBEDDING_MODEL = "models/text-embedding-004"
GENERATIVE_MODEL = "models/gemini-2.5-flash" 

app = Flask(__name__)
CORS(app)

# --- 2. CACHE DE PILARES (Memoria de Trabajo) ---
# Al iniciar, cargamos el mapa del círculo para no consultar SQL a cada rato
CATALOGO_PILARES = {} 

def cargar_catalogo():
    """Descarga el mapa mental del Orquestador desde Supabase."""
    global CATALOGO_PILARES
    try:
        # Traemos solo los pilares de ESTE orquestador
        response = supabase.table('catalogo_pilares')\
            .select('nombre_clave, nombre_tabla, descripcion')\
            .eq('orquestador_id', ORQUESTADOR_ID)\
            .execute()
        
        if response.data:
            CATALOGO_PILARES = {item['nombre_clave']: item for item in response.data}
            print(f"✅ Catálogo cargado: {len(CATALOGO_PILARES)} pilares disponibles.")
        else:
            print("⚠️ ADVERTENCIA: El catálogo está vacío. Ejecuta el SQL de setup.")
    except Exception as e:
        print(f"❌ Error cargando catálogo: {e}")

# Cargar al inicio
cargar_catalogo()

# --- 3. UTILIDADES ---

def limpiar_json(texto):
    """Limpia respuestas del LLM para obtener JSON puro."""
    texto = texto.strip()
    # Eliminar bloques de código markdown
    texto = re.sub(r'^```json\s*', '', texto)
    texto = re.sub(r'^```\s*', '', texto)
    texto = re.sub(r'\s*```$', '', texto)
    return texto.strip()

def get_embedding(text):
    """Vectorización estándar."""
    try:
        res = genai.embed_content(model=EMBEDDING_MODEL, content=text, task_type="RETRIEVAL_QUERY")
        return res['embedding']
    except Exception as e:
        print(f"❌ Error vectorizando: {e}")
        return None

# --- 4. LÓGICA DEL ORQUESTADOR (EL CEREBRO) ---

def planificar_busqueda(pregunta):
    """
    Paso 1: El Orquestador decide DÓNDE buscar.
    No buscamos en todas las tablas, solo en las relevantes.
    """
    modelo = genai.GenerativeModel(GENERATIVE_MODEL)
    
    # Crear lista legible para el prompt
    lista_pilares = "\n".join([f"- {k}: {v['descripcion']}" for k, v in CATALOGO_PILARES.items()])
    
    prompt = f"""
    Eres el Orquestador de una Base de Datos de Conocimiento de Blender.
    
    PREGUNTA DEL USUARIO: "{pregunta}"
    
    TU MEMORIA ESTÁ DIVIDIDA EN ESTAS BÓVEDAS (TABLAS):
    {lista_pilares}
    
    TAREA:
    Identifica en qué bóveda(s) (1 o 2 máximo) es más probable encontrar la respuesta.
    Si la pregunta es muy general, elige 'logica_ia' o 'api'.
    
    RESPONDE SOLO JSON:
    {{ "pilares_seleccionados": ["nombre_clave_1", "nombre_clave_2"] }}
    """
    
    try:
        res = modelo.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        data = json.loads(limpiar_json(res.text))
        return data.get("pilares_seleccionados", [])
    except:
        return ["api"] # Fallback seguro

def consultar_memoria(pilares_objetivo, vector_pregunta):
    """
    Paso 2: Ejecuta 'cerebro_recordar' en las tablas seleccionadas.
    """
    hallazgos = []
    
    for clave in pilares_objetivo:
        if clave not in CATALOGO_PILARES: continue
        
        tabla_real = CATALOGO_PILARES[clave]['nombre_tabla']
        print(f"🧠 Consultando memoria: {tabla_real}...")
        
        try:
            # Llamada a la RPC centralizada del SQL
            response = supabase.rpc('cerebro_recordar', {
                'p_orquestador_id': ORQUESTADOR_ID,
                'p_tabla_destino': tabla_real,
                'p_vector': vector_pregunta,
                'p_umbral': 0.4, # Umbral de similitud
                'p_limite': 3
            }).execute()
            
            if response.data:
                for item in response.data:
                    hallazgos.append(f"[{clave.upper()}] Concepto: {item['concepto']}\nDetalle: {item['detalle']}\nSimilitud: {item['similitud']:.2f}")
                    
        except Exception as e:
            print(f"⚠️ Error leyendo {tabla_real}: {e}")
            
    return hallazgos

def aprender_y_guardar(pregunta):
    """
    Paso 3 (CRÍTICO): Si no sabemos la respuesta, INVESTIGAMOS y APRENDEMOS.
    """
    print("🌐 Modo Aprendizaje Activado: Buscando información externa...")
    modelo = genai.GenerativeModel(GENERATIVE_MODEL)
    
    # 1. INVESTIGAR (Usamos Gemini con herramienta de búsqueda si disponible, o simulación)
    # Prompt diseñado para extraer información estructurada de su conocimiento base + búsqueda
    prompt_investigacion = f"""
    El usuario pregunta: "{pregunta}".
    No tengo esta información en mi base de datos local.
    
    Por favor, responde a la pregunta con tu mejor conocimiento experto en Blender y Python.
    Sé técnico, preciso y da ejemplos de código si aplica.
    """
    
    res_investigacion = modelo.generate_content(prompt_investigacion)
    info_nueva = res_investigacion.text
    
    # 2. CLASIFICAR Y ESTRUCTURAR (ETL)
    # Ahora que tenemos la info, el Orquestador debe decidir dónde guardarla.
    lista_pilares = "\n".join([f"- {k}: {v['descripcion']}" for k, v in CATALOGO_PILARES.items()])
    
    prompt_clasificacion = f"""
    ANALIZA ESTA INFORMACIÓN NUEVA:
    "{info_nueva}"
    
    TU CATÁLOGO DE MEMORIA:
    {lista_pilares}
    
    TAREA:
    1. Resume el concepto clave.
    2. Extrae el detalle técnico/código.
    3. Decide en QUÉ tabla (nombre_clave) debe guardarse.
    
    JSON OBLIGATORIO:
    {{
        "tabla_destino": "nombre_clave_del_catalogo",
        "concepto": "Título corto",
        "detalle_tecnico": "Explicación técnica resumida",
        "codigo_ejemplo": "snippet de codigo o null"
    }}
    """
    
    try:
        res_clasif = modelo.generate_content(prompt_clasificacion, generation_config={"response_mime_type": "application/json"})
        datos_aprendizaje = json.loads(limpiar_json(res_clasif.text))
        
        clave_destino = datos_aprendizaje.get("tabla_destino")
        
        if clave_destino in CATALOGO_PILARES:
            tabla_real = CATALOGO_PILARES[clave_destino]['nombre_tabla']
            
            # 3. GUARDAR (RPC cerebro_aprender)
            vec_nuevo = get_embedding(f"{datos_aprendizaje['concepto']} {datos_aprendizaje['detalle_tecnico']}")
            
            supabase.rpc('cerebro_aprender', {
                'p_orquestador_id': ORQUESTADOR_ID,
                'p_tabla_destino': tabla_real,
                'p_concepto': datos_aprendizaje['concepto'],
                'p_detalle': datos_aprendizaje['detalle_tecnico'],
                'p_codigo': datos_aprendizaje.get('codigo_ejemplo', ''),
                'p_vector': vec_nuevo
            }).execute()
            
            print(f"💾 CONOCIMIENTO GUARDADO en {tabla_real}: {datos_aprendizaje['concepto']}")
            return info_nueva + "\n\n(Nota: Acabo de aprender esto y lo he guardado en mi memoria de " + clave_destino + ")."
            
        else:
            return info_nueva + "\n(Nota: No supe dónde clasificar esto, pero aquí tienes la respuesta)."
            
    except Exception as e:
        print(f"❌ Error en aprendizaje: {e}")
        return info_nueva # Devolvemos la info aunque falle el guardado

# --- 5. ENDPOINTS ---

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "online", "mode": "Orquestador Estrella Centralizada"}), 200

@app.route("/preguntar", methods=["POST"])
def endpoint_preguntar():
    data = request.json
    pregunta = data.get('pregunta', '')
    
    if not pregunta: return jsonify({"error": "Pregunta vacía"}), 400
    
    print(f"\n📨 Nueva solicitud: '{pregunta}'")
    
    # 1. PLANIFICACIÓN
    pilares_target = planificar_busqueda(pregunta)
    print(f"🎯 Estrategia: Buscar en {pilares_target}")
    
    # 2. EJECUCIÓN (Búsqueda interna)
    vector = get_embedding(pregunta)
    contexto = consultar_memoria(pilares_target, vector)
    
    # 3. EVALUACIÓN
    if contexto:
        print(f"✅ Encontrado en memoria interna ({len(contexto)} registros).")
        # Generar respuesta con RAG
        modelo = genai.GenerativeModel(GENERATIVE_MODEL)
        prompt = f"""
        CONTEXTO DE TU MEMORIA (BASED ON SQL):
        {chr(10).join(contexto)}
        
        PREGUNTA: {pregunta}
        
        Responde usando el contexto. Si hay código, úsalo. Formato JSON.
        {{ "respuesta": "...", "codigo": "..." }}
        """
        res = modelo.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        return jsonify(json.loads(limpiar_json(res.text)))
        
    else:
        print("Empty - 🤷 No sé la respuesta. Iniciando protocolo de APRENDIZAJE...")
        # 4. APRENDIZAJE (Si falla la memoria interna)
        respuesta_aprendida = aprender_y_guardar(pregunta)
        
        return jsonify({
            "respuesta": respuesta_aprendida,
            "fuente": "Investigación en Tiempo Real (Nuevo Conocimiento)",
            "estado": "Aprendido y Guardado"
        })

if __name__ == "__main__":
    app.run(debug=True, port=5000)
