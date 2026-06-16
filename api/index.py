import json, os
import httpx
from http.server import BaseHTTPRequestHandler

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        message = body.get("message", {})
        text = message.get("text", "")
        chat_id = message["chat"]["id"]

        httpx.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": f"Mensaje recibido: {text}"},
            timeout=10,
        )

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")