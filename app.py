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
print("--- Iniciando CEREBRO ORQUESTADOR (V10: Curriculum Learning) ---")
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
TIEMPO_ENTRE_CICLOS = 30 # Un poco más lento para pensar mejor

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
        
        data_raw = response.data
        if data_raw:
            CATALOGO_PILARES = {
                item['nombre_clave']: {
                    **item,
                    'criterio_admision': item.get('criterio_admision') or "Información relevante."
                } 
                for item in data_raw
            }
            print(f"✅ Catálogo cargado: {len(CATALOGO_PILARES)} pilares.")
    except Exception as e:
        print(f"❌ Error catálogo: {e}")

cargar_catalogo()

# --- UTILIDADES BLINDADAS (FIX ERROR LISTAS) ---

def limpiar_json(texto):
    texto = texto.strip()
    texto = re.sub(r'^```json\s*', '', texto)
    texto = re.sub(r'^```\s*', '', texto)
    texto = re.sub(r'\s*```$', '', texto)
    return texto.strip()

def normalizar_respuesta_json(texto_json):
    """
    ARREGLO DEFINITIVO PARA 'list object has no attribute get'.
    Convierte listas [dict] en dict simples.
    """
    try:
        datos = json.loads(limpiar_json(texto_json))
        if isinstance(datos, list):
            if len(datos) > 0:
                return datos[0] # Tomamos el primer elemento
            else:
                return {} # Lista vacía
        return datos # Ya es diccionario
    except Exception as e:
        print(f"⚠️ Error parseando JSON: {e}")
        return {}

def get_embedding(text):
    try:
        res = genai.embed_content(model=EMBEDDING_MODEL, content=text, task_type="RETRIEVAL_QUERY")
        return res['embedding']
    except:
        return None

# ==============================================================================
# 🧬 EL JARDINERO CONSCIENTE (CURRICULUM LEARNING)
# ==============================================================================

def determinar_nivel_pilar(conteo):
    """Define la madurez de un pilar basado en la cantidad de datos."""
    if conteo < 5:
        return "NOVATO", "Busca definiciones fundamentales, conceptos básicos y 'Hola Mundo' del tema."
    elif conteo < 20:
        return "APRENDIZ", "Busca flujos de trabajo comunes, herramientas estándar y scripts sencillos."
    elif conteo < 50:
        return "PROFESIONAL", "Busca casos de uso específicos, solución de problemas y combinaciones de herramientas."
    else:
        return "EXPERTO", "Busca optimización de memoria, matemáticas vectoriales complejas, render engines internals y trucos oscuros."

def auditoria_consciente():
    """
    Escanea la BD, cuenta registros y determina el NIVEL de cada pilar.
    Retorna el pilar más débil y su nivel actual.
    """
    stats = {}
    
    print("🧠 [AUTO] Realizando auto-consciencia de conocimientos...")
    
    for clave, data in CATALOGO_PILARES.items():
        try:
            res = supabase.table(data['nombre_tabla']).select('id', count='exact').execute()
            stats[clave] = res.count
        except:
            stats[clave] = 0
            
    if not stats: return "api", "NOVATO", "Necesitamos empezar desde cero."
    
    # Encontrar el más débil
    pilar_debil = min(stats, key=stats.get)
    conteo = stats[pilar_debil]
    
    nivel, estrategia = determinar_nivel_pilar(conteo)
    
    print(f"🚨 [DIAGNÓSTICO] Pilar '{pilar_debil}' tiene solo {conteo} recuerdos.")
    print(f"   -> Nivel Detectado: {nivel}")
    
    return pilar_debil, nivel, estrategia

def generar_curriculum_adaptativo(pilar_objetivo, nivel, estrategia):
    """Genera un tema acorde al nivel de madurez."""
    info = CATALOGO_PILARES.get(pilar_objetivo)
    
    modelo = genai.GenerativeModel(GENERATIVE_MODEL)
    prompt = f"""
    ERES EL MAESTRO DE UNA IA DE BLENDER.
    
    CONTEXTO:
    Estamos mejorando el pilar: "{info['nombre_clave']}" ({info['descripcion']}).
    Nivel Actual de la IA: {nivel}.
    Estrategia Pedagógica: {estrategia}
    
    TAREA:
    Genera UN SOLO título de tema para estudiar ahora mismo.
    Debe ser estrictamente acorde al nivel.
    - Si es NOVATO: No pidas cosas complejas. Pide bases.
    - Si es EXPERTO: No pidas lo básico. Pide algo difícil.
    
    Responde SOLO el título del tema.
    """
    try:
        return modelo.generate_content(prompt).text.strip()
    except:
        return "Fundamentos de Blender Python"

def investigar_tema(tema, nivel):
    """Investiga ajustando la complejidad al nivel."""
    prompt = f"""
    ACTÚA COMO UN EXPERTO EN BLENDER.
    TEMA A ENSEÑAR: "{tema}"
    NIVEL DEL ESTUDIANTE: {nivel}
    
    Genera una explicación técnica y UN SCRIPT DE PYTHON (bpy).
    Si el nivel es NOVATO, explica paso a paso.
    Si el nivel es EXPERTO, sé conciso y ve al grano con código avanzado.
    """
    try:
        # Intento con búsqueda
        try:
            tools = [{"google_search": {}}]
            mod = genai.GenerativeModel(GENERATIVE_MODEL, tools=tools)
            return mod.generate_content(prompt).text
        except:
            # Fallback
            mod = genai.GenerativeModel(GENERATIVE_MODEL)
            return mod.generate_content(prompt).text
    except Exception as e:
        return f"Error investigando: {e}"

def ciclo_vida_autonomo():
    print("🤖 [AUTO] Sistema de Aprendizaje Adaptativo iniciado...")
    while True:
        if MODO_AUTONOMO_ACTIVO:
            try:
                # 1. VERIFICAR SI HAY TAREAS EN CURSO
                res = supabase.table('laboratorio_ideas').select('*').in_('estado', ['borrador', 'rechazado']).limit(1).execute()
                tarea = res.data[0] if res.data else None
                
                if tarea:
                    # PROCESAR TAREA EXISTENTE
                    print(f"🧪 [LAB] Procesando: {tarea['tema_objetivo']}")
                    
                    # Recuperamos el nivel (podríamos guardarlo en DB, pero lo inferimos por simplicidad o default)
                    nivel_actual = "INTERMEDIO" # Default para tareas ya creadas
                    
                    contenido = investigar_tema(tarea['tema_objetivo'], nivel_actual)
                    
                    # Juicio con Normalización JSON
                    mod_juez = genai.GenerativeModel(GENERATIVE_MODEL)
                    evaluacion = normalizar_respuesta_json(mod_juez.generate_content(
                        f"Evalúa este contenido Blender:\n{contenido}\nJSON: {{aprobado: bool, critica: str, codigo_detectado: str}}",
                        generation_config={"response_mime_type": "application/json"}
                    ).text)
                    
                    if evaluacion.get('aprobado'):
                        pilar_info = CATALOGO_PILARES.get(tarea['pilar_destino'])
                        if pilar_info:
                            tabla_real = pilar_info['nombre_tabla']
                            vec = get_embedding(f"{tarea['tema_objetivo']} {contenido}")
                            supabase.rpc('cerebro_aprender', {
                                'p_orquestador_id': ORQUESTADOR_ID, 'p_tabla_destino': tabla_real,
                                'p_concepto': tarea['tema_objetivo'], 'p_detalle': contenido,
                                'p_codigo': evaluacion.get('codigo_detectado', ''), 'p_vector': vec
                            }).execute()
                            supabase.table('laboratorio_ideas').delete().eq('id', tarea['id']).execute()
                            print(f"🎓 [LAB] Graduado: {tarea['tema_objetivo']}")
                    else:
                        supabase.table('laboratorio_ideas').update({
                            'estado': 'rechazado', 'critica_ia': evaluacion.get('critica', 'Rechazado'), 'intentos': tarea['intentos'] + 1
                        }).eq('id', tarea['id']).execute()
                else:
                    # 2. SI NO HAY TAREAS -> DIAGNÓSTICO CONSCIENTE
                    print("💡 [AUTO] Evaluando siguiente paso en el plan de estudios...")
                    
                    pilar, nivel, estrategia = auditoria_consciente()
                    nuevo_tema = generar_curriculum_adaptativo(pilar, nivel, estrategia)
                    
                    print(f"📘 [PLAN] Agregando al pensum ({nivel}): {nuevo_tema}")
                    
                    supabase.table('laboratorio_ideas').insert({
                        'orquestador_id': ORQUESTADOR_ID, 
                        'tema_objetivo': nuevo_tema, 
                        'pilar_destino': pilar, 
                        'estado': 'borrador'
                    }).execute()
                    
            except Exception as e:
                print(f"❌ [AUTO] Error ciclo: {e}")
        
        time.sleep(TIEMPO_ENTRE_CICLOS)

threading.Thread(target=ciclo_vida_autonomo, daemon=True).start()

# ==============================================================================
# API PÚBLICA (FIXED JSON LIST ERROR)
# ==============================================================================

def filtro_especialidad(pregunta):
    mod = genai.GenerativeModel(GENERATIVE_MODEL)
    try:
        # APLICAMOS EL FIX AQUÍ
        return normalizar_respuesta_json(mod.generate_content(
            f"Filtro Blender. Pregunta: '{pregunta}'. JSON {{es_relevante: bool, razon: str}}",
            generation_config={"response_mime_type": "application/json"}
        ).text)
    except: return {"es_relevante": True}

def planificar_busqueda(pregunta):
    mod = genai.GenerativeModel(GENERATIVE_MODEL)
    pilares = "\n".join([f"- {k}: {v['descripcion']}" for k,v in CATALOGO_PILARES.items()])
    try:
        # APLICAMOS EL FIX AQUÍ
        data = normalizar_respuesta_json(mod.generate_content(
            f"Pregunta: {pregunta}\nTablas:\n{pilares}\nJSON {{pilares_seleccionados: [str]}}",
            generation_config={"response_mime_type": "application/json"}
        ).text)
        return data.get("pilares_seleccionados", [])
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
        # APLICAMOS EL FIX AQUÍ
        data = normalizar_respuesta_json(mod.generate_content(
            f"Pregunta: {pregunta}\nContexto: {contexto}\n¿Suficiente? JSON {{es_suficiente: bool, razon: str}}",
            generation_config={"response_mime_type": "application/json"}
        ).text)
        return data.get("es_suficiente", False), data.get("razon", "")
    except: return False, "Error"

def aprender_usuario(pregunta, contexto_parcial):
    print(f"🌐 [USER] Investigando: {pregunta}")
    # Usamos nivel 'EXPERTO' para el usuario porque asumimos que pregunta algo que necesita
    contenido = investigar_tema(pregunta, "EXPERTO") 
    
    try:
        criterios = "\n".join([f"- {k}: {v.get('criterio_admision')}" for k,v in CATALOGO_PILARES.items()])
        mod = genai.GenerativeModel(GENERATIVE_MODEL)
        
        # APLICAMOS EL FIX AQUÍ (Donde fallaba antes)
        datos = normalizar_respuesta_json(mod.generate_content(
            f"Clasifica: {contenido}\nCriterios: {criterios}\nJSON {{tabla_destino: str|null, concepto: str, detalle: str, codigo: str}}",
            generation_config={"response_mime_type": "application/json"}
        ).text)
        
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
    return jsonify({"status": "Active", "mode": "Conscious Learner V10"}), 200

@app.route("/preguntar", methods=["POST"])
def endpoint_preguntar():
    data = request.json
    pregunta = data.get('pregunta', '')
    if not pregunta: return jsonify({"error": "Vacio"}), 400
    
    print(f"\n📨 Usuario: {pregunta}")
    
    analisis = filtro_especialidad(pregunta)
    if not analisis.get("es_relevante", True):
        return jsonify({"respuesta_principal": "Solo Blender/3D.", "puntos_clave": [], "fuente": "Filtro"})
        
    pilares = planificar_busqueda(pregunta)
    contexto = consultar_memoria_flow(pilares, get_embedding(pregunta))
    
    suficiente, razon = False, ""
    if contexto: suficiente, razon = evaluar_suficiencia(pregunta, contexto)
    
    mod = genai.GenerativeModel(GENERATIVE_MODEL)
    if suficiente:
        txt = mod.generate_content(f"Responde experto con: {contexto}. Pregunta: {pregunta}").text
        src = "Memoria Experta"
    else:
        txt = aprender_usuario(pregunta, contexto)
        src = "Investigación Activa"
        
    try:
        # APLICAMOS EL FIX AQUÍ
        final_data = normalizar_respuesta_json(mod.generate_content(
            f"JSON Frontend:\nTEXTO:{txt}\nFUENTE:{src}\nJSON:{{respuesta_principal: str, puntos_clave: [{{titulo:str, descripcion:str}}], fuente: str}}",
            generation_config={"response_mime_type": "application/json"}
        ).text)
        return jsonify(final_data)
    except:
        return jsonify({"respuesta_principal": txt, "puntos_clave": [], "fuente": src})

if __name__ == "__main__":
    app.run(debug=True, port=5000)
