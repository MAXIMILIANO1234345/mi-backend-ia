import os
import json
import time
import google.generativeai as genai
from dotenv import load_dotenv
from supabase import create_client, Client
from datetime import datetime, timedelta

# ==============================================================================
# 1. CONFIGURACIÓN E INICIALIZACIÓN
# ==============================================================================
print("--- INICIANDO MAESTRO ESTRATEGA V24 (Sincronizado con SQL) ---")

# Cargar variables de entorno
load_dotenv()

# Credenciales
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

# Validaciones de seguridad
if not all([GOOGLE_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
    raise ValueError("⚠️ Faltan credenciales críticas (GOOGLE_API_KEY, SUPABASE_URL, SUPABASE_KEY).")

# Conexión a servicios
genai.configure(api_key=GOOGLE_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Configuración del Worker
MODELO_DIRECTOR = "models/gemini-1.5-flash" 
ORQUESTADOR_ID = 1 # ID de este orquestador en tu BD (ajustar si tienes múltiples)
CICLO_ANALISIS = 70 # Tiempo en segundos entre análisis (30 min)

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
                "top_conceptos_usados": top_usados.data,
                "cantidad_datos_sin_uso": zombies.count
            }
        except Exception as e:
            print(f"⚠️ No se pudo leer la tabla {tabla}: {e}")
            
    return resumen_memorias

def obtener_tendencias_usuarios():
    """
    Lee la tabla 'historial_prompts' para entender qué está pidiendo el mercado (los usuarios).
    """
    try:
        # Traer los últimos 50 prompts crudos
        data = supabase.table('historial_prompts')\
            .select('prompt_usuario, created_at')\
            .order('created_at', desc=True)\
            .limit(50).execute()
        return data.data
    except Exception as e:
        print(f"⚠️ Error leyendo historial de usuarios: {e}")
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
            "nombre_clave": f"analisis_{datetime.now().strftime('%Y%m%d_%H%M')}",
            "total_registros": analisis_json.get("total_registros", 0),
            "top_temas_buscados": analisis_json.get("top_temas_buscados", "N/A"),
            "resumen_contenido": analisis_json.get("resumen_contenido", ""),
            "brechas_detectadas": analisis_json.get("brechas_detectadas", ""),
            "sugerencias_mercado": analisis_json.get("sugerencias_mercado", ""),
            "updated_at": datetime.now().isoformat()
        }
        
        # Insertar en Supabase
        supabase.table('informes_pilares').insert(datos_informe).execute()
        print(f"✅ Informe Estratégico guardado: {datos_informe['nombre_clave']}")
        
        # Procesar órdenes sugeridas (si las hay)
        if "nuevas_ordenes" in analisis_json and analisis_json["nuevas_ordenes"]:
            print("🚀 El Maestro sugiere las siguientes acciones inmediatas:")
            for orden in analisis_json["nuevas_ordenes"]:
                print(f"   - {orden}")
                # Aquí podrías inyectar estas órdenes en 'command_queue' automáticamente si quisieras

    except Exception as e:
        print(f"❌ Error guardando informe en DB: {e}")

# ==============================================================================
# 5. BUCLE PRINCIPAL DE VIDA
# ==============================================================================

def ciclo_vida_maestro():
    print(f"🎓 Maestro V24 escuchando... (Ciclo de {CICLO_ANALISIS} segundos)")
    print("   Modo: Análisis de Mercado (Prompts) y Auditoría de Memoria")
    
    while True:
        try:
            print("\n🔍 [FASE 1] Recopilando datos de telemetría SQL...")
            
            # 1. Obtener Datos
            metricas = obtener_metricas_memoria()
            prompts = obtener_tendencias_usuarios()
            
            # Validación básica para no gastar tokens si está vacío
            if not prompts and not metricas:
                print("💤 Sin actividad suficiente para análisis. Esperando...")
                time.sleep(300)
                continue

            print(f"   - Prompts analizados: {len(prompts)}")
            print(f"   - Tablas de memoria auditadas: {len(metricas)}")

            # 2. Construir el Prompt para Gemini
            prompt_analisis = f"""
            DATOS DEL SISTEMA PARA ANÁLISIS ESTRATÉGICO:
            
            --- SECCIÓN A: MÉTRICAS DE MEMORIA (Nuestro Conocimiento Actual) ---
            {json.dumps(metricas, indent=2)}
            
            --- SECCIÓN B: DEMANDA DEL MERCADO (Últimos Prompts de Usuarios) ---
            {json.dumps(prompts, indent=2)}
            
            Basado en esto, genera el JSON para el informe 'informes_pilares'.
            Prioriza identificar 'brechas_detectadas': cosas que piden en la Sección B que no aparecen fuertes en la Sección A.
            """

            # 3. Fase de Pensamiento
            print("🧠 [FASE 2] Procesando estrategia con Gemini...")
            response = model.generate_content(prompt_analisis)
            
            if response.text:
                try:
                    json_limpio = limpiar_json(response.text)
                    json_data = json.loads(json_limpio)
                    
                    # 4. Fase de Actuación
                    print("📝 [FASE 3] Escribiendo informe en Base de Datos...")
                    guardar_informe(json_data)
                    
                except json.JSONDecodeError as e:
                    print(f"⚠️ Error al decodificar la respuesta JSON del modelo: {e}")
                    print(f"   Texto recibido: {response.text[:100]}...")
            
            # 5. Descanso
            print(f"💤 Ciclo completado. Durmiendo por {CICLO_ANALISIS} segundos...")
            time.sleep(CICLO_ANALISIS)

        except Exception as e:
            print(f"🔥 Error Crítico en el ciclo del Maestro: {e}")
            print("   Reintentando en 60 segundos...")
            time.sleep(60)

if __name__ == "__main__":
    ciclo_vida_maestro()
