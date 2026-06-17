import json, os
import httpx
from http.server import BaseHTTPRequestHandler
from datetime import datetime
import pytz
import gspread
from google.oauth2.service_account import Credentials

# ── Env vars ──────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ALLOWED_CHAT_IDS = [
    int(cid.strip())
    for cid in os.environ.get("ALLOWED_CHAT_IDS", "").split(",")
    if cid.strip()
]
SHEET_ID_LOG    = os.environ.get("SHEET_ID_LOG", "")
SHEET_ID_GASTOS = os.environ.get("SHEET_ID_GASTOS", "")
GPC_SERVICE_ACCOUNT_JSON = os.environ.get("GPC_SERVICE_ACCOUNT_JSON", "")

# ── Gastos constants ───────────────────────────────────────────────────────────
CATEGORY_MAP = {
    "creditos":          "Créditos",
    "tarjetas":          "Tarjetas",
    "salidas_comer":     "Salidas a comer",
    "salud":             "Salud",
    "nico":              "Nico",
    "milo":              "Milo",
    "supermercados":     "Supermercados",
    "ferreteria":        "Ferretería",
    "jardineria":        "Jardinería",
    "impuestos":         "Impuestos",
    "servicios_basicos": "Servicios básicos",
    "comida_casa":       "Comida casa",
    "vehiculos":         "Vehículos",
    "gastos_bancarios":  "Gastos bancarios",
    "varios":            "Varios",
}

CATEGORIES = [
    "Créditos", "Tarjetas", "Salidas a comer", "Salud", "Nico", "Milo",
    "Supermercados", "Ferretería", "Jardinería", "Impuestos",
    "Servicios básicos", "Comida casa", "Vehículos", "Gastos bancarios", "Varios",
]

MONTHS_ES = {
    1: "ENERO", 2: "FEBRERO", 3: "MARZO",      4: "ABRIL",
    5: "MAYO",  6: "JUNIO",   7: "JULIO",      8: "AGOSTO",
    9: "SEPTIEMBRE", 10: "OCTUBRE", 11: "NOVIEMBRE", 12: "DICIEMBRE",
}

# ── System prompt ──────────────────────────────────────────────────────────────
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
- varios: en caso que no entre en ninguna de las categorías previas
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

# ── Shared helpers ─────────────────────────────────────────────────────────────
def get_gspread_client():
    creds_info = json.loads(GPC_SERVICE_ACCOUNT_JSON)
    creds = Credentials.from_service_account_info(
        creds_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return gspread.authorize(creds)

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
    result = response.json()["content"][0]["text"].strip()
    if result.startswith("```"):
        result = result.split("\n", 1)[-1]
        result = result.rsplit("```", 1)[0]
    return result.strip()

def parse_reply_jsons(reply: str) -> list:
    """Parse one or more JSON objects from a reply string."""
    try:
        parsed = json.loads(reply)
        return parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        results = []
        for line in reply.strip().splitlines():
            line = line.strip()
            if line:
                try:
                    results.append(json.loads(line))
                except Exception:
                    pass
        return results

# ── Log sheet ──────────────────────────────────────────────────────────────────
def log_to_sheet(message_text: str, reply: str, now: datetime):
    client = get_gspread_client()
    sheet = client.open_by_key(SHEET_ID_LOG).sheet1
    fecha = now.strftime("%d-%m-%Y %H:%M")
    sheet.insert_row([fecha, message_text, reply], index=3)

# ── Gastos sheet ───────────────────────────────────────────────────────────────
def setup_month_sheet(spreadsheet, sheet_name: str, saldo_anterior: float):
    """Create and fully format a new month worksheet."""
    ws = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=20)
    sid = ws.id

    # ── Values ──
    ws.update("B2:E2", [["Saldo", "Presupuesto", "Gastos", "Disponible"]])
    ws.update(
        "B3:E3",
        [[saldo_anterior, 3500, "=SUM(E25:E1000)", "=C3+B3-D3"]],
        value_input_option="USER_ENTERED",
    )
    ws.update("B7:C7", [["Categoría", "Monto"]])
    ws.update("B8:B22", [[cat] for cat in CATEGORIES])
    ws.update(
        "C8:C22",
        [[f'=SUMIF(D25:D1000,"{cat}",E25:E1000)'] for cat in CATEGORIES],
        value_input_option="USER_ENTERED",
    )
    ws.update("B25:E25", [["Fecha", "Detalle", "Categoría", "Monto"]])

    # ── Formatting ──
    border   = {"style": "SOLID", "width": 1, "color": {"red": 0, "green": 0, "blue": 0}}
    borders  = {"top": border, "bottom": border, "left": border, "right": border,
                "innerHorizontal": border, "innerVertical": border}
    green    = {"red": 0.153, "green": 0.804, "blue": 0.153}   # #27CD27
    yellow   = {"red": 1.0,   "green": 0.859, "blue": 0.0}     # #FFDB00
    red      = {"red": 0.918, "green": 0.196, "blue": 0.196}   # #EB3232
    currency = {"numberFormat": {"type": "CURRENCY", "pattern": "\"$\"#,##0.00"}}

    spreadsheet.batch_update({"requests": [
        # Table 1 — header: green + bold (B2:E2)
        {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": 1, "endRowIndex": 2,
                      "startColumnIndex": 1, "endColumnIndex": 5},
            "cell": {"userEnteredFormat": {"backgroundColor": green, "textFormat": {"bold": True}}},
            "fields": "userEnteredFormat(backgroundColor,textFormat)"}},
        # Table 1 — values: currency format (B3:E3)
        {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": 2, "endRowIndex": 3,
                      "startColumnIndex": 1, "endColumnIndex": 5},
            "cell": {"userEnteredFormat": currency},
            "fields": "userEnteredFormat.numberFormat"}},
        # Table 1 — borders (B2:E3)
        {"updateBorders": {"range": {"sheetId": sid, "startRowIndex": 1, "endRowIndex": 3,
                                     "startColumnIndex": 1, "endColumnIndex": 5}, **borders}},

        # Table 2 — header: yellow + bold (B7:C7)
        {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": 6, "endRowIndex": 7,
                      "startColumnIndex": 1, "endColumnIndex": 3},
            "cell": {"userEnteredFormat": {"backgroundColor": yellow, "textFormat": {"bold": True}}},
            "fields": "userEnteredFormat(backgroundColor,textFormat)"}},
        # Table 2 — monto column: currency format (C8:C22)
        {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": 7, "endRowIndex": 22,
                      "startColumnIndex": 2, "endColumnIndex": 3},
            "cell": {"userEnteredFormat": currency},
            "fields": "userEnteredFormat.numberFormat"}},
        # Table 2 — borders (B7:C22)
        {"updateBorders": {"range": {"sheetId": sid, "startRowIndex": 6, "endRowIndex": 22,
                                     "startColumnIndex": 1, "endColumnIndex": 3}, **borders}},

        # Dynamic table — header: red + bold (B25:E25)
        {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": 24, "endRowIndex": 25,
                      "startColumnIndex": 1, "endColumnIndex": 5},
            "cell": {"userEnteredFormat": {"backgroundColor": red, "textFormat": {"bold": True}}},
            "fields": "userEnteredFormat(backgroundColor,textFormat)"}},
        # Dynamic table — header borders (B25:E25)
        {"updateBorders": {"range": {"sheetId": sid, "startRowIndex": 24, "endRowIndex": 25,
                                     "startColumnIndex": 1, "endColumnIndex": 5}, **borders}},

        # Pie chart anchored at E5
        {"addChart": {"chart": {
            "spec": {
                "title": f"Gastos — {sheet_name}",
                "pieChart": {
                    "legendPosition": "RIGHT_LEGEND",
                    "domain": {"sourceRange": {"sources": [{
                        "sheetId": sid,
                        "startRowIndex": 7, "endRowIndex": 22,
                        "startColumnIndex": 1, "endColumnIndex": 2,
                    }]}},
                    "series": {"sourceRange": {"sources": [{
                        "sheetId": sid,
                        "startRowIndex": 7, "endRowIndex": 22,
                        "startColumnIndex": 2, "endColumnIndex": 3,
                    }]}},
                },
            },
            "position": {"overlayPosition": {
                "anchorCell": {"sheetId": sid, "rowIndex": 4, "columnIndex": 4},
                "widthPixels": 450,
                "heightPixels": 350,
            }},
        }}},
    ]})
    return ws


def get_or_create_month_sheet(spreadsheet, year: int, month: int):
    sheet_name = f"{MONTHS_ES[month]} {year}"
    try:
        return spreadsheet.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        pass

    # Get previous month's "Disponible" (E3) as saldo_anterior
    saldo_anterior = 0.0
    prev_month = month - 1 if month > 1 else 12
    prev_year  = year if month > 1 else year - 1
    try:
        prev_ws = spreadsheet.worksheet(f"{MONTHS_ES[prev_month]} {prev_year}")
        val = prev_ws.acell("E3").value
        if val:
            saldo_anterior = float(str(val).replace("$", "").replace(",", "").strip())
    except Exception:
        pass

    return setup_month_sheet(spreadsheet, sheet_name, saldo_anterior)


def log_gasto(gasto: dict, now: datetime):
    client = get_gspread_client()
    spreadsheet = client.open_by_key(SHEET_ID_GASTOS)

    try:
        d, m, y = map(int, gasto["fecha"].split("-"))
    except Exception:
        d, m, y = now.day, now.month, now.year

    ws = get_or_create_month_sheet(spreadsheet, y, m)

    category      = CATEGORY_MAP.get(gasto.get("categoria", ""), "Varios")
    fecha_display = f"{d:02d}/{m:02d}/{y}"
    detalle       = gasto.get("descripcion", "").capitalize()
    monto         = float(gasto.get("monto", 0))

    ws.insert_row(["", fecha_display, detalle, category, monto], index=26)

    # Apply borders + currency format to the newly inserted row (always row 26)
    border = {"style": "SOLID", "width": 1, "color": {"red": 0, "green": 0, "blue": 0}}
    spreadsheet.batch_update({"requests": [
        {"updateBorders": {
            "range": {"sheetId": ws.id, "startRowIndex": 25, "endRowIndex": 26,
                      "startColumnIndex": 1, "endColumnIndex": 5},
            "top": border, "bottom": border, "left": border, "right": border,
            "innerVertical": border,
        }},
        {"repeatCell": {
            "range": {"sheetId": ws.id, "startRowIndex": 25, "endRowIndex": 26,
                      "startColumnIndex": 4, "endColumnIndex": 5},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "CURRENCY", "pattern": "\"$\"#,##0.00"}}},
            "fields": "userEnteredFormat.numberFormat",
        }},
    ]})


# ── Handler ────────────────────────────────────────────────────────────────────
class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length  = int(self.headers.get("Content-Length", 0))
        body    = json.loads(self.rfile.read(length))
        message = body.get("message", {})
        text    = message.get("text", "")
        chat_id = message["chat"]["id"]

        if chat_id not in ALLOWED_CHAT_IDS:
            send_message(chat_id, "Usuario no autorizado, comuníquese con el creador del bot.")
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

        try:
            for item in parse_reply_jsons(reply):
                if item.get("tipo") == "gasto":
                    if not item.get("monto") or not item.get("descripcion"):
                        send_message(
                            chat_id,
                            "Tu mensaje de tipo gasto no tiene un monto y/o descripción y no fue ingresado al archivo. "
                            "Vuelve a escribir el mensaje con la información correcta.",
                        )
                    else:
                        log_gasto(item, now)
        except Exception as gasto_err:
            send_message(chat_id, f"⚠️ Gastos error: {gasto_err}")

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")
