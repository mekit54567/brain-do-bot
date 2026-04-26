"""
Brain-Do Telegram Bot
Никита → папа ❤️
"""

import os
import logging
import random
import tempfile
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from parser import parse_questions
from generator import generate_presentation

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Статистика (в памяти) ───────────────────────────────────────────────────
stats = {"total_presentations": 0, "total_questions": 0}

# ─── /start ──────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я помогаю делать презентации для Brain-Do.\n\n"
        "Просто пришли мне Word-файл (.docx) с вопросами и ответами — "
        "и я за несколько секунд сделаю готовую презентацию!\n\n"
        "Введи /help чтобы узнать все возможности."
    )

# ─── /help ───────────────────────────────────────────────────────────────────
async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Как пользоваться:*\n\n"
        "1. Пришли .docx файл с вопросами и ответами\n"
        "2. Я найду все пары вопрос/ответ\n"
        "3. Ты выбираешь настройки (тема, таймер, порядок)\n"
        "4. Получаешь готовый .pptx файл!\n\n"
        "📋 *Команды:*\n"
        "/start — начало\n"
        "/help — эта справка\n"
        "/stats — статистика\n\n"
        "💡 *Формат файла:*\n"
        "В документе должны быть слова *Вопрос* и *Ответ*. "
        "Комментарий — необязателен. Формат может быть разным, я разберусь!\n\n"
        "✏️ *Пометить сложный вопрос:*\n"
        "Добавь \\* в начало вопроса — он будет выделен красным цветом на слайде.",
        parse_mode="Markdown"
    )

# ─── /stats ──────────────────────────────────────────────────────────────────
async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"📊 *Статистика:*\n\n"
        f"Презентаций создано: *{stats['total_presentations']}*\n"
        f"Вопросов обработано: *{stats['total_questions']}*\n\n"
        f"_powered by Nikita to папа ❤️_",
        parse_mode="Markdown"
    )

# ─── Приём .docx файла ───────────────────────────────────────────────────────
async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc.file_name.endswith(".docx"):
        await update.message.reply_text("⚠️ Пожалуйста, пришли файл в формате .docx")
        return

    await update.message.reply_text("⏳ Читаю файл...")

    # Скачиваем файл
    file = await ctx.bot.get_file(doc.file_id)
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
        await file.download_to_drive(tmp.name)
        tmp_path = tmp.name

    # Парсим вопросы
    questions = parse_questions(tmp_path)
    os.unlink(tmp_path)

    if not questions:
        await update.message.reply_text(
            "❌ Не нашёл вопросы в файле.\n\n"
            "Убедись что в документе есть слова *Вопрос* и *Ответ*.",
            parse_mode="Markdown"
        )
        return

    # Сохраняем вопросы в контексте пользователя
    ctx.user_data["questions"] = questions
    ctx.user_data["settings"] = {
        "theme": "white",
        "shuffle": False,
        "timer": None,
        "numbering": True,
    }

    await update.message.reply_text(
        f"✅ Нашёл *{len(questions)}* вопросов!\n\nВыбери тему оформления:",
        parse_mode="Markdown",
        reply_markup=theme_keyboard()
    )

# ─── Клавиатуры ──────────────────────────────────────────────────────────────
def theme_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⬜ Белая (классика)", callback_data="theme_white"),
            InlineKeyboardButton("⬛ Тёмная", callback_data="theme_dark"),
        ],
        [
            InlineKeyboardButton("🔵 Синяя", callback_data="theme_blue"),
            InlineKeyboardButton("🔴 Красная", callback_data="theme_red"),
        ],
    ])

def options_keyboard(settings: dict):
    shuffle_icon = "✅" if settings["shuffle"] else "☐"
    numbering_icon = "✅" if settings["numbering"] else "☐"

    timer_label = f"⏱ Таймер: {settings['timer']}с" if settings["timer"] else "⏱ Таймер: выкл"

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{shuffle_icon} Перемешать вопросы", callback_data="toggle_shuffle")],
        [InlineKeyboardButton(f"{numbering_icon} Нумерация вопросов", callback_data="toggle_numbering")],
        [
            InlineKeyboardButton(timer_label, callback_data="timer_menu"),
        ],
        [InlineKeyboardButton("🚀 Создать презентацию!", callback_data="generate")],
    ])

def timer_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("30с", callback_data="timer_30"),
            InlineKeyboardButton("60с", callback_data="timer_60"),
            InlineKeyboardButton("90с", callback_data="timer_90"),
        ],
        [InlineKeyboardButton("Без таймера", callback_data="timer_off")],
        [InlineKeyboardButton("← Назад", callback_data="back_options")],
    ])

# ─── Обработка кнопок ────────────────────────────────────────────────────────
async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    settings = ctx.user_data.get("settings", {})
    questions = ctx.user_data.get("questions", [])

    # Выбор темы
    if data.startswith("theme_"):
        settings["theme"] = data.replace("theme_", "")
        ctx.user_data["settings"] = settings
        theme_names = {"white": "⬜ Белая", "dark": "⬛ Тёмная", "blue": "🔵 Синяя", "red": "🔴 Красная"}
        await query.edit_message_text(
            f"✅ Тема: *{theme_names[settings['theme']]}*\n\nНастрой дополнительные параметры:",
            parse_mode="Markdown",
            reply_markup=options_keyboard(settings)
        )

    # Перемешать
    elif data == "toggle_shuffle":
        settings["shuffle"] = not settings["shuffle"]
        ctx.user_data["settings"] = settings
        await query.edit_message_reply_markup(reply_markup=options_keyboard(settings))

    # Нумерация
    elif data == "toggle_numbering":
        settings["numbering"] = not settings["numbering"]
        ctx.user_data["settings"] = settings
        await query.edit_message_reply_markup(reply_markup=options_keyboard(settings))

    # Меню таймера
    elif data == "timer_menu":
        await query.edit_message_text(
            "⏱ Выбери время на вопрос:",
            reply_markup=timer_keyboard()
        )

    elif data.startswith("timer_"):
        val = data.replace("timer_", "")
        settings["timer"] = None if val == "off" else int(val)
        ctx.user_data["settings"] = settings
        await query.edit_message_text(
            "Настрой дополнительные параметры:",
            reply_markup=options_keyboard(settings)
        )

    elif data == "back_options":
        await query.edit_message_text(
            "Настрой дополнительные параметры:",
            reply_markup=options_keyboard(settings)
        )

    # Генерация
    elif data == "generate":
        await query.edit_message_text("⚙️ Генерирую презентацию...")
        await do_generate(query.message, ctx, questions, settings)

# ─── Генерация презентации ────────────────────────────────────────────────────
async def do_generate(message, ctx, questions, settings):
    if settings["shuffle"]:
        questions = questions.copy()
        random.shuffle(questions)

    with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as tmp:
        out_path = tmp.name

    try:
        generate_presentation(questions, out_path, settings)

        stats["total_presentations"] += 1
        stats["total_questions"] += len(questions)

        count = len(questions)
        theme_names = {"white": "Белая", "dark": "Тёмная", "blue": "Синяя", "red": "Красная"}
        theme = theme_names.get(settings["theme"], settings["theme"])

        with open(out_path, "rb") as f:
            await message.reply_document(
                document=f,
                filename="presentation.pptx",
                caption=(
                    f"✅ Готово! {count} вопросов, тема: {theme}"
                    + (" 🔀" if settings["shuffle"] else "")
                    + (f" ⏱{settings['timer']}с" if settings["timer"] else "")
                    + "\n\n_powered by Nikita to папа ❤️_"
                ),
                parse_mode="Markdown"
            )
    finally:
        os.unlink(out_path)

# ─── Простой веб-сервер для Render ───────────────────────────────────────────
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import asyncio

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *args):
        pass

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()

# ─── Запуск ──────────────────────────────────────────────────────────────────
async def run_bot():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError("Задай переменную окружения BOT_TOKEN")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("Бот запущен!")

    async with app:
        await app.start()
        await app.updater.start_polling()
        # Держим бота запущенным
        while True:
            await asyncio.sleep(3600)

if __name__ == "__main__":
    # Запускаем веб-сервер в фоне (для Render)
    threading.Thread(target=run_web_server, daemon=True).start()
    asyncio.run(run_bot())
