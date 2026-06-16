import json, os
import httpx
from http.server import BaseHTTPRequestHandler

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

SYSTEM_PROMPT = """
Eres un asistente que clasifica mensajes de gastos e inventario de café y devuelves SIEMPRE un JSON válido, sin texto adicional, sin backticks, sin explicaciones.
## TIPOS DE MENSAJE
### 1. gasto
Mensajes sobre dinero gastado. El formato puede variar: "50 frutería", "frutería 50", "gasté 50 en la frutería". La ubicación es Ecuador, así que las tiendas pueden ser buscadas para ver la categoría.
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
{"tipo": "inventario_nuevo_lote", "fecha": "2026-06-15", "lote":"15", "cantidad":"15"}
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
- Los montos siempre en número decimal (sin símbolo $).
- Devuelve ÚNICAMENTE el JSON, nada más.
- Cualquier mensaje que no corresponda claramente a gasto, inventario_entrega, inventario_pago, inventario_nuevo_lote o consulta — incluyendo saludos, mensajes de prueba, texto sin sentido — SIEMPRE devuelve: {"tipo": "desconocido", "mensaje_original": "texto del mensaje"} NUNCA devuelvas texto plano. SIEMPRE JSON.
"""

def classify_message(text: str) -> dict:
    response = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-haiku-4-5",
            "max_tokens": 512,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": text}],
        },
        timeout=15,
    )
    print("STATUS:", response.status_code)
    print("BODY:", response.text)
    
    raw = response.json()["content"][0]["text"].strip()
    return json.loads(raw)

def format_response(data: dict) -> str:
    tipo = data.get("tipo")

    if tipo == "gasto":
        return (
            f"💸 *Gasto registrado*\n"
            f"Monto: ${data.get('monto')}\n"
            f"Descripción: {data.get('descripcion')}\n"
            f"Categoría: `{data.get('categoria')}`\n"
            f"Fecha: {data.get('fecha')}"
        )
    elif tipo == "inventario_entrega":
        pagado = "Sí" if data.get("pagado_en_momento") else "No"
        return (
            f"📦 *Entrega de café*\n"
            f"Cliente: {data.get('proveedor')}\n"
            f"Cantidad: {data.get('cantidad')} fundas\n"
            f"Tipo: {data.get('tipo_cafe')}\n"
            f"Precio unitario: ${data.get('precio_unitario', 'ya registrado')}\n"
            f"Pagado en momento: {pagado}\n"
            f"Fecha: {data.get('fecha')}"
        )
    elif tipo == "inventario_pago":
        return (
            f"💰 *Pago recibido*\n"
            f"Cliente: {data.get('proveedor')}\n"
            f"Cantidad: {data.get('cantidad')} fundas\n"
            f"Lote: {data.get('lote')}\n"
            f"Fecha: {data.get('fecha')}"
        )
    elif tipo == "inventario_nuevo_lote":
        return (
            f"🏭 *Ingreso de lote*\n"
            f"Lote: {data.get('lote')}\n"
            f"Cantidad: {data.get('cantidad')} fundas\n"
            f"Fecha: {data.get('fecha')}"
        )
    elif tipo == "consulta":
        return f"🔍 *Consulta detectada*\n{data.get('pregunta')}\n\n_(las consultas aún no están implementadas)_"
    else:
        return f"❓ No entendí ese mensaje.\n`{data.get('mensaje_original', '')}`"

def send_message(chat_id: int, text: str):
    httpx.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
        timeout=10,
    )

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        message = body.get("message", {})
        text = message.get("text", "")
        chat_id = message["chat"]["id"]

        try:
            classified = classify_message(text)
            reply = format_response(classified)
        except Exception as e:
            reply = f"⚠️ Error al procesar el mensaje: {str(e)}"

        send_message(chat_id, reply)

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")