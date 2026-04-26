"""
Brain-Do Bot
Никита → папа ❤️
"""

import os
import logging
import random
import tempfile
import json
import requests
import threading
import asyncio
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from parser import parse_questions
from generator import generate_presentation

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Статистика ──────────────────────────────────────────────────────────────
STATS_FILE = "/tmp/stats.json"

def load_stats():
    try:
        with open(STATS_FILE) as f:
            return json.load(f)
    except:
        return {"total_presentations": 0, "total_questions": 0}

def save_stats(s):
    try:
        with open(STATS_FILE, "w") as f:
            json.dump(s, f)
    except:
        pass

stats = load_stats()

# ─── Главное меню (Reply кнопки) ─────────────────────────────────────────────
def main_menu():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📊 Статистика"), KeyboardButton("🤖 Поговорить с ИИ")],
        [KeyboardButton("❓ Помощь")],
    ], resize_keyboard=True)

# ─── /start ──────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["ai_mode"] = False
    await update.message.reply_text(
        "👋 *Привет!* Я помогаю делать презентации для Brain-Do.\n\n"
        "📎 Просто пришли мне *.docx файл* с вопросами и ответами — "
        "и я за несколько секунд сделаю готовую презентацию!\n\n"
        "Используй кнопки внизу или команду /help для справки.",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )

# ─── /help ───────────────────────────────────────────────────────────────────
async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Как пользоваться:*\n\n"
        "1. Пришли .docx файл с вопросами и ответами\n"
        "2. Я найду все пары вопрос/ответ\n"
        "3. Выбери настройки (тема, таймер, порядок)\n"
        "4. Получи готовый .pptx файл!\n\n"
        "📋 *Команды:*\n"
        "/start — главное меню\n"
        "/help — эта справка\n\n"
        "💡 *Формат файла:*\n"
        "Нужны слова *Вопрос* и *Ответ*. "
        "Комментарий — необязателен. Формат может быть разным — разберусь!\n\n"
        "✏️ *Сложный вопрос:*\n"
        "Добавь \\* в начало — он будет выделен красным на слайде.\n\n"
        "🤖 *ИИ-ассистент:*\n"
        "Нажми кнопку «Поговорить с ИИ» — можно задавать вопросы по файлам или просто поболтать.",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )

# ─── /stats ──────────────────────────────────────────────────────────────────
async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await show_stats(update.message)

async def show_stats(message):
    await message.reply_text(
        f"📊 *Статистика Brain-Do бота:*\n\n"
        f"🎯 Презентаций создано: *{stats['total_presentations']}*\n"
        f"❓ Вопросов обработано: *{stats['total_questions']}*\n\n"
        f"_powered by Nikita to папа ❤️_",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )

# ─── Обработка текстовых сообщений (кнопки меню + ИИ режим) ─────────────────
async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    # Кнопки главного меню
    if text == "📊 Статистика":
        await show_stats(update.message)
        return

    if text == "❓ Помощь":
        await cmd_help(update, ctx)
        return

    if text == "🤖 Поговорить с ИИ":
        ctx.user_data["ai_mode"] = True
        ctx.user_data["ai_history"] = []
        await update.message.reply_text(
            "🤖 *ИИ-ассистент активирован!*\n\n"
            "Задавай любые вопросы — по Brain-Do, по файлам, или просто поболтаем.\n\n"
            "Чтобы выйти из режима ИИ — напиши /start",
            parse_mode="Markdown"
        )
        return

    if text == "❌ Выйти из ИИ" or text == "/start":
        ctx.user_data["ai_mode"] = False
        await cmd_start(update, ctx)
        return

    # ИИ режим
    if ctx.user_data.get("ai_mode"):
        await handle_ai_chat(update, ctx, text)
        return

    # Обычное сообщение
    await update.message.reply_text(
        "Пришли мне .docx файл с вопросами и ответами 📎",
        reply_markup=main_menu()
    )

# ─── ИИ чат через Groq ───────────────────────────────────────────────────────
async def handle_ai_chat(update: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str):
    groq_key = os.environ.get("GROQ_API_KEY")
    if not groq_key:
        await update.message.reply_text("⚠️ ИИ недоступен — не настроен API ключ.")
        return

    # Инициализируем историю
    history = ctx.user_data.get("ai_history", [])
    history.append({"role": "user", "content": text})

    # Ограничиваем историю последними 10 сообщениями
    if len(history) > 10:
        history = history[-10:]

    await update.message.reply_text("🤔 Думаю...")

    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {groq_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "meta-llama/llama-4-scout-17b-16e-instruct",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Ты умный помощник для игры Brain-Do (интеллектуальная викторина). "
                            "Помогаешь с вопросами, ответами, форматированием файлов. "
                            "Отвечаешь на русском языке. Дружелюбный и краткий."
                        )
                    }
                ] + history,
                "temperature": 0.7,
                "max_tokens": 1000,
            },
            timeout=30
        )
        data = response.json()
        reply = data["choices"][0]["message"]["content"]

        history.append({"role": "assistant", "content": reply})
        ctx.user_data["ai_history"] = history

        exit_keyboard = ReplyKeyboardMarkup([
            [KeyboardButton("❌ Выйти из ИИ")]
        ], resize_keyboard=True)

        await update.message.reply_text(reply, reply_markup=exit_keyboard)

    except Exception as e:
        logger.error(f"AI chat error: {e}")
        await update.message.reply_text(
            "😕 Что-то пошло не так, попробуй ещё раз.",
            reply_markup=main_menu()
        )

# ─── Приём .docx файла ───────────────────────────────────────────────────────
async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc.file_name.endswith(".docx"):
        await update.message.reply_text("⚠️ Пожалуйста, пришли файл в формате .docx")
        return

    # Выходим из ИИ режима если были в нём
    ctx.user_data["ai_mode"] = False

    msg = await update.message.reply_text("⏳ Читаю файл...")

    file = await ctx.bot.get_file(doc.file_id)
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
        await file.download_to_drive(tmp.name)
        tmp_path = tmp.name

    await msg.edit_text("🔍 Ищу вопросы и ответы...")

    questions = parse_questions(tmp_path)
    os.unlink(tmp_path)

    if not questions:
        await msg.edit_text(
            "❌ Не нашёл вопросы в файле.\n\n"
            "Убедись что в документе есть слова *Вопрос* и *Ответ*.",
            parse_mode="Markdown"
        )
        return

    ctx.user_data["questions"] = questions
    ctx.user_data["settings"] = {
        "theme": "white",
        "shuffle": False,
        "timer": None,
        "numbering": True,
    }

    await msg.edit_text(
        f"✅ Нашёл *{len(questions)}* вопросов!\n\nВыбери тему оформления:",
        parse_mode="Markdown",
        reply_markup=theme_keyboard()
    )

# ─── Клавиатуры ──────────────────────────────────────────────────────────────
def theme_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⬜ Белая", callback_data="theme_white"),
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
        [InlineKeyboardButton(timer_label, callback_data="timer_menu")],
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

    if data.startswith("theme_"):
        settings["theme"] = data.replace("theme_", "")
        ctx.user_data["settings"] = settings
        theme_names = {"white": "⬜ Белая", "dark": "⬛ Тёмная", "blue": "🔵 Синяя", "red": "🔴 Красная"}
        await query.edit_message_text(
            f"✅ Тема: *{theme_names[settings['theme']]}*\n\nНастрой параметры:",
            parse_mode="Markdown",
            reply_markup=options_keyboard(settings)
        )

    elif data == "toggle_shuffle":
        settings["shuffle"] = not settings["shuffle"]
        ctx.user_data["settings"] = settings
        await query.edit_message_reply_markup(reply_markup=options_keyboard(settings))

    elif data == "toggle_numbering":
        settings["numbering"] = not settings["numbering"]
        ctx.user_data["settings"] = settings
        await query.edit_message_reply_markup(reply_markup=options_keyboard(settings))

    elif data == "timer_menu":
        await query.edit_message_text("⏱ Выбери время на вопрос:", reply_markup=timer_keyboard())

    elif data.startswith("timer_"):
        val = data.replace("timer_", "")
        settings["timer"] = None if val == "off" else int(val)
        ctx.user_data["settings"] = settings
        await query.edit_message_text("Настрой параметры:", reply_markup=options_keyboard(settings))

    elif data == "back_options":
        await query.edit_message_text("Настрой параметры:", reply_markup=options_keyboard(settings))

    elif data == "generate":
        await query.edit_message_text(
            "⏳ Читаю вопросы...\n"
            "🔍 Проверяю через ИИ...\n"
            "⚙️ Генерирую слайды..."
        )
        await do_generate(query.message, ctx, questions, settings)

    elif data == "show_list":
        last_qs = ctx.user_data.get("last_questions", [])
        if not last_qs:
            await query.edit_message_text("Список недоступен.", reply_markup=None)
            return
        lines = []
        for q in last_qs:
            lines.append(f"*{q['number']}.* {q['question'][:80]}{'...' if len(q['question']) > 80 else ''}\n↳ _{q['answer']}_")
        text = "\n\n".join(lines)
        # Telegram ограничение 4096 символов
        if len(text) > 3800:
            text = text[:3800] + "\n\n_...и ещё вопросы_"
        await query.edit_message_text(
            f"📋 *Список вопросов ({len(last_qs)} шт):*\n\n{text}",
            parse_mode="Markdown",
            reply_markup=None
        )

    elif data == "hide_list":
        await query.edit_message_text("Окей! 👍", reply_markup=None)

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
        save_stats(stats)

        count = len(questions)
        theme_names = {"white": "Белая", "dark": "Тёмная", "blue": "Синяя", "red": "Красная"}
        theme = theme_names.get(settings["theme"], settings["theme"])

        caption = (
            f"✅ *Готово!*\n\n"
            f"📊 Вопросов: *{count}*\n"
            f"🎨 Тема: {theme}"
            + (" 🔀 перемешано" if settings["shuffle"] else "")
            + (f" ⏱ {settings['timer']}с" if settings["timer"] else "")
            + f"\n\n🤖 Все вопросы проверены ИИ"
            + f"\n\n_powered by Nikita to папа ❤️_"
        )

        with open(out_path, "rb") as f:
            await message.reply_document(
                document=f,
                filename="presentation.pptx",
                caption=caption,
                parse_mode="Markdown",
            )

        # Предлагаем показать список вопросов
        ctx.user_data["last_questions"] = questions
        await message.reply_text(
            "Хочешь посмотреть список найденных вопросов?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 Показать список", callback_data="show_list")],
                [InlineKeyboardButton("✖️ Не надо", callback_data="hide_list")],
            ])
        )
    except Exception as e:
        logger.error(f"Generation error: {e}")
        await message.reply_text(
            "😕 Что-то пошло не так при генерации. Попробуй отправить файл ещё раз.",
            reply_markup=main_menu()
        )
    finally:
        try:
            os.unlink(out_path)
        except:
            pass

# ─── Веб-сервер для Railway ───────────────────────────────────────────────────
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("Бот запущен!")

    async with app:
        await app.start()
        await app.updater.start_polling()
        while True:
            await asyncio.sleep(3600)

if __name__ == "__main__":
    threading.Thread(target=run_web_server, daemon=True).start()
    asyncio.run(run_bot())
