"""
Обработчики команд и сообщений Telegram бота.
"""

import logging
import os
import random
import tempfile

import requests
from telegram import Update
from telegram.ext import ContextTypes

import yadisk as yadisk_api
from config import (
    AI_MAX_HISTORY,
    AI_MAX_TOKENS,
    AI_SYSTEM_PROMPT,
    AI_TEMPERATURE,
    DEFAULT_SETTINGS,
    GROQ_BASE_URL,
    GROQ_MODEL,
    QUESTION_LIST_MAX_CHARS,
    QUESTION_PREVIEW_MAX_LEN,
)
from generator import generate_presentation
from keyboards import (
    exit_ai_menu,
    main_menu,
    options_keyboard,
    question_list_keyboard,
    theme_keyboard,
    yadisk_auth_keyboard,
    yadisk_files_keyboard,
)
from parser import parse_questions
from storage import load_yadisk_tokens, save_stats, save_yadisk_tokens

logger = logging.getLogger(__name__)

# Глобальное состояние (инициализируется в bot.py)
stats: dict = {}
yadisk_tokens: dict = {}


# ─── Команды ─────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Приветствие и сброс состояния."""
    ctx.user_data["ai_mode"] = False
    await update.message.reply_text(
        "👋 *Привет!* Я помогаю делать презентации для Brain-Do.\n\n"
        "📎 Просто пришли мне *.docx файл* с вопросами и ответами — "
        "и я за несколько секунд сделаю готовую презентацию!\n\n"
        "Используй кнопки внизу или команду /help для справки.",
        parse_mode="Markdown",
        reply_markup=main_menu(),
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Справка по использованию бота."""
    await update.message.reply_text(
        "📖 *Как пользоваться:*\n\n"
        "1. Пришли .docx файл с вопросами и ответами\n"
        "2. Я найду все пары вопрос/ответ\n"
        "3. Выбери настройки (тема, порядок)\n"
        "4. Получи готовый .pptx файл!\n\n"
        "📋 *Команды:*\n"
        "/start — главное меню\n"
        "/help — эта справка\n\n"
        "💡 *Формат файла:*\n"
        "Пиши как удобно — я сам разберусь! Главное чтобы были вопросы и ответы.\n\n"
        "✏️ *Сложный вопрос:*\n"
        "Добавь \\* в начало — он будет выделен красным на слайде.\n\n"
        "🤖 *ИИ-ассистент:*\n"
        "Нажми кнопку «Поговорить с ИИ» — можно задавать вопросы по файлам или просто поболтать.\n\n"
        "📁 *Яндекс Диск:*\n"
        "Нажми кнопку «Яндекс Диск» — выбери файл прямо с диска, без загрузки вручную.",
        parse_mode="Markdown",
        reply_markup=main_menu(),
    )


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает статистику использования бота."""
    await _show_stats(update.message)


async def cmd_yadisk(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Открывает интерфейс Яндекс Диска."""
    user_id = update.effective_user.id
    token = yadisk_tokens.get(str(user_id))

    if token:
        await _show_yadisk_files(update.message, token)
    else:
        url = yadisk_api.auth_url(user_id)
        await update.message.reply_text(
            "📁 *Яндекс Диск*\n\n"
            "Для доступа к файлам нужно авторизоваться один раз.\n"
            "Нажми кнопку ниже — откроется страница Яндекса:",
            parse_mode="Markdown",
            reply_markup=yadisk_auth_keyboard(url),
        )


# ─── Текстовые сообщения ─────────────────────────────────────────────────────

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает текстовые сообщения и кнопки главного меню."""
    text = update.message.text

    menu_handlers = {
        "📁 Яндекс Диск": lambda: cmd_yadisk(update, ctx),
        "📊 Статистика": lambda: _show_stats(update.message),
        "❓ Помощь": lambda: cmd_help(update, ctx),
        "🤖 Поговорить с ИИ": lambda: _activate_ai_mode(update, ctx),
        "❌ Выйти из ИИ": lambda: _deactivate_ai_mode(update, ctx),
    }

    if text in menu_handlers:
        await menu_handlers[text]()
        return

    if ctx.user_data.get("ai_mode"):
        await _handle_ai_chat(update, ctx, text)
        return

    await update.message.reply_text(
        "Пришли мне .docx файл с вопросами и ответами 📎",
        reply_markup=main_menu(),
    )


# ─── Документы ───────────────────────────────────────────────────────────────

async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Принимает .docx файл и запускает процесс создания презентации."""
    doc = update.message.document

    if not doc.file_name.endswith(".docx"):
        await update.message.reply_text("⚠️ Пожалуйста, пришли файл в формате .docx")
        return

    ctx.user_data["ai_mode"] = False
    msg = await update.message.reply_text("⏳ Читаю файл...")

    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
        file = await ctx.bot.get_file(doc.file_id)
        await file.download_to_drive(tmp.name)
        tmp_path = tmp.name

    await msg.edit_text("🔍 Ищу вопросы и ответы...")
    questions = parse_questions(tmp_path)
    os.unlink(tmp_path)

    if not questions:
        await msg.edit_text(
            "❌ Не нашёл вопросы в файле.\n\n"
            "Убедись что в документе есть слова *Вопрос* и *Ответ*.",
            parse_mode="Markdown",
        )
        return

    _save_questions_to_context(ctx, questions)
    await msg.edit_text(
        f"✅ Нашёл *{len(questions)}* вопросов!\n\nВыбери тему оформления:",
        parse_mode="Markdown",
        reply_markup=theme_keyboard(),
    )


# ─── Inline кнопки ───────────────────────────────────────────────────────────

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает нажатия inline кнопок."""
    query = update.callback_query
    await query.answer()

    data = query.data
    settings = ctx.user_data.get("settings", {})
    questions = ctx.user_data.get("questions", [])

    # Выбор темы
    if data.startswith("theme_"):
        theme = data.removeprefix("theme_")
        settings["theme"] = theme
        ctx.user_data["settings"] = settings
        theme_labels = {"white": "⬜ Белая", "dark": "⬛ Тёмная"}
        await query.edit_message_text(
            f"✅ Тема: *{theme_labels.get(theme, theme)}*\n\nНастрой параметры:",
            parse_mode="Markdown",
            reply_markup=options_keyboard(settings),
        )

    # Переключение перемешивания
    elif data == "toggle_shuffle":
        settings["shuffle"] = not settings["shuffle"]
        ctx.user_data["settings"] = settings
        await query.edit_message_reply_markup(reply_markup=options_keyboard(settings))

    # Переключение нумерации
    elif data == "toggle_numbering":
        settings["numbering"] = not settings["numbering"]
        ctx.user_data["settings"] = settings
        await query.edit_message_reply_markup(reply_markup=options_keyboard(settings))

    # Запуск генерации
    elif data == "generate":
        await query.edit_message_text(
            "⏳ Читаю вопросы...\n"
            "🔍 Проверяю через ИИ...\n"
            "⚙️ Генерирую слайды..."
        )
        await _do_generate(query.message, ctx, questions, settings)

    # Показ списка вопросов
    elif data == "show_list":
        await _show_question_list(query, ctx)

    elif data == "hide_list":
        await query.edit_message_text("Окей! 👍", reply_markup=None)

    # Яндекс Диск
    elif data == "yadisk_check":
        await _yadisk_check_auth(query)

    elif data == "yadisk_refresh":
        await _yadisk_refresh(query)

    elif data.startswith("yadisk_file:"):
        file_path = data.removeprefix("yadisk_file:")
        await _yadisk_open_file(query, ctx, file_path)


# ─── Вспомогательные функции ─────────────────────────────────────────────────

async def _show_stats(message) -> None:
    await message.reply_text(
        f"📊 *Статистика Brain-Do бота:*\n\n"
        f"🎯 Презентаций создано: *{stats['total_presentations']}*\n"
        f"❓ Вопросов обработано: *{stats['total_questions']}*\n\n"
        f"_powered by Nikita to папа ❤️_",
        parse_mode="Markdown",
        reply_markup=main_menu(),
    )


async def _activate_ai_mode(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ctx.user_data["ai_mode"] = True
    ctx.user_data["ai_history"] = []
    await update.message.reply_text(
        "🤖 *ИИ-ассистент активирован!*\n\n"
        "Задавай любые вопросы — по Brain-Do, по файлам, или просто поболтаем.\n\n"
        "Чтобы выйти из режима ИИ — напиши /start",
        parse_mode="Markdown",
        reply_markup=exit_ai_menu(),
    )


async def _deactivate_ai_mode(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ctx.user_data["ai_mode"] = False
    await cmd_start(update, ctx)


async def _handle_ai_chat(update: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Отправляет сообщение в Groq и возвращает ответ."""
    groq_key = os.environ.get("GROQ_API_KEY", "")
    if not groq_key:
        await update.message.reply_text("⚠️ ИИ недоступен — не настроен API ключ.")
        return

    history: list = ctx.user_data.get("ai_history", [])
    history.append({"role": "user", "content": text})
    history = history[-AI_MAX_HISTORY:]

    await update.message.reply_text("🤔 Думаю...")

    try:
        response = requests.post(
            GROQ_BASE_URL,
            headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "system", "content": AI_SYSTEM_PROMPT}] + history,
                "temperature": AI_TEMPERATURE,
                "max_tokens": AI_MAX_TOKENS,
            },
            timeout=30,
        )
        reply = response.json()["choices"][0]["message"]["content"]
        history.append({"role": "assistant", "content": reply})
        ctx.user_data["ai_history"] = history
        await update.message.reply_text(reply, reply_markup=exit_ai_menu())

    except Exception as e:
        logger.error("Ошибка ИИ чата: %s", e)
        await update.message.reply_text(
            "😕 Что-то пошло не так, попробуй ещё раз.",
            reply_markup=main_menu(),
        )


async def _do_generate(message, ctx, questions: list, settings: dict) -> None:
    """Генерирует презентацию и отправляет пользователю."""
    if settings.get("shuffle"):
        questions = random.sample(questions, len(questions))

    with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as tmp:
        out_path = tmp.name

    try:
        generate_presentation(questions, out_path, settings)

        stats["total_presentations"] += 1
        stats["total_questions"] += len(questions)
        save_stats(stats)

        theme_labels = {"white": "Белая", "dark": "Тёмная"}
        theme = theme_labels.get(settings["theme"], settings["theme"])
        shuffle_note = " 🔀 перемешано" if settings["shuffle"] else ""

        caption = (
            f"✅ *Готово!*\n\n"
            f"📊 Вопросов: *{len(questions)}*\n"
            f"🎨 Тема: {theme}{shuffle_note}\n\n"
            f"🤖 Все вопросы проверены ИИ\n\n"
            f"_powered by Nikita to папа ❤️_"
        )

        with open(out_path, "rb") as f:
            await message.reply_document(
                document=f,
                filename="presentation.pptx",
                caption=caption,
                parse_mode="Markdown",
            )

        ctx.user_data["last_questions"] = questions
        await message.reply_text(
            "Хочешь посмотреть список найденных вопросов?",
            reply_markup=question_list_keyboard(),
        )

    except Exception as e:
        logger.error("Ошибка генерации: %s", e)
        await message.reply_text(
            "😕 Что-то пошло не так при генерации. Попробуй отправить файл ещё раз.",
            reply_markup=main_menu(),
        )
    finally:
        try:
            os.unlink(out_path)
        except Exception:
            pass


async def _show_question_list(query, ctx) -> None:
    """Показывает список вопросов с ответами."""
    questions = ctx.user_data.get("last_questions", [])
    if not questions:
        await query.edit_message_text("Список недоступен.", reply_markup=None)
        return

    lines = [
        f"*{q['number']}.* {q['question'][:QUESTION_PREVIEW_MAX_LEN]}"
        f"{'...' if len(q['question']) > QUESTION_PREVIEW_MAX_LEN else ''}\n"
        f"↳ _{q['answer']}_"
        for q in questions
    ]
    text = "\n\n".join(lines)

    if len(text) > QUESTION_LIST_MAX_CHARS:
        text = text[:QUESTION_LIST_MAX_CHARS] + "\n\n_...и ещё вопросы_"

    await query.edit_message_text(
        f"📋 *Список вопросов ({len(questions)} шт):*\n\n{text}",
        parse_mode="Markdown",
        reply_markup=None,
    )


def _save_questions_to_context(ctx, questions: list) -> None:
    """Сохраняет вопросы и сбрасывает настройки в контекст пользователя."""
    ctx.user_data["questions"] = questions
    ctx.user_data["settings"] = DEFAULT_SETTINGS.copy()


# ─── Яндекс Диск обработчики ─────────────────────────────────────────────────

async def _show_yadisk_files(message, token: str) -> None:
    """Показывает список .docx файлов с Яндекс Диска."""
    msg = await message.reply_text("📂 Загружаю список файлов...")
    files = yadisk_api.list_docx_files(token)

    if not files:
        await msg.edit_text(
            "📂 Не нашёл .docx файлов на диске.\n"
            "Убедись что файлы есть на Яндекс Диске.",
            reply_markup=yadisk_files_keyboard([]),
        )
        return

    await msg.edit_text(
        f"📁 *Файлы на Яндекс Диске* ({len(files)} шт):\n\nВыбери файл для генерации презентации:",
        parse_mode="Markdown",
        reply_markup=yadisk_files_keyboard(files),
    )


async def _yadisk_check_auth(query) -> None:
    """Проверяет авторизацию Яндекс Диска."""
    user_id = str(query.from_user.id)
    token = yadisk_tokens.get(user_id)

    if token:
        await query.edit_message_text("✅ Авторизация подтверждена!")
        await _show_yadisk_files(query.message, token)
    else:
        await query.answer("Авторизация не найдена. Попробуй войти ещё раз.", show_alert=True)


async def _yadisk_refresh(query) -> None:
    """Обновляет список файлов Яндекс Диска."""
    user_id = str(query.from_user.id)
    token = yadisk_tokens.get(user_id)

    if token:
        await _show_yadisk_files(query.message, token)
    else:
        await query.answer("Нужна авторизация!", show_alert=True)


async def _yadisk_open_file(query, ctx, file_path: str) -> None:
    """Скачивает файл с Яндекс Диска и запускает создание презентации."""
    user_id = str(query.from_user.id)
    token = yadisk_tokens.get(user_id)

    if not token:
        await query.answer("Нужна авторизация!", show_alert=True)
        return

    file_name = file_path.split("/")[-1]
    await query.edit_message_text(f"⏳ Скачиваю *{file_name}*...", parse_mode="Markdown")

    content = yadisk_api.download_file(token, file_path)
    if not content:
        await query.message.reply_text("❌ Не удалось скачать файл. Попробуй ещё раз.")
        return

    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    await query.edit_message_text("🔍 Ищу вопросы и ответы...")
    questions = parse_questions(tmp_path)
    os.unlink(tmp_path)

    if not questions:
        await query.message.reply_text(
            "❌ Не нашёл вопросы в файле.\n\nПиши как удобно — главное чтобы были вопросы и ответы."
        )
        return

    _save_questions_to_context(ctx, questions)
    await query.edit_message_text(
        f"✅ Нашёл *{len(questions)}* вопросов из файла *{file_name}*!\n\nВыбери тему оформления:",
        parse_mode="Markdown",
        reply_markup=theme_keyboard(),
    )
