import sys
import os
import time
import traceback
import json
import google.generativeai as genai
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client, Client

# ==============================================================================
# 1. CONFIGURACIÓN
# ==============================================================================
sys.stdout.reconfigure(line_buffering=True)

def log_visual(emoji, estado, mensaje):
    timestamp = datetime.now().strftime('%H:%M:%S')
    print(f"{emoji} [{timestamp}] {estado:<10} | {mensaje}", flush=True)

print("\n" + "="*60)
log_visual("🎩", "INIT", "MAESTRO V35: EDITOR JEFE Y ESTRATEGA")
log_visual("⏳", "CONFIG", "Ciclo de auditoría configurado a 1 hora (3600s)")
print("="*60 + "\n")

try:
    load_dotenv()

    # Credenciales
    GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
    SUPABASE_URL = os.getenv('SUPABASE_URL')
    SUPABASE_KEY = os.getenv('SUPABASE_KEY')

    if not all([GOOGLE_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
        log_visual("🔥", "ERROR", "Faltan credenciales críticas.")
        sys.exit(1)

    # Conexiones
    genai.configure(api_key=GOOGLE_API_KEY)
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    
    # Configuración del Cerebro
    MODELO_DIRECTOR = "models/gemini-1.5-flash" 
    ORQUESTADOR_ID = 1 
    CICLO_ANALISIS = 3600 # 1 Hora

    log_visual("🔗", "CONEXION", "Conectado a Supabase y Gemini.")

    # ==============================================================================
    # 2. HERRAMIENTAS DE LECTURA (Ojos del Maestro)
    # ==============================================================================
    
    def limpiar_json(texto):
        texto = texto.strip()
        if texto.startswith("```json"):
            texto = texto[7:]
        elif texto.startswith("```"):
            texto = texto[3:]
        
        if texto.endswith("```"):
            texto = texto[:-3]
        
        return texto.strip()

    def obtener_muestras_contenido():
        """
        Extrae muestras reales de conocimiento para evaluar su calidad.
        No solo cuenta, LEE el contenido.
        """
        muestras = {}
        
        # 1. Obtener lista de tablas activas
        try:
            pilares = supabase.table('catalogo_pilares').select('nombre_tabla').execute()
            
            for p in pilares.data:
                tabla = p['nombre_tabla']
                # Traer los últimos 5 registros agregados para ver qué está aprendiendo Gemma
                data = supabase.table(tabla)\
                    .select('id, concepto, detalle_tecnico, codigo_ejemplo')\
                    .order('created_at', desc=True)\
                    .limit(5).execute()
                
                if data.data:
                    muestras[tabla] = data.data
                    
        except Exception as e:
            log_visual("⚠️", "READ_ERR", f"Error leyendo muestras: {e}")
            
        return muestras

    def leer_demanda_usuarios():
        """Ve qué están pidiendo los usuarios para dirigir a Gemma."""
        try:
            data = supabase.table('historial_prompts')\
                .select('prompt_usuario')\
                .order('created_at', desc=True)\
                .limit(20).execute()
            return [d['prompt_usuario'] for d in data.data]
        except: return []

    # ==============================================================================
    # 3. HERRAMIENTAS DE ACCIÓN (Manos del Maestro)
    # ==============================================================================

    def ejecutar_curaduria(acciones):
        """Ejecuta las órdenes de eliminación o creación de tareas."""
        if not acciones: return

        # 1. ELIMINAR BASURA (Gemma a veces alucina, Gemini limpia)
        if "eliminar_registros" in acciones:
            for item in acciones["eliminar_registros"]:
                tabla = item.get("tabla")
                id_reg = item.get("id")
                razon = item.get("razon", "Calidad baja")
                try:
                    supabase.table(tabla).delete().eq('id', id_reg).execute()
                    log_visual("🗑️", "DELETE", f"Borrado ID {id_reg} en {tabla}: {razon}")
                except Exception as e:
                    log_visual("❌", "DEL_FAIL", f"No se pudo borrar: {e}")

        # 2. ASIGNAR MISIONES A GEMMA (Crear tareas en laboratorio)
        if "nuevas_misiones" in acciones:
            for mision in acciones["nuevas_misiones"]:
                tema = mision.get("tema")
                pilar = mision.get("pilar_destino") # Debe coincidir con nombre_clave en catalogo
                
                if tema and pilar:
                    try:
                        nueva_tarea = {
                            "orquestador_id": ORQUESTADOR_ID,
                            "tema_objetivo": tema,
                            "pilar_destino": pilar,
                            "estado": "borrador", # Para que el app.py lo recoja
                            "origen": "MAESTRO_QA"
                        }
                        supabase.table('laboratorio_ideas').insert(nueva_tarea).execute()
                        log_visual("📢", "ASSIGN", f"Misión asignada a Gemma: {tema} -> {pilar}")
                    except Exception as e:
                        log_visual("❌", "ASSIGN_ERR", f"No se pudo asignar misión: {e}")

    def guardar_informe_auditoria(analisis):
        """Deja constancia del trabajo realizado."""
        try:
            informe = {
                "orquestador_id": ORQUESTADOR_ID,
                "nombre_clave": f"auditoria_qa_{datetime.now().strftime('%Y%m%d_%H%M')}",
                "resumen_contenido": analisis.get("comentario_general", "Sin comentarios"),
                "brechas_detectadas": str(analisis.get("nuevas_misiones", [])),
                "sugerencias_mercado": "Limpieza y Asignación Automática Ejecutada",
                "updated_at": datetime.now().isoformat()
            }
            supabase.table('informes_pilares').insert(informe).execute()
        except: pass

    # ==============================================================================
    # 4. CEREBRO ESTRATÉGICO
    # ==============================================================================
    model = genai.GenerativeModel(
        model_name=MODELO_DIRECTOR,
        generation_config={"response_mime_type": "application/json", "temperature": 0.3}, # Temperatura baja para ser estricto
        system_instruction="""
        Eres el EDITOR JEFE y ARQUITECTO de una base de conocimiento de Blender.
        Tu subordinado es "Gemma" (un modelo local), que a veces genera contenido de baja calidad o irrelevante.
        
        TUS RESPONSABILIDADES:
        1. AUDITAR (QA): Revisa las muestras de contenido recientes. Si ves código roto, explicaciones vacías (ej: "No sé"), o contenido en otro idioma no solicitado, ORDÉNA ELIMINARLO.
        2. DIRIGIR: Lee lo que piden los usuarios. Si piden algo que no ves en las muestras, CREA UNA MISIÓN para que Gemma lo investigue.
        
        FORMATO JSON STRICTO:
        {
            "comentario_general": "Opinión sobre la salud actual de la base de datos",
            "eliminar_registros": [
                {"tabla": "nombre_tabla", "id": 123, "razon": "Código incompleto/Alucinación"}
            ],
            "nuevas_misiones": [
                {"tema": "Título técnico específico para investigar", "pilar_destino": "nombre_clave_del_pilar (ej: api, objetos)"}
            ]
        }
        """
    )

    # ==============================================================================
    # 5. BUCLE DE VIDA
    # ==============================================================================
    def sesion_auditoria():
        log_visual("⚡", "START", "Iniciando sesión de Control de Calidad...")
        
        # 1. Recolección de Evidencia
        muestras = obtener_muestras_contenido()
        prompts = leer_demanda_usuarios()
        
        if not muestras and not prompts:
            log_visual("💤", "SKIP", "Sistema vacío. Nada que auditar.")
            return

        log_visual("🧠", "JUDGE", "Evaluando calidad del conocimiento...")
        
        prompt_analisis = f"""
        EVALUACIÓN DE CALIDAD:
        
        [CONTENIDO RECIENTE (Lo que Gemma escribió)]
        {json.dumps(muestras, indent=2)}
        
        [DEMANDA DE USUARIOS (Lo que el mercado pide)]
        {json.dumps(prompts, indent=2)}
        
        Decide qué borrar y qué investigar.
        """
        
        try:
            response = model.generate_content(prompt_analisis)
            if response.text:
                ordenes = json.loads(limpiar_json(response.text))
                
                log_visual("⚖️", "VERDICT", ordenes.get("comentario_general"))
                
                # Ejecutar decisiones reales
                ejecutar_curaduria(ordenes)
                guardar_informe_auditoria(ordenes)
                
        except Exception as e:
            log_visual("🔥", "AI_ERROR", f"Error en juicio del Maestro: {e}")

    def bucle_infinito():
        # Primera ejecución inmediata para verificar
        sesion_auditoria()
        
        while True:
            log_visual("💤", "WAIT", f"Esperando próximo turno ({CICLO_ANALISIS}s)...")
            time.sleep(CICLO_ANALISIS)
            sesion_auditoria()

    if __name__ == "__main__":
        bucle_infinito()

except Exception as e:
    log_visual("💀", "FATAL", f"Error irrecuperable: {e}")
    traceback.print_exc()
    time.sleep(10)
    sys.exit(1)
