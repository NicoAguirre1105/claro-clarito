import json, os
import httpx
from http.server import BaseHTTPRequestHandler

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")

def handler(request):
    body = request.json()
    message = body.get("message", {})
    text = message.get("text", "")
    chat_id = message["chat"]["id"]

    httpx.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": f"Mensaje recibido: {text}"},
        timeout=10,
    )

    return Response("ok", status=200)