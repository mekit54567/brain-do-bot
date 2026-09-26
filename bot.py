"""
Brain-Do Bot — точка входа.
Никита → папа ❤️
"""

import asyncio
import html
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    PersistenceInput,
    PicklePersistence,
    TypeHandler,
    filters,
)

import admin
import handlers
import session
import yadisk
from access import Access
from config import ACCESS_MODE, ADMIN_IDS, BOT_TOKEN, DATA_DIR, PERSISTENCE_FILE, SERVER_PORT
from history import History
from storage import Stats, TokenStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)  # не спамим логами каждого запроса
logger = logging.getLogger(__name__)

COMMANDS = [
    BotCommand("start", "Главное меню"),
    BotCommand("settings", "Настройки презентации"),
    BotCommand("history", "Последние пакеты"),
    BotCommand("yadisk", "Файлы с Яндекс Диска"),
    BotCommand("stats", "Статистика"),
    BotCommand("help", "Как пользоваться"),
    BotCommand("id", "Мой Telegram ID"),
]

PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Brain-Do Bot</title>
<style>body{{font-family:system-ui,sans-serif;display:flex;min-height:100vh;margin:0;
align-items:center;justify-content:center;background:#f5f5f5;color:#222}}
main{{background:#fff;padding:32px;border-radius:16px;max-width:420px;text-align:center;
box-shadow:0 4px 24px rgba(0,0,0,.08)}}h1{{font-size:48px;margin:0}}a{{color:#d82819}}</style>
</head><body><main><h1>{icon}</h1><p>{text}</p>{link}</main></body></html>"""


# ─── Веб-сервер ──────────────────────────────────────────────────────────────

class WebHandler(BaseHTTPRequestHandler):
    """Health check и OAuth callback Яндекс Диска."""

    app: Application
    loop: asyncio.AbstractEventLoop

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/yadisk/callback":
            self._handle_yadisk_callback(parsed)
        else:
            self._respond(200, b"OK", "text/plain")

    def do_HEAD(self) -> None:
        self._respond(200, b"", "text/plain")

    def _handle_yadisk_callback(self, parsed) -> None:
        params = parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        owner = yadisk.pop_state(params.get("state", [""])[0] or "")

        if not code or not owner:
            self._page(400, "⚠️", "Ссылка для входа устарела. Вернись в Telegram и нажми «📁 Яндекс Диск» ещё раз.")
            return

        user_id, chat_id = owner
        token = yadisk.exchange_code_for_token(code)
        if not token:
            self._page(400, "😕", "Яндекс не выдал доступ. Попробуй войти ещё раз.")
            return

        session.tokens.set(user_id, token)
        asyncio.run_coroutine_threadsafe(
            handlers.notify_yadisk_connected(self.app, user_id, chat_id), self.loop
        )
        self._page(200, "✅", "Яндекс Диск подключён! Список файлов уже ждёт тебя в Telegram — эту вкладку можно закрыть.")

    def _page(self, status: int, icon: str, text: str) -> None:
        username = self.app.bot.username if self.app.bot else None
        link = f'<p><a href="https://t.me/{username}">Вернуться в бота →</a></p>' if username else ""
        body = PAGE.format(icon=icon, text=html.escape(text), link=link).encode("utf-8")
        self._respond(status, body, "text/html; charset=utf-8")

    def _respond(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass  # Отключаем стандартные логи HTTP сервера


def start_web_server(app: Application, loop: asyncio.AbstractEventLoop) -> None:
    """HTTP сервер в отдельном потоке (Railway ждёт открытый порт)."""
    WebHandler.app = app
    WebHandler.loop = loop
    server = ThreadingHTTPServer(("0.0.0.0", SERVER_PORT), WebHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info("Веб-сервер запущен на порту %d", SERVER_PORT)


# ─── Бот ─────────────────────────────────────────────────────────────────────

async def post_init(app: Application) -> None:
    await app.bot.set_my_commands(COMMANDS)
    start_web_server(app, asyncio.get_running_loop())
    logger.info("Бот @%s запущен!", app.bot.username)


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Не задана переменная окружения BOT_TOKEN")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    session.stats = Stats()
    session.tokens = TokenStore()
    session.history = History()
    session.access = Access()
    if ACCESS_MODE == "private" and not ADMIN_IDS:
        logger.warning("ACCESS_MODE=private, но ADMIN_IDS пуст — одобрять запросы доступа некому")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .persistence(PicklePersistence(
            filepath=PERSISTENCE_FILE,
            store_data=PersistenceInput(chat_data=False, bot_data=False, callback_data=False),
            update_interval=30,
        ))
        .concurrent_updates(True)  # пока один ждёт презентацию, остальные не стоят в очереди
        .post_init(post_init)
        .build()
    )

    # Проверка доступа — раньше всех обработчиков (группа -1)
    app.add_handler(TypeHandler(Update, admin.access_guard), group=-1)

    app.add_handler(CommandHandler("start", handlers.cmd_start))
    app.add_handler(CommandHandler("help", handlers.cmd_help))
    app.add_handler(CommandHandler("settings", handlers.cmd_settings))
    app.add_handler(CommandHandler("stats", handlers.cmd_stats))
    app.add_handler(CommandHandler("yadisk", handlers.cmd_yadisk))
    app.add_handler(CommandHandler("history", handlers.cmd_history))
    app.add_handler(CommandHandler("id", admin.cmd_id))
    app.add_handler(CommandHandler("invite", admin.cmd_invite))
    app.add_handler(CommandHandler("users", admin.cmd_users))
    app.add_handler(MessageHandler(filters.Document.ALL, handlers.handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_text))
    app.add_handler(CallbackQueryHandler(handlers.handle_callback))
    app.add_error_handler(handlers.on_error)

    app.run_polling(allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY])


if __name__ == "__main__":
    main()
