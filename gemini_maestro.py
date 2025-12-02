import sys
import os
import time
import traceback
from datetime import datetime

# ==============================================================================
# 1. CONFIGURACIÓN DE LOGS (CRÍTICO PARA RENDER)
# ==============================================================================
# Forzamos que los prints salgan inmediatamente a la consola de Render
sys.stdout.reconfigure(line_buffering=True)

def log_visual(emoji, estado, mensaje):
    """Imprime mensajes con timestamp y fuerza el flush."""
    timestamp = datetime.now().strftime('%H:%M:%S')
    print(f"{emoji} [{timestamp}] {estado:<10} | {mensaje}", flush=True)

print("\n" + "="*60)
log_visual("🚀", "INIT", "ARRANCANDO MAESTRO V33 (AUDITORÍA INMEDIATA)")
print("="*60 + "\n")

try:
    # --- IMPORTACIONES ---
    # Las hacemos dentro del try para detectar si faltan librerías en requirements.txt
    import json
    import google.generativeai as genai
    from dotenv import load_dotenv
    from supabase import create_client, Client

    log_visual("📦", "IMPORTS", "Librerías cargadas correctamente.")

    # --- CARGA DE VARIABLES ---
    load_dotenv()

    GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
    SUPABASE_URL = os.getenv('SUPABASE_URL')
    SUPABASE_KEY = os.getenv('SUPABASE_KEY')

    # --- VALIDACIONES DE SEGURIDAD ---
    if not GOOGLE_API_KEY:
        log_visual("🔥", "ERROR", "Falta la variable GOOGLE_API_KEY.")
        time.sleep(2)
        sys.exit(1)
    if not SUPABASE_URL or not SUPABASE_KEY:
        log_visual("🔥", "ERROR", "Faltan credenciales de SUPABASE.")
        time.sleep(2)
        sys.exit(1)

    # --- CONEXIÓN A SERVICIOS ---
    genai.configure(api_key=GOOGLE_API_KEY)
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    
    # --- CONFIGURACIÓN DEL MODELO ---
    MODELO_DIRECTOR = "models/gemini-1.5-flash" 
    ORQUESTADOR_ID = 1 
    CICLO_ANALISIS = 60 # Segundos entre auditorías

    log_visual("🔗", "CONEXION", f"Conectado a Gemini ({MODELO_DIRECTOR}) y Supabase.")

    # ==============================================================================
    # 2. HERRAMIENTAS DE DATOS
    # ==============================================================================
    
    def limpiar_json(texto):
        """Limpia el formato Markdown de la respuesta para obtener JSON puro."""
        texto = texto.strip()
        if texto.startswith("```json"):
            texto = texto[7:]
        elif texto.startswith("```"):
            texto = texto[3:]
        
        if texto.endswith("```"):
            texto = texto[:-3]
        
        return texto.strip()

    def obtener_metricas_memoria():
        """Lee el estado actual de las tablas de conocimiento."""
        resumen = {}
        tablas = ['memoria_blender_scripting', 'memoria_blender_commands']
        
        for tabla in tablas:
            try:
                # 1. Contar total de registros
                total = supabase.table(tabla).select('id', count='exact').execute()
                
                # 2. Obtener un par de ejemplos para contexto
                top = supabase.table(tabla).select('concepto').limit(3).execute()
                
                resumen[tabla] = {
                    "total_registros": total.count,
                    "ejemplos": [t['concepto'] for t in top.data]
                }
            except Exception as e:
                log_visual("⚠️", "DB_READ", f"Fallo leyendo tabla '{tabla}': {e}")
                resumen[tabla] = "Error de lectura o tabla inexistente"
                
        return resumen

    def obtener_prompts_recientes():
        """Lee qué están pidiendo los usuarios para detectar tendencias."""
        try:
            # Traemos los últimos 10 prompts
            data = supabase.table('historial_prompts')\
                .select('prompt_usuario')\
                .order('created_at', desc=True)\
                .limit(10).execute()
            
            return [d['prompt_usuario'] for d in data.data]
        except Exception as e:
            log_visual("⚠️", "DB_READ", f"Fallo leyendo historial de usuarios: {e}")
            return []

    def guardar_informe(json_data):
        """Escribe el plan de acción generado por Gemini en la base de datos."""
        try:
            informe = {
                "orquestador_id": ORQUESTADOR_ID,
                "nombre_clave": f"auditoria_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                "resumen_contenido": json_data.get("resumen_estado", "Sin resumen"),
                "brechas_detectadas": str(json_data.get("brechas_conocimiento", [])),
                "sugerencias_mercado": json_data.get("accion_recomendada", ""),
                "updated_at": datetime.now().isoformat()
            }
            
            # Insertar en la tabla de informes
            supabase.table('informes_pilares').insert(informe).execute()
            log_visual("💾", "DB_WRITE", f"Informe '{informe['nombre_clave']}' guardado correctamente.")
            
        except Exception as e:
            log_visual("❌", "DB_ERROR", f"No se pudo escribir el informe en DB: {e}")

    # ==============================================================================
    # 3. CEREBRO (GEMINI)
    # ==============================================================================
    
    model = genai.GenerativeModel(
        model_name=MODELO_DIRECTOR,
        generation_config={"response_mime_type": "application/json", "temperature": 0.5},
        system_instruction="""
        Eres el MAESTRO AUDITOR del sistema Blender AI.
        Tu trabajo es verificar que todo funciona, leer la base de datos y proponer mejoras.
        
        Analiza los datos recibidos y genera un JSON estricto:
        {
            "resumen_estado": "Resumen breve del estado de la memoria y actividad reciente",
            "brechas_conocimiento": ["Lista de temas que faltan en memoria basados en prompts"],
            "accion_recomendada": "Sugerencia estratégica para el admin",
            "status_sistema": "OPERATIVO"
        }
        """
    )

    # ==============================================================================
    # 4. BUCLE PRINCIPAL DE VIDA
    # ==============================================================================
    
    def ejecutar_auditoria():
        """Ejecuta un ciclo de análisis completo (Leer -> Pensar -> Escribir)."""
        log_visual("🕵️", "AUDIT", "Iniciando ciclo de auditoría...")
        
        # 1. Leer Datos
        metricas = obtener_metricas_memoria()
        prompts = obtener_prompts_recientes()
        
        log_visual("📊", "DATA", f"Datos recolectados. Prompts recientes: {len(prompts)}")

        # 2. Construir Prompt para Gemini
        prompt_gemini = f"""
        REALIZA AUDITORÍA DE SISTEMA:
        
        [ESTADO DE MEMORIA]
        {json.dumps(metricas)}
        
        [ACTIVIDAD USUARIOS RECIENTE]
        {json.dumps(prompts)}
        
        Instrucciones:
        - Si no hay prompts, indica que el sistema está a la espera de usuarios (Status: ESPERA).
        - Si hay prompts sobre temas que no ves en memoria, repórtalo como brecha.
        """
        
        try:
            log_visual("🧠", "THINK", "Enviando datos a Gemini...")
            response = model.generate_content(prompt_gemini)
            
            if response.text:
                plan = json.loads(limpiar_json(response.text))
                log_visual("✅", "GEMINI", f"Diagnóstico: {plan.get('status_sistema')} | {plan.get('accion_recomendada')}")
                
                # 3. Guardar Resultado
                guardar_informe(plan)
            else:
                log_visual("⚠️", "GEMINI", "Respuesta vacía del modelo.")
                
        except Exception as e:
            log_visual("🔥", "ERROR", f"Fallo durante el análisis de Gemini: {e}")

    def ciclo_vida():
        # --- EJECUCIÓN INMEDIATA AL INICIO ---
        # Esto garantiza que veamos actividad en el log apenas arranque el worker
        log_visual("⚡", "START", "¡Ejecutando Auditoría de Arranque!")
        ejecutar_auditoria()
        log_visual("✅", "START", "Auditoría de arranque finalizada. Entrando en bucle continuo.")
        
        # --- BUCLE INFINITO ---
        while True:
            log_visual("💤", "WAIT", f"Durmiendo {CICLO_ANALISIS} segundos...")
            time.sleep(CICLO_ANALISIS)
            
            # Ejecutamos auditoría periódica
            ejecutar_auditoria()

    if __name__ == "__main__":
        ciclo_vida()

except Exception as e:
    # Captura cualquier error fatal al inicio que mataría el script silenciosamente
    log_visual("💀", "FATAL", f"El Maestro murió inesperadamente: {e}")
    traceback.print_exc()
    time.sleep(10) # Dar tiempo a que el log salga antes de que Render reinicie el proceso
    sys.exit(1)
