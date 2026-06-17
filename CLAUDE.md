# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Claro-Clarito** is a Telegram bot deployed as a Python serverless function on Vercel. It classifies incoming messages using Claude (Haiku) and writes structured data to Google Sheets. The use case is personal expense and coffee-inventory tracking for a user in Ecuador.

## Architecture

Everything lives in `api/index.py` — a single Python file that Vercel treats as a serverless function:

- **Webhook handler**: `handler(BaseHTTPRequestHandler)` — receives Telegram `POST` updates
- **Access control**: validates `chat_id` against `ALLOWED_CHAT_IDS` env var before processing
- **Classification**: `classify_message()` calls Claude Haiku with a Spanish system prompt and returns a JSON string
- **Telegram response**: `send_message()` POSTs back to the Telegram Bot API

There is currently no Google Sheets write logic in the main handler — the `gspread` dependency is installed but not yet wired up.

## Environment Variables

Defined in `.env` (not committed to production — use Vercel environment variables):

| Variable | Purpose |
|---|---|
| `TELEGRAM_TOKEN` | Bot token from BotFather |
| `ANTHROPIC_API_KEY` | API key for Claude |
| `ALLOWED_CHAT_IDS` | Comma-separated list of authorized Telegram chat IDs |
| `GPC_SERVICE_ACCOUNT_JSON` | Google service account JSON (stringified) |
| `SHEET_ID_GASTOS` | Google Sheet ID for expenses |
| `SHEET_ID_INVENTARIO` | Google Sheet ID for coffee inventory |
| `SHEET_ID_LOG` | Google Sheet ID for raw message log |

## Message Types

The system prompt instructs Claude to classify messages into these JSON types:

- `gasto` — money spent (amount, description, category, date)
- `inventario_entrega` — coffee bags delivered to a client
- `inventario_pago` — payment received from a client
- `inventario_lote` — new coffee batch ingested
- `consulta` — question about expenses or inventory
- `desconocido` — anything else

Dates are always `DD-MM-YYYY`, amounts are decimals, location context is Ecuador (America/Guayaquil timezone).

## Deployment

Deployed on Vercel. The `api/` directory is auto-detected as serverless functions. The Telegram webhook must be pointed at `https://<vercel-domain>/api/index`.

Dependencies are in `requirements.txt` — Vercel installs them automatically.

## Local Development

There is no local dev server setup. To test locally, run the handler directly or mock HTTP requests. The bot only responds to Telegram webhook POSTs, so testing requires either ngrok + a real bot token or unit-testing `classify_message()` in isolation.
