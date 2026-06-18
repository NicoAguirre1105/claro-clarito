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
SHEET_ID_LOG         = os.environ.get("SHEET_ID_LOG", "")
SHEET_ID_GASTOS      = os.environ.get("SHEET_ID_GASTOS", "")
SHEET_ID_INVENTARIO  = os.environ.get("SHEET_ID_INVENTARIO", "")
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
{"tipo": "inventario_entrega", "cliente": "Carloko", "cantidad": 30, "tipo_cafe": "molido", "precio_unitario": 3.00, "pagado_en_momento": false, "fecha": "2026-06-15"}
### 3. inventario_pago
Mensajes sobre pagos realizados. Ejemplos: "Carloko depositó de 5 fundas", "Merceditas pagó 30 fundas del lote anterior".
Devuelve:
{"tipo": "inventario_pago", "cliente": "Carloko", "cantidad": 5, "tipo_cafe":"molido" "fecha": "2026-06-15", "lote":"anterior"}
### 4. inventario_lote
Mensajes sobre ingreso de mercadería de un lote de café. Ejemplos: "Ingreso de 15 fundas de café en grano del lote 15", "100 fundas empacadas de café".
Supuestos fijos:
- El café es MOLIDO salvo que el mensaje diga explícitamente "en grano"
- El lote es el actual a menos que se indique un lote específico.
- Se acepta decir lote nuevo para la creación de un nuevo lote
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
- Clientes siempre escribelos con mayúscula al inicio. Si no hay cliente, deja ese item vacío
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
    data = response.json()
    if data.get("type") == "error":
        err_type = data.get("error", {}).get("type", "")
        if err_type in ("authentication_error", "permission_error", "billing_error") or response.status_code in (401, 402, 403):
            raise RuntimeError("SIN_CREDITOS")
        raise RuntimeError(data.get("error", {}).get("message", "error desconocido"))
    result = data["content"][0]["text"].strip()
    if result.startswith("```"):
        result = result.split("\n", 1)[-1]
        result = result.rsplit("```", 1)[0]
    return result.strip()

def parse_reply_jsons(reply: str) -> list:
    """Parse one or more JSON objects from a reply string."""
    # Try full string first
    try:
        parsed = json.loads(reply)
        return parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        pass
    # Try line by line (multiple JSONs on separate lines)
    results = []
    for line in reply.strip().splitlines():
        line = line.strip()
        if line:
            try:
                results.append(json.loads(line))
            except Exception:
                pass
    if results:
        return results
    # Last resort: extract JSON objects via braces matching
    extracted = []
    depth, start = 0, None
    for i, ch in enumerate(reply):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    extracted.append(json.loads(reply[start:i + 1]))
                except Exception:
                    pass
    return extracted

# ── Log sheet ──────────────────────────────────────────────────────────────────
def log_to_sheet(client, message_text: str, reply: str, now: datetime):
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


def log_gasto(client, gasto: dict, now: datetime):
    spreadsheet = client.open_by_key(SHEET_ID_GASTOS)

    try:
        d, m, y = map(int, gasto["fecha"].split("-"))
    except Exception:
        d, m, y = now.day, now.month, now.year

    ws = get_or_create_month_sheet(spreadsheet, y, m)

    category      = CATEGORY_MAP.get(gasto.get("categoria", ""), "Varios")
    fecha_display = f"{d:02d}/{m:02d}/{y}"
    raw_detalle   = gasto.get("descripcion", "")
    detalle       = raw_detalle[0].upper() + raw_detalle[1:] if raw_detalle else ""
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


# ── Inventario sheet ───────────────────────────────────────────────────────────
def setup_lote_sheet(spreadsheet, lote_num: int):
    """Create and format a new LOTE sheet."""
    sheet_name = f"LOTE {lote_num}"
    ws = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=20)
    sid = ws.id

    # Fixed table headers and formulas
    ws.update("B3:G3", [["Número de lote", "Grano", "Molido", "Stock en grano", "Stock molido", "Ingresos"]])
    ws.update(
        "B4:G4",
        [[lote_num, 0, 0,
          '=C4-SUMIF(C8:C1000,"Grano",D8:D1000)',
          '=D4-SUMIF(C8:C1000,"Molido",D8:D1000)',
          "=SUM(H8:H1000)"]],
        value_input_option="USER_ENTERED",
    )

    # Client table header
    ws.update("B7:H7", [["Cliente", "Tipo", "Cantidad", "Pagadas", "Precio unitario", "Deuda", "Total"]])

    # History table header
    ws.update("J7:N7", [["Fecha", "Acción", "Cliente", "Cantidad", "Mensaje"]])

    border      = {"style": "SOLID", "width": 1, "color": {"red": 0, "green": 0, "blue": 0}}
    borders     = {"top": border, "bottom": border, "left": border, "right": border,
                   "innerHorizontal": border, "innerVertical": border}
    lila        = {"red": 0.576, "green": 0.439, "blue": 0.859}
    green       = {"red": 0.133, "green": 0.694, "blue": 0.298}
    orange      = {"red": 0.902, "green": 0.494, "blue": 0.133}
    white_text  = {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True}
    currency_fmt = {"numberFormat": {"type": "CURRENCY", "pattern": "\"$\"#,##0.00"}}

    spreadsheet.batch_update({"requests": [
        # Fixed table header: lila + bold white (B3:G3)
        {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": 2, "endRowIndex": 3,
                      "startColumnIndex": 1, "endColumnIndex": 7},
            "cell": {"userEnteredFormat": {"backgroundColor": lila, "textFormat": white_text}},
            "fields": "userEnteredFormat(backgroundColor,textFormat)"}},
        # Fixed table borders (B3:G4)
        {"updateBorders": {"range": {"sheetId": sid, "startRowIndex": 2, "endRowIndex": 4,
                                     "startColumnIndex": 1, "endColumnIndex": 7}, **borders}},
        # G4 currency format
        {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": 3, "endRowIndex": 4,
                      "startColumnIndex": 6, "endColumnIndex": 7},
            "cell": {"userEnteredFormat": currency_fmt},
            "fields": "userEnteredFormat.numberFormat"}},

        # Client table header: green + bold white (B7:H7)
        {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": 6, "endRowIndex": 7,
                      "startColumnIndex": 1, "endColumnIndex": 8},
            "cell": {"userEnteredFormat": {"backgroundColor": green, "textFormat": white_text}},
            "fields": "userEnteredFormat(backgroundColor,textFormat)"}},
        # Client table header borders (B7:H7)
        {"updateBorders": {"range": {"sheetId": sid, "startRowIndex": 6, "endRowIndex": 7,
                                     "startColumnIndex": 1, "endColumnIndex": 8}, **borders}},

        # History table header: orange + bold white (J7:N7)
        {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": 6, "endRowIndex": 7,
                      "startColumnIndex": 9, "endColumnIndex": 14},
            "cell": {"userEnteredFormat": {"backgroundColor": orange, "textFormat": white_text}},
            "fields": "userEnteredFormat(backgroundColor,textFormat)"}},
        # History table header borders (J7:N7)
        {"updateBorders": {"range": {"sheetId": sid, "startRowIndex": 6, "endRowIndex": 7,
                                     "startColumnIndex": 9, "endColumnIndex": 14}, **borders}},
    ]})
    return ws


def get_max_lote_sheet(spreadsheet):
    """Return (worksheet, lote_number) for the sheet with the highest LOTE number."""
    max_num = 0
    max_ws  = None
    for ws in spreadsheet.worksheets():
        if ws.title.startswith("LOTE "):
            try:
                num = int(ws.title.split(" ", 1)[1])
                if num > max_num:
                    max_num = num
                    max_ws  = ws
            except ValueError:
                pass
    return max_ws, max_num


def get_or_create_lote_sheet(spreadsheet, lote_num: int):
    try:
        return spreadsheet.worksheet(f"LOTE {lote_num}")
    except gspread.WorksheetNotFound:
        return setup_lote_sheet(spreadsheet, lote_num)


def _find_client_row(ws, cliente: str, tipo_cafe: str):
    """Return 1-based row index where client+type match, or None."""
    col_b = ws.col_values(2)  # column B
    col_c = ws.col_values(3)  # column C
    for i in range(7, len(col_b)):  # data starts at row 8 (index 7)
        if (col_b[i].strip().lower() == cliente.strip().lower() and
                col_c[i].strip().lower() == tipo_cafe.strip().lower()):
            return i + 1
    return None


def _next_empty_row(ws, col_index: int, start_row: int = 8):
    """Find next empty row in a column (1-based col_index)."""
    values = ws.col_values(col_index)
    for i in range(start_row - 1, len(values)):
        if not values[i].strip():
            return i + 1
    return max(len(values) + 1, start_row)


def _format_client_row(spreadsheet, ws, row: int):
    border = {"style": "SOLID", "width": 1, "color": {"red": 0, "green": 0, "blue": 0}}
    currency_fmt = {"numberFormat": {"type": "CURRENCY", "pattern": "\"$\"#,##0.00"}}
    ri = row - 1
    spreadsheet.batch_update({"requests": [
        {"updateBorders": {
            "range": {"sheetId": ws.id, "startRowIndex": ri, "endRowIndex": ri + 1,
                      "startColumnIndex": 1, "endColumnIndex": 8},
            "top": border, "bottom": border, "left": border, "right": border,
            "innerVertical": border,
        }},
        # Precio unitario (F = col index 5)
        {"repeatCell": {
            "range": {"sheetId": ws.id, "startRowIndex": ri, "endRowIndex": ri + 1,
                      "startColumnIndex": 5, "endColumnIndex": 6},
            "cell": {"userEnteredFormat": currency_fmt},
            "fields": "userEnteredFormat.numberFormat"}},
        # Total (H = col index 7)
        {"repeatCell": {
            "range": {"sheetId": ws.id, "startRowIndex": ri, "endRowIndex": ri + 1,
                      "startColumnIndex": 7, "endColumnIndex": 8},
            "cell": {"userEnteredFormat": currency_fmt},
            "fields": "userEnteredFormat.numberFormat"}},
    ]})


def _format_history_row(spreadsheet, ws, row: int):
    border = {"style": "SOLID", "width": 1, "color": {"red": 0, "green": 0, "blue": 0}}
    ri = row - 1
    spreadsheet.batch_update({"requests": [
        {"updateBorders": {
            "range": {"sheetId": ws.id, "startRowIndex": ri, "endRowIndex": ri + 1,
                      "startColumnIndex": 9, "endColumnIndex": 14},
            "top": border, "bottom": border, "left": border, "right": border,
            "innerVertical": border,
        }},
    ]})


def _append_history(ws, spreadsheet, now: datetime, accion: str, cliente: str, cantidad: int, mensaje: str):
    fecha = now.strftime("%d-%m-%Y %H:%M")
    row = _next_empty_row(ws, col_index=10, start_row=8)  # column J = 10
    ws.update(f"J{row}:N{row}", [[fecha, accion, cliente, cantidad, mensaje]])
    _format_history_row(spreadsheet, ws, row)


def log_inventario_lote(client, item: dict, now: datetime) -> tuple[str | None, int | None]:
    """Returns (error_message, None) on failure or (None, lote_num) on success."""
    spreadsheet = client.open_by_key(SHEET_ID_INVENTARIO)
    lote_ref  = str(item.get("lote", "actual")).strip().lower()
    tipo_cafe = _normalize_tipo_cafe(item.get("tipo_cafe", "molido"))
    cantidad  = int(item.get("cantidad", 0))

    if tipo_cafe is None:
        return "El tipo de café no es válido. Debe ser 'grano' o 'molido'. Vuelve a escribir el mensaje.", None

    # Resolve lote
    try:
        lote_num = int(lote_ref)
        ws = get_or_create_lote_sheet(spreadsheet, lote_num)
    except ValueError:
        _, max_num = get_max_lote_sheet(spreadsheet)
        if lote_ref in ("nuevo", "new"):
            lote_num = max_num + 1
            ws = setup_lote_sheet(spreadsheet, lote_num)
        else:
            # "actual" or any other non-numeric → use max lote sheet
            ws, lote_num = get_max_lote_sheet(spreadsheet)
            if not ws:
                return "No hay lotes registrados. Indica un número de lote para crear el primero.", None

    if tipo_cafe == "Grano":
        current = ws.acell("C4").value or "0"
        ws.update("C4", [[int(_parse_num(current)) + cantidad]])
    else:
        current = ws.acell("D4").value or "0"
        ws.update("D4", [[int(_parse_num(current)) + cantidad]])

    _append_history(ws, spreadsheet, now, "Ingreso stock", "", cantidad, item.get("_raw", ""))
    return None, lote_num


def _normalize_tipo_cafe(raw: str) -> str | None:
    """Return 'Grano' or 'Molido', or None if unrecognized."""
    s = raw.strip().lower()
    if "grano" in s:
        return "Grano"
    if "molido" in s:
        return "Molido"
    return None


def _parse_num(value) -> float:
    """Parse a cell value (may contain $, commas) to float."""
    return float(str(value or "0").replace("$", "").replace(",", "").strip() or "0")


def log_inventario_entrega(client, item: dict, now: datetime) -> str | None:
    """Returns an error message string if something went wrong, else None."""
    spreadsheet = client.open_by_key(SHEET_ID_INVENTARIO)
    ws, _ = get_max_lote_sheet(spreadsheet)
    if not ws:
        return "No hay lotes registrados en el inventario."

    cliente   = item.get("cliente", "").strip()
    tipo_cafe = _normalize_tipo_cafe(item.get("tipo_cafe", "molido"))
    cantidad  = int(item.get("cantidad", 0))
    precio    = item.get("precio_unitario")
    pagado    = item.get("pagado_en_momento", False)

    if not cliente:
        return "La entrega no tiene un cliente asociado. Vuelve a escribir el mensaje con la información correcta."

    if tipo_cafe is None:
        return "El tipo de café no es válido. Debe ser 'grano' o 'molido'. Vuelve a escribir el mensaje."

    existing_row = _find_client_row(ws, cliente, tipo_cafe)

    # Validate stock
    stock_cell = "E4" if tipo_cafe == "Grano" else "F4"
    stock = _parse_num(ws.acell(stock_cell).value)
    if stock < cantidad:
        return (
            f"Stock insuficiente de café {tipo_cafe.lower()}. "
            f"Disponible: {int(stock)} fundas, solicitadas: {cantidad}. "
            "Verifica el inventario e intenta de nuevo."
        )

    if existing_row:
        current_d = int(_parse_num(ws.cell(existing_row, 4).value))
        ws.update_cell(existing_row, 4, current_d + cantidad)
        if pagado:
            current_e = int(_parse_num(ws.cell(existing_row, 5).value))
            ws.update_cell(existing_row, 5, current_e + cantidad)
    else:
        if not precio:
            return (
                "La entrega no tiene precio unitario y no hay una fila previa para este cliente. "
                "Vuelve a escribir el mensaje indicando el precio por funda."
            )
        row = _next_empty_row(ws, col_index=2, start_row=8)
        pagadas = cantidad if pagado else 0
        ws.update(
            f"B{row}:H{row}",
            [[cliente, tipo_cafe, cantidad, pagadas, float(precio),
              f"=D{row}-E{row}", f"=E{row}*F{row}"]],
            value_input_option="USER_ENTERED",
        )
        _format_client_row(spreadsheet, ws, row)

    _append_history(ws, spreadsheet, now, "Venta", cliente, cantidad, item.get("_raw", ""))
    return None


def log_inventario_pago(client, item: dict, now: datetime) -> str | None:
    """Returns an error message string if something went wrong, else None."""
    spreadsheet = client.open_by_key(SHEET_ID_INVENTARIO)
    lote_ref  = str(item.get("lote", "actual")).strip().lower()
    cliente   = item.get("cliente", "").strip()
    cantidad  = int(item.get("cantidad", 0))
    tipo_cafe = _normalize_tipo_cafe(item.get("tipo_cafe", "molido"))

    if not cliente:
        return "El pago no tiene un cliente asociado. Vuelve a escribir el mensaje con la información correcta."

    if tipo_cafe is None:
        return "El tipo de café no es válido. Debe ser 'grano' o 'molido'. Vuelve a escribir el mensaje."

    max_ws, max_num = get_max_lote_sheet(spreadsheet)
    if not max_ws:
        return "No hay lotes registrados en el inventario."

    if lote_ref in ("actual", "current"):
        ws = max_ws
    elif lote_ref == "anterior":
        target_num = max_num - 1
        if target_num < 1:
            return "No existe un lote anterior."
        try:
            ws = spreadsheet.worksheet(f"LOTE {target_num}")
        except gspread.WorksheetNotFound:
            return f"No se encontró la hoja LOTE {target_num}."
    else:
        try:
            lote_num = int(lote_ref)
            try:
                ws = spreadsheet.worksheet(f"LOTE {lote_num}")
            except gspread.WorksheetNotFound:
                return f"No se encontró la hoja LOTE {lote_num}."
        except ValueError:
            return (
                f"El lote '{lote_ref}' no es válido. "
                "Debe ser un número, 'actual' o 'anterior'. Vuelve a escribir el mensaje."
            )

    existing_row = _find_client_row(ws, cliente, tipo_cafe)
    if not existing_row:
        return (
            f"No se encontró una venta registrada a {cliente} de café {tipo_cafe.lower()}. "
            "Verifica que la entrega haya sido ingresada previamente."
        )

    # Validate that deuda (G = col 7) >= cantidad
    deuda = _parse_num(ws.cell(existing_row, 7).value)
    if deuda < cantidad:
        return (
            f"{cliente} tiene una deuda de {int(deuda)} fundas de {tipo_cafe.lower()}, "
            f"pero se intenta registrar un pago de {cantidad}. "
            "Verifica la cantidad e intenta de nuevo."
        )

    current_e = int(_parse_num(ws.cell(existing_row, 5).value))
    ws.update_cell(existing_row, 5, current_e + cantidad)

    _append_history(ws, spreadsheet, now, "Pago", cliente, cantidad, item.get("_raw", ""))
    return None


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

        if text.strip().lower().startswith("/ayuda"):
            send_message(
                chat_id,
                "Soy un bot para registrar gastos e inventario de café. Acepto estos mensajes:\n\n"
                "💸 *Gasto*\n'gasté 10 en pan', '50 frutería', 'kfc 25'\n\n"
                "📦 *Ingreso de stock*\n'50 fundas de café molido del lote 3', '20 fundas en grano lote actual'\n\n"
                "🚚 *Entrega de café*\n'30 fundas a Carloko a $3', 'entregué 15 fundas a Merceditas'\n\n"
                "💰 *Pago de café*\n'Carloko pagó 10 fundas', 'Merceditas depositó 5 del lote anterior'",
            )
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return

        # Single shared client for all sheet operations
        try:
            sheets_client = get_gspread_client()
        except Exception:
            send_message(chat_id, "Hubo un error, inténtalo nuevamente.")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return

        # Classify
        try:
            reply = classify_message(text, now)
        except Exception as e:
            error_reply = f"ERROR_MODELO: {e}"
            try:
                log_to_sheet(sheets_client, text, error_reply, now)
            except Exception:
                pass
            if str(e) == "SIN_CREDITOS":
                send_message(chat_id, "La cuenta de Anthropic no tiene créditos disponibles. Recarga la cuenta para continuar.")
            else:
                send_message(chat_id, "Ha ocurrido un error con el modelo. Inténtalo más tarde.")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return

        try:
            log_to_sheet(sheets_client, text, reply, now)
        except Exception:
            send_message(chat_id, "Hubo un error, inténtalo nuevamente.")

        try:
            for item in parse_reply_jsons(reply):
                item["_raw"] = text
                tipo = item.get("tipo")

                if tipo == "gasto":
                    if not item.get("monto") or not item.get("descripcion"):
                        send_message(
                            chat_id,
                            "Tu mensaje de tipo gasto no tiene un monto y/o descripción y no fue ingresado al archivo. "
                            "Vuelve a escribir el mensaje con la información correcta.",
                        )
                    else:
                        log_gasto(sheets_client, item, now)
                        desc = item.get("descripcion", "")
                        desc_cap = desc[0].upper() + desc[1:] if desc else ""
                        send_message(chat_id, f"Gasto {desc_cap} de ${float(item.get('monto')):.2f} ingresado.")

                elif tipo == "inventario_lote":
                    error, lote_num = log_inventario_lote(sheets_client, item, now)
                    if error:
                        send_message(chat_id, error)
                    else:
                        tipo_cafe_disp = _normalize_tipo_cafe(item.get("tipo_cafe", "molido")) or "café"
                        send_message(
                            chat_id,
                            f"Lote {lote_num}: {item.get('cantidad')} fundas de {tipo_cafe_disp.lower()} ingresadas.",
                        )

                elif tipo == "inventario_entrega":
                    error = log_inventario_entrega(sheets_client, item, now)
                    if error:
                        send_message(chat_id, error)
                    else:
                        send_message(
                            chat_id,
                            f"Entrega de {item.get('cantidad')} fundas a {item.get('cliente')} registrada.",
                        )

                elif tipo == "inventario_pago":
                    error = log_inventario_pago(sheets_client, item, now)
                    if error:
                        send_message(chat_id, error)
                    else:
                        send_message(
                            chat_id,
                            f"Pago de {item.get('cantidad')} fundas de {item.get('cliente')} registrado.",
                        )

                elif tipo == "desconocido":
                    send_message(
                        chat_id,
                        "No pude entender el mensaje. Se aceptan mensajes de:\n"
                        "• Gasto (ej. 'gasté 10 en pan')\n"
                        "• Ingreso de stock (ej. '50 fundas de café molido del lote 3')\n"
                        "• Entrega de café (ej. '20 fundas a Carloko a $3')\n"
                        "• Pago de café (ej. 'Carloko pagó 10 fundas')",
                    )

        except Exception:
            send_message(chat_id, "Hubo un error, inténtalo nuevamente.")

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")
