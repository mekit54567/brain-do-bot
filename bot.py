"""
Brain-Do Bot — точка входа.
Никита → папа ❤️
"""

import asyncio
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

import handlers
from config import BOT_TOKEN, SERVER_PORT
from storage import load_stats, load_yadisk_tokens, save_yadisk_tokens
from yadisk import exchange_code_for_token

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ─── Веб-сервер ──────────────────────────────────────────────────────────────

class WebHandler(BaseHTTPRequestHandler):
    """HTTP обработчик для health check и OAuth callback Яндекс Диска."""

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == "/yadisk/callback":
            self._handle_yadisk_callback(parsed)
        else:
            self._respond(200, b"OK")

    def _handle_yadisk_callback(self, parsed) -> None:
        """Обрабатывает OAuth callback от Яндекса."""
        params = parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        user_id = params.get("state", [None])[0]

        if code and user_id:
            token = exchange_code_for_token(code)
            if token:
                tokens = load_yadisk_tokens()
                tokens[user_id] = token
                save_yadisk_tokens(tokens)
                handlers.yadisk_tokens = tokens

                self._respond(
                    200,
                    "✅ Авторизация прошла успешно! Вернись в Telegram и нажми '❓ Уже авторизовался'".encode("utf-8"),
                )
                return

        self._respond(400, b"Error: invalid code or state")

    def _respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass  # Отключаем стандартные логи HTTP сервера


def start_web_server() -> None:
    """Запускает HTTP сервер в отдельном потоке."""
    server = HTTPServer(("0.0.0.0", SERVER_PORT), WebHandler)
    logger.info("Веб-сервер запущен на порту %d", SERVER_PORT)
    server.serve_forever()


# ─── Бот ─────────────────────────────────────────────────────────────────────

async def run_bot() -> None:
    """Инициализирует и запускает Telegram бота."""
    if not BOT_TOKEN:
        raise RuntimeError("Не задана переменная окружения BOT_TOKEN")

    # Загружаем глобальное состояние в handlers
    handlers.stats = load_stats()
    handlers.yadisk_tokens = load_yadisk_tokens()

    app = Application.builder().token(BOT_TOKEN).build()

    # Регистрируем обработчики
    app.add_handler(CommandHandler("start", handlers.cmd_start))
    app.add_handler(CommandHandler("help", handlers.cmd_help))
    app.add_handler(CommandHandler("stats", handlers.cmd_stats))
    app.add_handler(CommandHandler("yadisk", handlers.cmd_yadisk))
    app.add_handler(MessageHandler(filters.Document.ALL, handlers.handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_text))
    app.add_handler(CallbackQueryHandler(handlers.handle_callback))

    logger.info("Бот запущен!")

    async with app:
        await app.start()
        await app.updater.start_polling()
        while True:
            await asyncio.sleep(3600)


# ─── Точка входа ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    threading.Thread(target=start_web_server, daemon=True).start()
    asyncio.run(run_bot())
