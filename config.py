"""
Конфигурация Brain-Do Bot.
Все настройки и константы собраны здесь.
"""

import os

# ─── Telegram ────────────────────────────────────────────────────────────────
BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "")

# ─── Groq (Llama 4 Scout) ────────────────────────────────────────────────────
GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL: str = "meta-llama/llama-4-scout-17b-16e-instruct"
GROQ_BASE_URL: str = "https://api.groq.com/openai/v1/chat/completions"

# ─── Яндекс Диск ─────────────────────────────────────────────────────────────
YADISK_CLIENT_ID: str = os.environ.get("YADISK_CLIENT_ID", "")
YADISK_CLIENT_SECRET: str = os.environ.get("YADISK_CLIENT_SECRET", "")
YADISK_REDIRECT_URI: str = "https://brain-do-bot-production.up.railway.app/yadisk/callback"
YADISK_MAX_FILES: int = 20
YADISK_MAX_FILES_PER_DIR: int = 5

# ─── Веб-сервер ──────────────────────────────────────────────────────────────
SERVER_PORT: int = int(os.environ.get("PORT", 8080))

# ─── Хранилище ───────────────────────────────────────────────────────────────
STATS_FILE: str = "/tmp/stats.json"
YADISK_TOKENS_FILE: str = "/tmp/yadisk_tokens.json"

# ─── ИИ чат ──────────────────────────────────────────────────────────────────
AI_MAX_HISTORY: int = 10
AI_TEMPERATURE: float = 0.7
AI_MAX_TOKENS: int = 1000
AI_SYSTEM_PROMPT: str = (
    "Ты умный помощник для игры Brain-Do (интеллектуальная викторина). "
    "Помогаешь с вопросами, ответами, форматированием файлов. "
    "Отвечаешь на русском языке. Дружелюбный и краткий."
)

# ─── Список вопросов ─────────────────────────────────────────────────────────
QUESTION_PREVIEW_MAX_LEN: int = 80
QUESTION_LIST_MAX_CHARS: int = 3800

# ─── Настройки презентации по умолчанию ─────────────────────────────────────
DEFAULT_SETTINGS: dict = {
    "theme": "white",
    "shuffle": False,
    "numbering": True,
}
