import json, os
import httpx
from http.server import BaseHTTPRequestHandler
from datetime import datetime
import pytz
import gspread
from google.oauth2.service_account import Credentials

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ALLOWED_CHAT_IDS = [
    int(cid.strip())
    for cid in os.environ.get("ALLOWED_CHAT_IDS", "").split(",")
    if cid.strip()
]

SHEET_ID_LOG = os.environ.get("SHEET_ID_LOG", "")
GPC_SERVICE_ACCOUNT_JSON = os.environ.get("GPC_SERVICE_ACCOUNT_JSON", "")

def log_to_sheet(message_text: str, reply: str, now: datetime):
    creds_info = json.loads(GPC_SERVICE_ACCOUNT_JSON)
    creds = Credentials.from_service_account_info(
        creds_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID_LOG).sheet1

    fecha = now.strftime("%d-%m-%Y %H:%M")
    sheet.insert_row([fecha, message_text, reply], index=3)

SYSTEM_PROMPT = """
Eres un asistente que clasifica mensajes de gastos e inventario de café y devuelves SIEMPRE un JSON válido, sin texto adicional, sin backticks, sin explicaciones.
## TIPOS DE MENSAJE
### 1. gasto
Mensajes sobre dinero gastado. El formato puede variar: "50 frutería", "frutería 50", "gasté 50 en la frutería". La ubicación es Ecuador, así que las tiendas pueden ser buscadas para ver la categoría y el formato de descripción correcto (kfc -> KFC, mcdonalds -> McDonald's, y entre otras).
Categorías disponibles (elige la más apropiada):
- creditos: pagos de créditos bancarios, cuota de la casa
- tarjetas: pago de tarjetas de crédito
- salidas_comer: restaurantes, comida fuera de casa
- salud: farmacias, clínicas, odontólogo
- nico: gastos relacionados al primer hijo, transferencias para él
- milo: gastos relacionados al segundo hijo, transferencias para él
- supermercados: Tuti, Supermaxi, Santa María, Akí y similares
- ferreteria: Kywi, Promart y similares
- jardineria: plantas, herramientas de jardín
- impuestos: pagos al SRI u otros impuestos
- servicios_basicos: agua, luz, teléfono, gas, botellones de agua
- comida_casa: frutería, panadería, carnicería
- vehiculos: gasolina, mantenimiento de vehículos
- gastos_bancarios: comisiones de transferencia, costos bancarios
Devuelve:
{"tipo": "gasto", "monto": 50.00, "descripcion": "frutería", "categoria": "comida_casa", "fecha": "2026-06-15"}
### 2. inventario_entrega
Mensajes sobre fundas de café entregadas a un cliente. Ejemplos: "30 fundas entregadas a Carloko a 3 cada una", "entregué 15 fundas a Merceditas".
Supuestos fijos:
- El café es MOLIDO salvo que el mensaje diga explícitamente "en grano"
- La entrega NO fue pagada en el momento salvo que se diga explicitamente
- Si no hay precio inicial, ya está ingresado en la tabla.
Devuelve:
{"tipo": "inventario_entrega", "proveedor": "Carloko", "cantidad": 30, "tipo_cafe": "molido", "precio_unitario": 3.00, "pagado_en_momento": false, "fecha": "2026-06-15"}
### 3. inventario_pago
Mensajes sobre pagos realizados. Ejemplos: "Carloko depositó de 5 fundas", "Merceditas pagó 30 fundas del lote anterior".
Devuelve:
{"tipo": "inventario_pago", "proveedor": "Carloko", "cantidad": 5, "fecha": "2026-06-15", "lote":"anterior"}
### 4. inventario_lote
Mensajes sobre ingreso de mercadería de un lote de café. Ejemplos: "Ingreso de 15 fundas de café en grano del lote 15", "100 fundas empacadas de café".
Supuestos fijos:
- El café es MOLIDO salvo que el mensaje diga explícitamente "en grano"
- El lote es el actual a menos que se indique "nuevo lote".
Devuelve:
{"tipo": "inventario_lote", "fecha": "2026-06-15", "lote":"15", "cantidad":15, "tipo_cafe":"molido"}
### 5. consulta
Preguntas sobre gastos o inventario. Ejemplos: "¿cuánto me debe Carloko?", "¿cuánto gasté este mes?".
Devuelve:
{"tipo": "consulta", "pregunta": "¿cuánto me debe Carloko?"}
### 6. desconocido
Si el mensaje no encaja en ninguna categoría.
Devuelve:
{"tipo": "desconocido", "mensaje_original": "texto del mensaje"}
## REGLAS GENERALES
- La fecha siempre en formato DD-MM-YYYY. Usa la fecha actual si no se especifica.
- Cuando el mensaje mencione un día de la semana (ej. "el martes", "el lunes pasado"), calcula la fecha exacta contando hacia atrás desde el día actual indicado en el contexto. Si el día mencionado es el mismo día de la semana que hoy, asume que se refiere a la semana anterior.
- Los montos siempre en número decimal (sin símbolo $).
- Devuelve ÚNICAMENTE JSON, nada más.
- En caso de ser necesario,puedes devolver más de un JSON para categorizar distintas transacciones.
- Cualquier mensaje que no corresponda claramente a gasto, inventario_entrega, inventario_pago, inventario_nuevo_lote o consulta — incluyendo saludos, mensajes de prueba, texto sin sentido — SIEMPRE devuelve: {"tipo": "desconocido", "mensaje_original": "texto del mensaje"} NUNCA devuelvas texto plano. SIEMPRE JSON.
"""

def send_message(chat_id: int, text: str):
    httpx.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=10,
    )

def classify_message(text: str, now: datetime) -> str:
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    dia_actual = dias[now.weekday()]
    today = now.strftime("%d-%m-%Y")
    
    contexto_fecha = f"Hoy es {dia_actual}, {today}."

    response = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 512,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": f"{contexto_fecha}\n{text}"}],
        },
        timeout=15,
    )
    text = response.json()["content"][0]["text"].strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]
    return text.strip()

class handler(BaseHTTPRequestHandler):
     def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        message = body.get("message", {})
        text = message.get("text", "")
        chat_id = message["chat"]["id"]

        if chat_id not in ALLOWED_CHAT_IDS:
            send_message(chat_id, 'Usuario no autorizado, comuníquese con el creador del bot.')
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return

        quito_tz = pytz.timezone("America/Guayaquil")
        now = datetime.now(quito_tz)

        try:
            reply = classify_message(text, now)
        except Exception as e:
            reply = f"Error: {str(e)}"

        send_message(chat_id, reply)

        try:
            log_to_sheet(text, reply, now)
        except Exception as log_err:
            send_message(chat_id, f"⚠️ Log error: {log_err}")

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")