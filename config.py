"""
Конфигурация Brain-Do Bot.
Все настройки и константы собраны здесь. Секреты — только из переменных окружения.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# ─── Telegram ────────────────────────────────────────────────────────────────
BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "")

# ─── Groq (Llama 4 Scout) ────────────────────────────────────────────────────
GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL: str = os.environ.get("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
GROQ_BASE_URL: str = "https://api.groq.com/openai/v1/chat/completions"

# ─── Публичный адрес бота (нужен для OAuth Яндекса) ─────────────────────────
PUBLIC_URL: str = os.environ.get(
    "PUBLIC_URL", "https://brain-do-bot-production.up.railway.app"
).rstrip("/")

# ─── Яндекс Диск ─────────────────────────────────────────────────────────────
YADISK_CLIENT_ID: str = os.environ.get("YADISK_CLIENT_ID", "")
YADISK_CLIENT_SECRET: str = os.environ.get("YADISK_CLIENT_SECRET", "")
YADISK_REDIRECT_URI: str = os.environ.get("YADISK_REDIRECT_URI", f"{PUBLIC_URL}/yadisk/callback")
YADISK_MAX_FILES: int = 300    # сколько файлов максимум забираем с диска
YADISK_PAGE_SIZE: int = 8      # файлов на одной странице списка

# ─── Веб-сервер ──────────────────────────────────────────────────────────────
SERVER_PORT: int = int(os.environ.get("PORT", 8080))

# ─── Хранилище ───────────────────────────────────────────────────────────────
# На Railway подключи Volume и укажи DATA_DIR=/data — тогда статистика,
# настройки и авторизация Яндекса переживут перезапуск.
DATA_DIR: Path = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
STATS_FILE: Path = DATA_DIR / "stats.json"
YADISK_TOKENS_FILE: Path = DATA_DIR / "yadisk_tokens.json"
PERSISTENCE_FILE: Path = DATA_DIR / "bot_state.pickle"

# ─── Файлы ───────────────────────────────────────────────────────────────────
SUPPORTED_EXTENSIONS: tuple = (".docx", ".doc", ".rtf", ".odt", ".txt")
MAX_FILE_SIZE: int = 20 * 1024 * 1024  # лимит Telegram Bot API на скачивание

# ─── ИИ чат ──────────────────────────────────────────────────────────────────
AI_MAX_HISTORY: int = 10
AI_TEMPERATURE: float = 0.7
AI_MAX_TOKENS: int = 1000
AI_CONTEXT_MAX_CHARS: int = 6000  # сколько текста загруженного файла отдаём ИИ
AI_SYSTEM_PROMPT: str = (
    "Ты умный помощник для игры Brain-Do (интеллектуальная викторина). "
    "Помогаешь с вопросами, ответами, форматированием файлов. "
    "Отвечаешь на русском языке. Дружелюбный и краткий."
)

# ─── Список вопросов ─────────────────────────────────────────────────────────
QUESTION_PREVIEW_MAX_LEN: int = 90
QUESTION_LIST_PAGE_CHARS: int = 3500

# ─── Настройки презентации по умолчанию ─────────────────────────────────────
DEFAULT_SETTINGS: dict = {
    "theme": "white",      # white | dark
    "numbering": "seq",    # seq (1..N) | orig (как в файле) | none
    "shuffle": False,
    "pdf": False,          # дополнительно прислать PDF
    "instant": False,      # сразу делать презентацию после загрузки файла
}

SIGNATURE: str = "powered by Nikita to папа ❤️"
