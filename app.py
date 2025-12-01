import os
import json
import re
import time
import threading
import google.generativeai as genai
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from supabase import create_client, Client

# --- 1. CONFIGURACIÓN ---
print("--- Iniciando SISTEMA DE INTELIGENCIA AUTÓNOMA (V8: Lab + Flow) ---")
load_dotenv()

GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

if not all([GOOGLE_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
    raise ValueError("⚠️ Faltan variables de entorno.")

genai.configure(api_key=GOOGLE_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

ORQUESTADOR_ID = 1 
EMBEDDING_MODEL = "models/text-embedding-004"
GENERATIVE_MODEL = "models/gemini-2.5-flash" 

# CONFIGURACIÓN DE AUTO-MEJORA
MODO_AUTONOMO_ACTIVO = True
TIEMPO_ENTRE_CICLOS = 20  # Segundos entre experimentos del laboratorio

app = Flask(__name__)
CORS(app)

# --- CACHE DE PILARES ---
CATALOGO_PILARES = {} 

def cargar_catalogo():
    global CATALOGO_PILARES
    try:
        response = supabase.table('catalogo_pilares')\
            .select('nombre_clave, nombre_tabla, descripcion, criterio_admision')\
            .eq('orquestador_id', ORQUESTADOR_ID)\
            .execute()
        
        if response.data:
            CATALOGO_PILARES = {
                item['nombre_clave']: {
                    **item,
                    'criterio_admision': item.get('criterio_admision') or "Información relevante."
                } 
                for item in response.data
            }
            print(f"✅ Catálogo cargado: {len(CATALOGO_PILARES)} pilares.")
    except Exception as e:
        print(f"❌ Error catálogo: {e}")

cargar_catalogo()

# --- UTILIDADES ---
def limpiar_json(texto):
    texto = texto.strip()
    texto = re.sub(r'^```json\s*', '', texto)
    texto = re.sub(r'^```\s*', '', texto)
    texto = re.sub(r'\s*```$', '', texto)
    return texto.strip()

def get_embedding(text):
    try:
        res = genai.embed_content(model=EMBEDDING_MODEL, content=text, task_type="RETRIEVAL_QUERY")
        return res['embedding']
    except:
        return None

# ==============================================================================
# 🧬 EL CIENTÍFICO AUTÓNOMO (HILO DE SEGUNDO PLANO)
# ==============================================================================

def auditoria_sistema():
    """Escanea la BD para encontrar el pilar más débil."""
    stats = {}
    for clave, data in CATALOGO_PILARES.items():
        try:
            res = supabase.table(data['nombre_tabla']).select('id', count='exact').execute()
            stats[clave] = res.count
        except:
            stats[clave] = 0
    
    # Devuelve el pilar con menos datos
    if not stats: return "api"
    return min(stats, key=stats.get)

def generar_curriculum(pilar_objetivo):
    """Inventa un tema técnico que falte en ese pilar."""
    info = CATALOGO_PILARES.get(pilar_objetivo)
    if not info: return "Conceptos avanzados de Blender"
    
    modelo = genai.GenerativeModel(GENERATIVE_MODEL)
    prompt = f"""
    ERES EL ADMINISTRADOR DE UNA BASE DE DATOS DE BLENDER.
    PILAR DÉBIL: "{info['nombre_clave']}" ({info['descripcion']}).
    
    Genera UN SOLO tema técnico específico y avanzado que falte.
    Ejemplo: "Optimización de Light Bounces en Cycles".
    Solo el título.
    """
    try:
        return modelo.generate_content(prompt).text.strip()
    except:
        return "Trucos avanzados de Python en Blender"

def investigar_tema(tema):
    """Investiga un tema usando herramientas o conocimiento interno."""
    prompt = f"""
    ACTÚA COMO EXPERTO TÉCNICO EN BLENDER.
    TEMA: "{tema}"
    
    Genera una explicación técnica densa y UN SCRIPT DE PYTHON (bpy) funcional.
    Prioriza documentación oficial.
    """
    try:
        # Intento con búsqueda
        tools = [{"google_search": {}}]
        mod = genai.GenerativeModel(GENERATIVE_MODEL, tools=tools)
        return mod.generate_content(prompt).text
    except:
        # Fallback conocimiento interno
        mod = genai.GenerativeModel(GENERATIVE_MODEL)
        return mod.generate_content(prompt).text

def ciclo_vida_autonomo():
    """El bucle infinito de auto-mejora."""
    print("🤖 [AUTO] Científico Autónomo iniciado...")
    
    while True:
        if MODO_AUTONOMO_ACTIVO:
            try:
                # 1. BUSCAR TAREA EN EL LABORATORIO
                res = supabase.table('laboratorio_ideas')\
                    .select('*')\
                    .in_('estado', ['borrador', 'rechazado'])\
                    .limit(1)\
                    .execute()
                
                tarea = res.data[0] if res.data else None
                
                if tarea:
                    # --- FASE DE EXPERIMENTACIÓN ---
                    print(f"🧪 [LAB] Procesando: {tarea['tema_objetivo']} (Intento {tarea['intentos']})")
                    
                    contenido = investigar_tema(tarea['tema_objetivo'])
                    
                    # --- FASE DE JUICIO (CRÍTICA) ---
                    mod_juez = genai.GenerativeModel(GENERATIVE_MODEL)
                    evaluacion = json.loads(limpiar_json(mod_juez.generate_content(
                        f"Evalúa este contenido técnico Blender:\n{contenido}\nJSON: {{aprobado: bool, critica: str, codigo_detectado: str}}",
                        generation_config={"response_mime_type": "application/json"}
                    ).text))
                    
                    if evaluacion.get('aprobado'):
                        # --- GRADUACIÓN ---
                        print(f"🎓 [LAB] Aprobado. Graduando a memoria real...")
                        
                        # Obtener tabla real
                        pilar_info = CATALOGO_PILARES.get(tarea['pilar_destino'])
                        if pilar_info:
                            tabla_real = pilar_info['nombre_tabla']
                            vec = get_embedding(f"{tarea['tema_objetivo']} {contenido}")
                            
                            # Insertar en memoria real
                            supabase.rpc('cerebro_aprender', {
                                'p_orquestador_id': ORQUESTADOR_ID,
                                'p_tabla_destino': tabla_real,
                                'p_concepto': tarea['tema_objetivo'],
                                'p_detalle': contenido,
                                'p_codigo': evaluacion.get('codigo_detectado', ''),
                                'p_vector': vec
                            }).execute()
                            
                            # Borrar del laboratorio
                            supabase.table('laboratorio_ideas').delete().eq('id', tarea['id']).execute()
                        else:
                            print("⚠️ Error: Pilar destino no encontrado.")
                    else:
                        # --- RECHAZO ---
                        print(f"⚠️ [LAB] Rechazado: {evaluacion.get('critica')}")
                        supabase.table('laboratorio_ideas').update({
                            'estado': 'rechazado',
                            'critica_ia': evaluacion.get('critica'),
                            'intentos': tarea['intentos'] + 1
                        }).eq('id', tarea['id']).execute()
                
                else:
                    # --- FASE DE GENERACIÓN DE HIPÓTESIS (Si el lab está vacío) ---
                    print("💡 [LAB] Laboratorio vacío. Buscando nuevos temas...")
                    pilar_debil = auditoria_sistema()
                    nuevo_tema = generar_curriculum(pilar_debil)
                    
                    supabase.table('laboratorio_ideas').insert({
                        'orquestador_id': ORQUESTADOR_ID,
                        'tema_objetivo': nuevo_tema,
                        'pilar_destino': pilar_debil,
                        'estado': 'borrador'
                    }).execute()
                    print(f"✨ [LAB] Hipótesis creada: {nuevo_tema}")
                    
            except Exception as e:
                print(f"❌ [AUTO] Error ciclo: {e}")
        
        time.sleep(TIEMPO_ENTRE_CICLOS)

# ARRANCAR EL HILO DE FONDO
threading.Thread(target=ciclo_vida_autonomo, daemon=True).start()


# ==============================================================================
# API PÚBLICA (INTERACCIÓN CON USUARIO)
# ==============================================================================

def filtro_especialidad(pregunta):
    mod = genai.GenerativeModel(GENERATIVE_MODEL)
    try:
        res = mod.generate_content(
            f"Filtro Blender 3D. Pregunta: '{pregunta}'. JSON {{es_relevante: bool, razon: str}}",
            generation_config={"response_mime_type": "application/json"}
        )
        return json.loads(limpiar_json(res.text))
    except: return {"es_relevante": True}

def planificar_busqueda(pregunta):
    mod = genai.GenerativeModel(GENERATIVE_MODEL)
    pilares = "\n".join([f"- {k}: {v['descripcion']}" for k,v in CATALOGO_PILARES.items()])
    try:
        res = mod.generate_content(
            f"Pregunta: {pregunta}\nTablas:\n{pilares}\nJSON {{pilares_seleccionados: [str]}}",
            generation_config={"response_mime_type": "application/json"}
        )
        return json.loads(limpiar_json(res.text)).get("pilares_seleccionados", [])
    except: return ["api"]

def consultar_memoria_flow(pilares, vector):
    hallazgos = []
    for clave in pilares:
        if clave not in CATALOGO_PILARES: continue
        tabla = CATALOGO_PILARES[clave]['nombre_tabla']
        try:
            res = supabase.rpc('cerebro_recordar_flow', {
                'p_orquestador_id': ORQUESTADOR_ID, 'p_tabla_destino': tabla,
                'p_vector': vector, 'p_umbral': 0.35, 'p_limite': 5
            }).execute()
            if res.data:
                for item in res.data:
                    hallazgos.append(f"[{clave.upper()}] {item['concepto']}: {item['detalle']}")
        except: pass
    return hallazgos

def evaluar_suficiencia(pregunta, contexto):
    if not contexto: return False, "Vacio"
    mod = genai.GenerativeModel(GENERATIVE_MODEL)
    try:
        res = mod.generate_content(
            f"Pregunta: {pregunta}\nContexto: {contexto}\n¿Suficiente? JSON {{es_suficiente: bool, razon: str}}",
            generation_config={"response_mime_type": "application/json"}
        )
        d = json.loads(limpiar_json(res.text))
        return d.get("es_suficiente", False), d.get("razon", "")
    except: return False, "Error"

def aprender_usuario(pregunta, contexto_parcial):
    """Aprendizaje disparado por el usuario (Prioridad Alta)"""
    print(f"🌐 [USER] Investigando: {pregunta}")
    contenido = investigar_tema(pregunta)
    
    # Clasificar y guardar
    try:
        criterios = "\n".join([f"- {k}: {v.get('criterio_admision')}" for k,v in CATALOGO_PILARES.items()])
        mod = genai.GenerativeModel(GENERATIVE_MODEL)
        datos = json.loads(limpiar_json(mod.generate_content(
            f"Clasifica: {contenido}\nCriterios: {criterios}\nJSON {{tabla_destino: str|null, concepto: str, detalle: str, codigo: str}}",
            generation_config={"response_mime_type": "application/json"}
        ).text))
        
        if datos.get("tabla_destino") in CATALOGO_PILARES:
            tabla = CATALOGO_PILARES[datos["tabla_destino"]]['nombre_tabla']
            vec = get_embedding(f"{datos['concepto']} {datos['detalle']}")
            supabase.rpc('cerebro_aprender', {
                'p_orquestador_id': ORQUESTADOR_ID, 'p_tabla_destino': tabla,
                'p_concepto': datos['concepto'], 'p_detalle': datos['detalle'],
                'p_codigo': datos.get('codigo', ''), 'p_vector': vec
            }).execute()
            print(f"💾 [USER] Guardado en {tabla}")
    except Exception as e:
        print(f"❌ Error guardado usuario: {e}")
        
    return contenido

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "Active", "mode": "Autonomous Lab V8"}), 200

@app.route("/preguntar", methods=["POST"])
def endpoint_preguntar():
    data = request.json
    pregunta = data.get('pregunta', '')
    if not pregunta: return jsonify({"error": "Vacio"}), 400
    
    print(f"\n📨 Usuario: {pregunta}")
    
    # 1. Filtro
    analisis = filtro_especialidad(pregunta)
    if not analisis.get("es_relevante", True):
        return jsonify({"respuesta_principal": "Solo Blender/3D.", "puntos_clave": [], "fuente": "Filtro"})
        
    # 2. Memoria
    pilares = planificar_busqueda(pregunta)
    contexto = consultar_memoria_flow(pilares, get_embedding(pregunta))
    
    # 3. Suficiencia
    suficiente, razon = False, ""
    if contexto: suficiente, razon = evaluar_suficiencia(pregunta, contexto)
    
    # 4. Respuesta
    mod = genai.GenerativeModel(GENERATIVE_MODEL)
    if suficiente:
        txt = mod.generate_content(f"Responde experto con: {contexto}. Pregunta: {pregunta}").text
        src = "Memoria Experta"
    else:
        txt = aprender_usuario(pregunta, contexto)
        src = "Investigación Activa"
        
    # 5. Formato JSON
    try:
        res = mod.generate_content(
            f"JSON Frontend:\nTEXTO:{txt}\nFUENTE:{src}\nJSON:{{respuesta_principal: str, puntos_clave: [{{titulo:str, descripcion:str}}], fuente: str}}",
            generation_config={"response_mime_type": "application/json"}
        )
        return jsonify(json.loads(limpiar_json(res.text)))
    except:
        return jsonify({"respuesta_principal": txt, "puntos_clave": [], "fuente": src})

if __name__ == "__main__":
    app.run(debug=True, port=5000)
