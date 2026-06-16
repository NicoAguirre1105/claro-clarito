from http.server import BaseHTTPRequestHandler
import json, os, httpx

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]

def send_message(chat_id: int, text: str):
    httpx.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=10,
    )

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length  = int(self.headers.get("Content-Length", 0))
        body    = json.loads(self.rfile.read(length))
        message = body.get("message", {})
        text    = message.get("text", "")
        chat_id = message["chat"]["id"]

        send_message(chat_id, f"Mensaje recibido: {text}")

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")