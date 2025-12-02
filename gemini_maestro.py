import os
import json
import time
import google.generativeai as genai
from dotenv import load_dotenv
from supabase import create_client, Client

# --- CONFIGURACIÓN DEL MAESTRO ---
print("--- INICIANDO SISTEMA DE MANDO ESTRATÉGICO (Gemini Maestro) ---")
load_dotenv()

# Credenciales
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

# Validaciones
if not all([GOOGLE_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
    raise ValueError("⚠️ Faltan credenciales (GOOGLE_API_KEY, SUPABASE_URL/KEY).")

# Conexión
genai.configure(api_key=GOOGLE_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Configuración del Modelo Director
MODELO_DIRECTOR = "models/gemini-2.5-flash" # Rápido, barato y con ventana de contexto grande
ORQUESTADOR_ID = 1
CICLO_AUDITORIA = 3600 # 1 Hora (El jefe revisa cada hora)

# ==============================================================================
# 🧠 UTILIDADES COGNITIVAS
# ==============================================================================

def limpiar_json(texto):
    """Limpia el formato Markdown de las respuestas de Gemini."""
    texto = texto.strip()
    if texto.startswith("
