import os
import json
import time
import sys
import google.generativeai as genai
from dotenv import load_dotenv
from supabase import create_client, Client
from datetime import datetime, timedelta

# ==============================================================================
# 1. CONFIGURACIÓN E INICIALIZACIÓN
# ==============================================================================
def log_visual(emoji, estado, mensaje):
    """Imprime mensajes bonitos y fuerza la salida al log de Render."""
    timestamp = datetime.now().strftime('%H:%M:%S')
    print(f"{emoji} [{timestamp}] {estado:<12} | {mensaje}", flush=True)

print("\n" + "="*60)
log_visual("🎩", "SYSTEM", "INICIANDO MAESTRO ESTRATEGA V24 (Visual Mode)")
print("="*60 + "\n")

# Cargar variables de entorno
load_dotenv()

# Credenciales
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

# Validaciones de seguridad
if not all([GOOGLE_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
    log_visual("🔥", "ERROR", "Faltan credenciales críticas (GOOGLE_API_KEY, SUPABASE_URL, SUPABASE_KEY).")
    raise ValueError("Faltan credenciales.")

# Conexión a servicios
try:
    genai.configure(api_key=GOOGLE_API_KEY)
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    log_visual("🔗", "CONEXION", "Servicios conectados (Supabase + Gemini)")
except Exception as e:
    log_visual("🔥", "ERROR", f"Fallo en conexión inicial: {e}")
    sys.exit(1)

# Configuración del Worker
MODELO_DIRECTOR = "models/gemini-2.5-flash" 
ORQUESTADOR_ID = 1 # ID de este orquestador en tu BD (ajustar si tienes múltiples)
CICLO_ANALISIS = 70 # Tiempo en segundos entre análisis

# ==============================================================================
# 2. HERRAMIENTAS DE DATOS (LECTURA DE TELEMETRÍA)
# ==============================================================================

def limpiar_json(texto):
    """Limpia el formato Markdown de las respuestas de Gemini para obtener JSON puro."""
    texto = texto.strip()
    if texto.startswith("```json"):
        texto = texto[7:]
    elif texto.startswith("```"):
        texto = texto[3:]
    
    if texto.endswith("```"):
        texto = texto[:-3]
    
    return texto.strip()

def obtener_metricas_memoria():
    """
    Escanea las tablas de memoria para ver qué conocimientos son útiles ('Héroes')
    y cuáles están obsoletos ('Zombies'). Utiliza los contadores de la V24.
    """
    resumen_memorias = {}
    
    # Tablas de conocimiento que queremos auditar
    tablas_memoria = ['memoria_blender_scripting', 'memoria_blender_commands'] 
    
    for tabla in tablas_memoria:
        try:
            # 1. Traer los conceptos más útiles (Top 5)
            top_usados = supabase.table(tabla)\
                .select('concepto, contador_uso')\
                .order('contador_uso', desc=True)\
                .limit(5).execute()
            
            # 2. Contar cuántos datos son 'Zombies' (0 uso)
            zombies = supabase.table(tabla)\
                .select('id', count='exact')\
                .eq('contador_uso', 0)\
                .execute() 
            
            # 3. Total de registros
            total = supabase.table(tabla).select('id', count='exact').execute()

            resumen_memorias[tabla] = {
                "total_registros": total.count,
                "top_conceptos": [t['concepto'] for t in top_usados.data], # Simplificado para log
                "cantidad_datos_sin_uso": zombies.count
            }
        except Exception as e:
            log_visual("⚠️", "WARN", f"No se pudo leer la tabla {tabla}: {e}")
            
    return resumen_memorias

def obtener_tendencias_usuarios():
    """
    Lee la tabla 'historial_prompts' para entender qué está pidiendo el mercado.
    """
    try:
        # Traer los últimos 20 prompts para ser ágil
        data = supabase.table('historial_prompts')\
            .select('prompt_usuario, created_at')\
            .order('created_at', desc=True)\
            .limit(20).execute()
        return data.data
    except Exception as e:
        log_visual("⚠️", "WARN", f"Error leyendo historial de usuarios: {e}")
        return []

# ==============================================================================
# 3. EL CEREBRO ANALÍTICO (DEFINICIÓN DEL MODELO)
# ==============================================================================

generation_config = {
    "temperature": 0.5, # Temperatura media: Analítico pero capaz de conectar ideas
    "response_mime_type": "application/json",
}

model = genai.GenerativeModel(
    model_name=MODELO_DIRECTOR,
    generation_config=generation_config,
    system_instruction="""
    Eres el DIRECTOR DE INTELIGENCIA Y ESTRATEGIA de un sistema de automatización para Blender 3D.
    
    TU MISIÓN:
    No ejecutas código. Tu trabajo es ANALIZAR los datos de uso y la memoria del sistema para generar un INFORME ESTRATÉGICO que guíe el desarrollo futuro.
    
    INPUT QUE RECIBIRÁS:
    1. 'metricas_memoria': Estadísticas de qué scripts se usan mucho y cuáles nunca se usan.
    2. 'prompts_recientes': Lista de lo que los usuarios han pedido recientemente.
    
    OUTPUT REQUERIDO (JSON STRICT):
    Debes generar un objeto JSON listo para ser insertado en la tabla SQL 'informes_pilares'.
    Estructura:
    {
        "nombre_clave": "informe_automatico_v24",
        "total_registros": (Entero: Suma aproximada de conocimientos en memoria),
        "top_temas_buscados": "String: Resumen de los 3 temas más recurrentes en los prompts (ej: 'Simulación de fluidos, Materiales PBR, Exportación')",
        "resumen_contenido": "String: Párrafo breve sobre la salud actual de la base de conocimiento.",
        "brechas_detectadas": "String: CRÍTICO. ¿Qué están pidiendo los usuarios que NO tenemos en memoria o tenemos pero con 0 uso?",
        "sugerencias_mercado": "String: Recomendación estratégica. ¿Qué nuevos scripts deberíamos generar para satisfacer la demanda no cubierta?",
        "nuevas_ordenes": [ 
            "String opcional: Tarea técnica específica 1",
            "String opcional: Tarea técnica específica 2"
        ]
    }
    """
)

# ==============================================================================
# 4. ACTUADORES (ESCRITURA EN DB)
# ==============================================================================

def guardar_informe(analisis_json):
    """Escribe el resultado del análisis en la tabla 'informes_pilares'."""
    try:
        # Preparamos el payload para SQL
        datos_informe = {
            "orquestador_id": ORQUESTADOR_ID,
            "nombre_clave": f"analisis_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "total_registros": analisis_json.get("total_registros", 0),
            "top_temas_buscados": analisis_json.get("top_temas_buscados", "N/A"),
            "resumen_contenido": analisis_json.get("resumen_contenido", ""),
            "brechas_detectadas": analisis_json.get("brechas_detectadas", ""),
            "sugerencias_mercado": analisis_json.get("sugerencias_mercado", ""),
            "updated_at": datetime.now().isoformat()
        }
        
        # Insertar en Supabase
        supabase.table('informes_pilares').insert(datos_informe).execute()
        log_visual("💾", "GUARDADO", f"Informe Estratégico guardado: {datos_informe['nombre_clave']}")
        
        # Procesar órdenes sugeridas (si las hay)
        if "nuevas_ordenes" in analisis_json and analisis_json["nuevas_ordenes"]:
            log_visual("🚀", "ACCION", f"El Maestro sugiere {len(analisis_json['nuevas_ordenes'])} acciones nuevas.")
            for orden in analisis_json["nuevas_ordenes"]:
                print(f"      - {orden}")

    except Exception as e:
        log_visual("❌", "DB ERROR", f"Error guardando informe en DB: {e}")

# ==============================================================================
# 5. BUCLE PRINCIPAL DE VIDA
# ==============================================================================

def ciclo_vida_maestro():
    log_visual("🟢", "ONLINE", f"Ciclo de vida iniciado. Intervalo: {CICLO_ANALISIS}s")
    
    while True:
        try:
            # --- FASE 1: RECOLECCIÓN ---
            log_visual("🔍", "SCAN", "Recopilando telemetría SQL...")
            metricas = obtener_metricas_memoria()
            prompts = obtener_tendencias_usuarios()
            
            # Validación básica para no gastar tokens si está vacío
            if not prompts and not metricas:
                log_visual("💤", "IDLE", "Sin actividad suficiente. Esperando...")
                time.sleep(CICLO_ANALISIS)
                continue

            log_visual("📊", "STATS", f"Prompts: {len(prompts)} | Tablas Memoria: {len(metricas)}")

            # --- FASE 2: ANÁLISIS ---
            log_visual("🧠", "THINKING", "Procesando estrategia con Gemini...")
            
            prompt_analisis = f"""
            DATOS DEL SISTEMA PARA ANÁLISIS ESTRATÉGICO:
            --- SECCIÓN A: MÉTRICAS DE MEMORIA (Nuestro Conocimiento Actual) ---
            {json.dumps(metricas, indent=2)}
            --- SECCIÓN B: DEMANDA DEL MERCADO (Últimos Prompts de Usuarios) ---
            {json.dumps(prompts, indent=2)}
            
            Basado en esto, genera el JSON para el informe 'informes_pilares'.
            """

            response = model.generate_content(prompt_analisis)
            
            # --- FASE 3: EJECUCIÓN ---
            if response.text:
                try:
                    json_limpio = limpiar_json(response.text)
                    json_data = json.loads(json_limpio)
                    log_visual("📝", "WRITING", "Estrategia generada. Guardando en DB...")
                    guardar_informe(json_data)
                    
                except json.JSONDecodeError as e:
                    log_visual("⚠️", "PARSE ERROR", f"JSON inválido de Gemini: {e}")
            
            log_visual("✅", "DONE", "Ciclo completado correctamente.")
            
            # --- BARRA DE PROGRESO VISUAL ---
            print(f"⏳ Esperando {CICLO_ANALISIS}s: ", end="", flush=True)
            pasos = 10
            tiempo_paso = CICLO_ANALISIS / pasos
            for _ in range(pasos):
                time.sleep(tiempo_paso)
                print(".", end="", flush=True)
            print(" 🚀\n")

        except Exception as e:
            log_visual("🔥", "CRITICAL", f"Error en ciclo del Maestro: {e}")
            print("   Reintentando en 60 segundos...")
            time.sleep(60)

if __name__ == "__main__":
    ciclo_vida_maestro()
