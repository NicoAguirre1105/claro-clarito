from flask import Flask, request
import httpx
import os
from dotenv import load_dotenv

load_dotenv('/home/nicof1105/telegram_bot/.env')

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

PROXY = "http://proxy.server:3128"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "")
    
    if chat_id and text:
        with httpx.Client(proxy=PROXY) as client:
            client.post(f"{TELEGRAM_API}/sendMessage", json={
                "chat_id": chat_id,
                "text": f"Mensaje recibido: {text}"
            })
    
    return {"ok": True}

@app.route("/")
def health():
    return "running"