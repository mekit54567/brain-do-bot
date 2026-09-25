"""
Обработчики команд и сообщений Telegram бота.

Всё тяжёлое (парсинг, генерация, LibreOffice) уходит в поток через
asyncio.to_thread, сеть — через httpx.AsyncClient, поэтому бот не
замирает для остальных, пока кто-то делает презентацию.
"""

import asyncio
import html
import logging
import random
import re
import tempfile
from pathlib import Path

import httpx
from telegram import InputFile, Message, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

import keyboards as kb
import office
import yadisk as yd
from config import (
    AI_CONTEXT_MAX_CHARS,
    AI_MAX_HISTORY,
    AI_MAX_TOKENS,
    AI_SYSTEM_PROMPT,
    AI_TEMPERATURE,
    DEFAULT_SETTINGS,
    GROQ_API_KEY,
    GROQ_BASE_URL,
    GROQ_MODEL,
    MAX_FILE_SIZE,
    QUESTION_LIST_PAGE_CHARS,
    QUESTION_PREVIEW_MAX_LEN,
    SIGNATURE,
    SUPPORTED_EXTENSIONS,
    YADISK_PAGE_SIZE,
)
from generator import THEMES, generate_presentation
from parser import ParseResult, looks_like_questions, parse_file, parse_text
from storage import Stats, TokenStore

logger = logging.getLogger(__name__)

HTML = ParseMode.HTML

# Глобальное состояние (инициализируется в bot.py)
stats: Stats
tokens: TokenStore

# Последний загруженный файл каждого пользователя. Держим в памяти, а не в
# user_data: там картинки раздували бы файл персистентности.
DOCS: dict[int, dict] = {}
# Кто сейчас ждёт презентацию (не в user_data — иначе флаг переживёт перезапуск)
BUSY: set[int] = set()


def esc(text) -> str:
    return html.escape(str(text), quote=False)


# ─── Команды ─────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Приветствие и сброс режимов."""
    ctx.user_data["ai_mode"] = False
    ctx.user_data.pop("awaiting", None)
    name = esc(update.effective_user.first_name or "")
    await update.effective_message.reply_text(
        f"👋 <b>Привет{', ' + name if name else ''}!</b> Я делаю презентации для Brain-Do.\n\n"
        "📎 Пришли <b>Word-файл</b> с вопросами и ответами — или просто вставь их текстом "
        "в сообщение. Через пару секунд получишь готовый .pptx.\n\n"
        "⚙️ Тему и нумерацию можно выбрать один раз в «Настройках» — я запомню.\n"
        "❓ Подробности — в «Помощи».",
        parse_mode=HTML,
        reply_markup=kb.main_menu(),
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Справка по использованию бота."""
    formats = ".docx, .doc, .rtf, .odt, .txt" if office.libreoffice_available() else ".docx, .txt"
    await update.effective_message.reply_text(
        "📖 <b>Как пользоваться</b>\n\n"
        f"1. Пришли файл ({formats}), выбери его на Яндекс Диске или вставь вопросы текстом.\n"
        "2. Проверь, что я нашёл, и при желании поменяй настройки.\n"
        "3. Жми «🚀 Создать презентацию» — готово!\n\n"
        "💡 <b>Формат</b> — пиши как удобно:\n"
        "<code>Вопрос 1: Кто написал «Войну и мир»?\n"
        "Ответ: Лев Толстой\n"
        "Зачёт: Толстой\n"
        "Комментарий: Написал в 1869 году.</code>\n"
        "Номер можно писать как «5.», «5)» или списком Word. Ответ можно "
        "перенести на следующую строку. Таблицы тоже понимаю.\n\n"
        "★ <b>Сложный вопрос</b> — поставь <code>*</code> в начале текста вопроса.\n"
        "🖼 <b>Картинки</b> из вопросов и комментариев переносятся на слайды.\n"
        "📑 <b>Туры</b> — если в файле есть «Тур 1», «Тур 2», можно сделать презентацию на один тур.\n\n"
        "⚡ <b>Быстрый режим</b> в «Настройках» — презентация делается сразу после загрузки.\n"
        "🤖 <b>ИИ-помощник</b> видит последний загруженный файл: можно попросить проверить ответы.\n\n"
        "📋 <b>Команды:</b> /start · /settings · /yadisk · /stats · /help",
        parse_mode=HTML,
        reply_markup=kb.main_menu(),
    )


async def cmd_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Настройки по умолчанию."""
    await update.effective_message.reply_text(
        _settings_text(),
        parse_mode=HTML,
        reply_markup=kb.default_settings(_settings(ctx), office.libreoffice_available()),
    )


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Статистика: общая и личная."""
    mine = stats.user(update.effective_user.id)
    await update.effective_message.reply_text(
        "📊 <b>Статистика Brain-Do бота</b>\n\n"
        f"🎯 Презентаций создано: <b>{stats.total_presentations}</b>\n"
        f"❓ Вопросов обработано: <b>{stats.total_questions}</b>\n"
        f"👥 Пользователей: <b>{len(stats.users)}</b>\n\n"
        f"🙋 Твои презентации: <b>{mine['presentations']}</b> ({mine['questions']} вопросов)\n\n"
        f"<i>{SIGNATURE}</i>",
        parse_mode=HTML,
        reply_markup=kb.main_menu(),
    )


async def cmd_yadisk(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Яндекс Диск. /yadisk текст — сразу поиск по названию."""
    ctx.user_data["yd_query"] = " ".join(ctx.args or []).strip()
    await _yd_open(update.effective_message, ctx, update.effective_user.id, refresh=True)


# ─── Текстовые сообщения ─────────────────────────────────────────────────────

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопки меню, поиск по диску, ИИ-чат или вопросы, вставленные текстом."""
    message = update.effective_message
    text = message.text.strip()

    menu = {
        kb.BTN_YADISK: cmd_yadisk,
        kb.BTN_SETTINGS: cmd_settings,
        kb.BTN_STATS: cmd_stats,
        kb.BTN_HELP: cmd_help,
        kb.BTN_AI: _activate_ai_mode,
        kb.BTN_EXIT_AI: _deactivate_ai_mode,
    }
    if text in menu:
        ctx.user_data.pop("awaiting", None)
        if text == kb.BTN_YADISK:
            ctx.user_data["yd_query"] = ""
            await _yd_open(message, ctx, update.effective_user.id, refresh=True)
        else:
            await menu[text](update, ctx)
        return

    if ctx.user_data.pop("awaiting", None) == "yd_search":
        ctx.user_data["yd_query"] = text
        ctx.user_data["yd_page"] = 0
        await _yd_open(message, ctx, update.effective_user.id, refresh=False)
        return

    if ctx.user_data.get("ai_mode"):
        await _handle_ai_chat(update, ctx, text)
        return

    if looks_like_questions(text):
        status = await message.reply_text("🔍 Ищу вопросы и ответы…")
        result = await asyncio.to_thread(parse_text, text)
        await _open_doc(status, ctx, update.effective_user.id, result, "Вопросы из сообщения")
        return

    await message.reply_text(
        "📎 Пришли мне файл с вопросами и ответами или вставь их текстом, например:\n\n"
        "<code>1. Кто написал «Войну и мир»?\nОтвет: Лев Толстой</code>",
        parse_mode=HTML,
        reply_markup=kb.main_menu(),
    )


# ─── Документы ───────────────────────────────────────────────────────────────

async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Принимает файл и открывает карточку (или сразу делает презентацию)."""
    message = update.effective_message
    doc = message.document
    file_name = doc.file_name or "file.docx"
    ext = Path(file_name).suffix.lower()

    if ext not in SUPPORTED_EXTENSIONS:
        await message.reply_text(
            "⚠️ Я понимаю файлы Word (.docx, .doc), .rtf, .odt и .txt.\n"
            "Если у тебя PDF — открой его в Word и сохрани как .docx."
        )
        return
    if ext in (".doc", ".rtf", ".odt") and not office.libreoffice_available():
        await message.reply_text("⚠️ Этот формат я пока не умею открывать. Сохрани файл как .docx и пришли снова.")
        return
    if doc.file_size and doc.file_size > MAX_FILE_SIZE:
        await message.reply_text("⚠️ Файл больше 20 МБ — Telegram не даёт ботам скачивать такие. Сожми картинки или раздели файл.")
        return

    ctx.user_data["ai_mode"] = False
    status = await message.reply_text("⏳ Читаю файл…", reply_markup=None)

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"input{ext}"
        tg_file = await ctx.bot.get_file(doc.file_id)
        await tg_file.download_to_drive(path)
        await _edit(status, "🔍 Ищу вопросы и ответы…")
        result = await _parse_safely(status, str(path))

    if result is not None:
        await _open_doc(status, ctx, update.effective_user.id, result, Path(file_name).stem)


async def _parse_safely(status: Message, path: str) -> ParseResult | None:
    try:
        return await asyncio.to_thread(parse_file, path)
    except Exception as e:
        logger.exception("Ошибка чтения файла: %s", e)
        await _edit(status, "😕 Не получилось прочитать файл. Он точно открывается в Word? "
                            "Попробуй пересохранить его как .docx.")
        return None


async def _open_doc(status: Message, ctx, user_id: int, result: ParseResult, name: str) -> None:
    """Запоминает разобранный файл и показывает карточку."""
    if not result.questions:
        tips = (
            "❌ <b>Не нашёл вопросов с ответами.</b>\n\n"
            "Проверь, что у каждого вопроса есть строка «Ответ: …». Например:\n"
            "<code>Вопрос 1: Текст вопроса\nОтвет: Текст ответа</code>"
        )
        if result.skipped:
            tips += "\n\nНашёл, но без ответа:\n" + "\n".join(f"• {esc(s)}" for s in result.skipped[:5])
        await _edit(status, tips)
        return

    DOCS[user_id] = {
        "name": name,
        "questions": result.questions,
        "skipped": result.skipped,
        "tours": result.tours,
        "tour": None,
        "ai_checked": result.ai_checked,
        "ai_added": result.ai_added,
        "pictures": result.pictures,
    }

    if _settings(ctx)["instant"]:
        await _generate(status, ctx, user_id)
    else:
        await _show_card(status, ctx, user_id, edit=True)


# ─── Карточка файла ──────────────────────────────────────────────────────────

def _selected(doc: dict) -> list[dict]:
    if doc["tour"] is None:
        return doc["questions"]
    return [q for q in doc["questions"] if q["tour"] == doc["tour"]]


def _card_text(doc: dict) -> str:
    questions = doc["questions"]
    hard = sum(1 for q in questions if q["hard"])
    facts = [f"✅ Нашёл <b>{len(questions)}</b> {_plural(len(questions), 'вопрос', 'вопроса', 'вопросов')}"]
    if hard:
        facts.append(f"★ {hard} {_plural(hard, 'сложный', 'сложных', 'сложных')}")
    if doc["pictures"]:
        facts.append(f"🖼 {doc['pictures']} {_plural(doc['pictures'], 'картинка', 'картинки', 'картинок')}")
    if len(doc["tours"]) > 1:
        facts.append(f"📑 {len(doc['tours'])} {_plural(len(doc['tours']), 'тур', 'тура', 'туров')}")

    lines = [f"📄 <b>{esc(doc['name'])}</b>", " · ".join(facts)]
    if doc["ai_added"]:
        lines.append(f"🤖 ИИ нашёл ещё {doc['ai_added']}, которые я сначала пропустил")
    if doc["skipped"]:
        shown = "; ".join(esc(s) for s in doc["skipped"][:4])
        more = f" и ещё {len(doc['skipped']) - 4}" if len(doc["skipped"]) > 4 else ""
        lines.append(f"⚠️ Не взял: {shown}{more}")
    lines.append("\nНастрой, если нужно, и жми «Создать» 👇")
    return "\n".join(lines)


def _tour_label(doc: dict) -> str | None:
    if len(doc["tours"]) < 2:
        return None
    if doc["tour"] is None:
        return "Все туры"
    count = len(_selected(doc))
    return f"{doc['tour']} ({count} {_plural(count, 'вопрос', 'вопроса', 'вопросов')})"


async def _show_card(message: Message, ctx, user_id: int, edit: bool) -> None:
    doc = DOCS[user_id]
    markup = kb.file_card(_settings(ctx), _tour_label(doc), office.libreoffice_available())
    if edit and message.text is not None:
        await _edit(message, _card_text(doc), markup)
    else:
        await message.reply_text(_card_text(doc), parse_mode=HTML, reply_markup=markup)


# ─── Генерация ───────────────────────────────────────────────────────────────

async def _generate(status: Message, ctx, user_id: int) -> None:
    """Делает презентацию (и PDF), отправляет, убирает статус-сообщение."""
    doc = DOCS[user_id]
    settings = dict(_settings(ctx))
    questions = [dict(q) for q in _selected(doc)]
    if settings["shuffle"]:
        random.shuffle(questions)
    for i, q in enumerate(questions, 1):
        q["number"] = i  # порядковые номера — в итоговом порядке

    BUSY.add(user_id)
    chat_id = status.chat_id
    try:
        await _edit(status, f"⚙️ Собираю презентацию: {len(questions)} вопросов…")
        await ctx.bot.send_chat_action(chat_id, ChatAction.UPLOAD_DOCUMENT)
        pptx = await asyncio.to_thread(generate_presentation, questions, settings)

        pdf, pdf_failed = None, False
        if settings["pdf"] and office.libreoffice_available():
            await _edit(status, "📄 Делаю PDF-версию…")
            try:
                pdf = await asyncio.to_thread(office.pptx_to_pdf, pptx)
            except Exception as e:
                logger.warning("PDF не получился: %s", e)
                pdf_failed = True

        filename = _safe_filename(doc["name"] + (f" — {doc['tour']}" if doc["tour"] else ""))
        caption = _result_caption(doc, questions, settings, pdf_failed)
        await ctx.bot.send_document(
            chat_id,
            InputFile(pptx, filename=f"{filename}.pptx"),
            caption=caption,
            parse_mode=HTML,
            reply_markup=kb.after_generate(),
        )
        if pdf:
            await ctx.bot.send_document(chat_id, InputFile(pdf, filename=f"{filename}.pdf"))

        stats.record(user_id, len(questions))
        try:
            await status.delete()
        except BadRequest:
            pass
    except Exception as e:
        logger.exception("Ошибка генерации: %s", e)
        await _edit(status, "😕 Что-то пошло не так при генерации. Попробуй ещё раз или пришли файл заново.")
    finally:
        BUSY.discard(user_id)


def _result_caption(doc: dict, questions: list, settings: dict, pdf_failed: bool) -> str:
    details = [THEMES[settings["theme"]]["label"], f"🔢 {kb.NUMBERING_LABELS[settings['numbering']]}"]
    if settings["shuffle"]:
        details.append("🔀 перемешано")
    if doc["tour"]:
        details.append(f"📑 {esc(doc['tour'])}")
    lines = [
        f"✅ <b>Готово!</b> {len(questions)} {_plural(len(questions), 'вопрос', 'вопроса', 'вопросов')} · "
        f"{len(questions) * 2} слайдов",
        " · ".join(details),
    ]
    if doc["ai_checked"]:
        lines.append("🤖 ИИ проверил, что ничего не пропущено")
    if pdf_failed:
        lines.append("⚠️ PDF сделать не получилось — только .pptx")
    lines.append(f"\n<i>{SIGNATURE}</i>")
    return "\n".join(lines)


def _safe_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\n\r\t]+', " ", name).strip(" .")
    return (name[:80] or "presentation").strip()


# ─── Inline кнопки ───────────────────────────────────────────────────────────

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Маршрутизатор нажатий inline-кнопок. Каждая ветка отвечает на query ровно один раз."""
    query = update.callback_query
    data = query.data or ""
    user_id = query.from_user.id
    message = query.message

    if data == "noop":
        await query.answer()
        return

    # Настройки по умолчанию (/settings)
    if data.startswith("d:"):
        settings = _settings(ctx)
        _toggle(settings, data[2:])
        await query.answer()
        await _edit_markup(message, kb.default_settings(settings, office.libreoffice_available()))
        return

    if data.startswith("yd:"):
        await _yd_callback(query, ctx, data[3:])
        return

    # Всё ниже требует загруженного файла
    known = data.startswith(("s:", "list:")) or data in ("gen", "card")
    if not known:
        await query.answer("Эта кнопка устарела — пришли файл заново 🙂", show_alert=True)
        return
    if user_id not in DOCS:
        await query.answer("Я не помню этот файл (бот перезапускался). Пришли его ещё раз 🙂", show_alert=True)
        return
    doc = DOCS[user_id]

    if data.startswith("s:"):
        key = data[2:]
        if key == "tour":
            options = [None] + doc["tours"]
            doc["tour"] = options[(options.index(doc["tour"]) + 1) % len(options)]
        else:
            _toggle(_settings(ctx), key)
        await query.answer()
        await _show_card(message, ctx, user_id, edit=True)

    elif data == "gen":
        if user_id in BUSY:
            await query.answer("Уже делаю, секунду… ⏳")
            return
        BUSY.add(user_id)  # до первого await — иначе двойное нажатие проскочит
        try:
            await query.answer("Поехали! 🚀")
        finally:
            await _generate(message, ctx, user_id)

    elif data == "card":
        await query.answer()
        await _show_card(message, ctx, user_id, edit=True)

    elif data.startswith("list:"):
        await query.answer()
        await _show_question_list(message, doc, int(data[5:]))


def _toggle(settings: dict, key: str) -> None:
    if key == "theme":
        order = kb.THEME_ORDER
        settings["theme"] = order[(order.index(settings["theme"]) + 1) % len(order)]
    elif key == "num":
        order = kb.NUMBERING_ORDER
        settings["numbering"] = order[(order.index(settings["numbering"]) + 1) % len(order)]
    elif key in ("shuffle", "pdf", "instant"):
        settings[key] = not settings[key]


def _settings(ctx) -> dict:
    """Настройки пользователя (запоминаются между файлами и перезапусками)."""
    settings = {**DEFAULT_SETTINGS, **ctx.user_data.get("settings", {})}
    if settings["theme"] not in THEMES:
        settings["theme"] = DEFAULT_SETTINGS["theme"]
    if settings["numbering"] not in kb.NUMBERING_LABELS:
        settings["numbering"] = DEFAULT_SETTINGS["numbering"]
    ctx.user_data["settings"] = settings
    return settings


def _settings_text() -> str:
    return (
        "⚙️ <b>Настройки по умолчанию</b>\n\n"
        "Они применяются к каждому новому файлу — выбрал один раз и забыл. "
        "В карточке файла их можно поменять перед созданием.\n\n"
        "⚡ <b>Сразу делать презентацию</b> — не показывать карточку, "
        "а присылать готовый файл сразу после загрузки."
    )


# ─── Список вопросов ─────────────────────────────────────────────────────────

def _question_pages(questions: list[dict]) -> list[str]:
    pages, current = [], ""
    for q in questions:
        text = q["question"].replace("\n", " ") or "🖼 (картинка)"
        preview = text[:QUESTION_PREVIEW_MAX_LEN] + ("…" if len(text) > QUESTION_PREVIEW_MAX_LEN else "")
        number = q.get("orig_number") or q["number"]
        entry = f"<b>{number}.</b> {esc(preview)}{' ★' if q['hard'] else ''}\n↳ <i>{esc(q['answer'])}</i>\n\n"
        if current and len(current) + len(entry) > QUESTION_LIST_PAGE_CHARS:
            pages.append(current)
            current = ""
        current += entry
    pages.append(current)
    return pages


async def _show_question_list(message: Message, doc: dict, page: int) -> None:
    questions = _selected(doc)
    pages = _question_pages(questions)
    page = max(0, min(page, len(pages) - 1))
    text = f"📋 <b>{esc(doc['name'])}</b> — {len(questions)} шт.\n\n{pages[page]}".rstrip()
    markup = kb.question_list(page, len(pages))
    if message.text is not None:
        await _edit(message, text, markup)
    else:
        await message.reply_text(text, parse_mode=HTML, reply_markup=markup)


# ─── ИИ-чат ──────────────────────────────────────────────────────────────────

async def _activate_ai_mode(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ctx.user_data["ai_mode"] = True
    ctx.user_data["ai_history"] = []
    doc = DOCS.get(update.effective_user.id)
    extra = (f"\n\n📄 Я вижу твой последний файл «{esc(doc['name'])}» — могу проверить ответы "
             "или придумать похожие вопросы." if doc else "")
    await update.effective_message.reply_text(
        "🤖 <b>ИИ-ассистент на связи!</b>\n\n"
        "Задавай любые вопросы — по Brain-Do, по файлам, или просто поболтаем." + extra,
        parse_mode=HTML,
        reply_markup=kb.exit_ai_menu(),
    )


async def _deactivate_ai_mode(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ctx.user_data["ai_mode"] = False
    ctx.user_data["ai_history"] = []
    await update.effective_message.reply_text("👌 Вышли из режима ИИ.", reply_markup=kb.main_menu())


def _ai_system_prompt(doc: dict | None) -> str:
    if not doc:
        return AI_SYSTEM_PROMPT
    lines = []
    for q in doc["questions"]:
        line = f"{q.get('orig_number') or q['number']}. {q['question']} — Ответ: {q['answer']}"
        if q.get("comment"):
            line += f" (Комментарий: {q['comment']})"
        lines.append(line.replace("\n", " "))
    context = "\n".join(lines)[:AI_CONTEXT_MAX_CHARS]
    return f"{AI_SYSTEM_PROMPT}\n\nПользователь загрузил пакет «{doc['name']}»:\n{context}"


async def _handle_ai_chat(update: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Отправляет сообщение в Groq и возвращает ответ."""
    message = update.effective_message
    if not GROQ_API_KEY:
        await message.reply_text("⚠️ ИИ недоступен — не настроен API ключ.", reply_markup=kb.main_menu())
        ctx.user_data["ai_mode"] = False
        return

    history: list = ctx.user_data.get("ai_history", [])
    history.append({"role": "user", "content": text})
    history = history[-AI_MAX_HISTORY:]
    await ctx.bot.send_chat_action(message.chat_id, ChatAction.TYPING)

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(
                GROQ_BASE_URL,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                json={
                    "model": GROQ_MODEL,
                    "messages": [{"role": "system", "content": _ai_system_prompt(DOCS.get(update.effective_user.id))}]
                                + history,
                    "temperature": AI_TEMPERATURE,
                    "max_tokens": AI_MAX_TOKENS,
                },
            )
        if response.status_code == 429:
            await message.reply_text("⏳ ИИ сейчас перегружен. Попробуй через минутку.", reply_markup=kb.exit_ai_menu())
            return
        response.raise_for_status()
        reply = response.json()["choices"][0]["message"]["content"].strip() or "🤷"
    except Exception as e:
        logger.error("Ошибка ИИ чата: %s", e)
        await message.reply_text("😕 ИИ не ответил, попробуй ещё раз.", reply_markup=kb.exit_ai_menu())
        return

    history.append({"role": "assistant", "content": reply})
    ctx.user_data["ai_history"] = history[-AI_MAX_HISTORY:]
    for i in range(0, len(reply), 4000):  # лимит Telegram — 4096 символов
        await message.reply_text(reply[i:i + 4000], reply_markup=kb.exit_ai_menu())


# ─── Яндекс Диск ─────────────────────────────────────────────────────────────

async def _yd_open(message: Message, ctx, user_id: int, refresh: bool, edit: bool = False) -> None:
    """Показывает список файлов (или предлагает авторизоваться)."""
    if not yd.is_configured():
        await message.reply_text("📁 Яндекс Диск не настроен у этого бота.")
        return

    token = tokens.get(user_id)
    if not token:
        await message.reply_text(
            "📁 <b>Яндекс Диск</b>\n\n"
            "Чтобы выбирать файлы прямо с диска, нужно один раз войти через Яндекс. "
            "После входа я сам пришлю список файлов.",
            parse_mode=HTML,
            reply_markup=kb.yadisk_auth(yd.auth_url(user_id, message.chat_id)),
        )
        return

    await _yd_render(message, ctx.user_data, user_id, token, refresh, edit)


async def _yd_render(message: Message, user_data: dict, user_id: int, token: str,
                     refresh: bool, edit: bool) -> None:
    status = message
    if refresh or "yd_files" not in user_data:
        if edit:
            await _edit(message, "📂 Загружаю список файлов…")
        else:
            status = await message.reply_text("📂 Загружаю список файлов…")
            edit = True
        try:
            user_data["yd_files"] = await yd.list_files(token)
        except yd.AuthError:
            tokens.delete(user_id)
            await _edit(status, "🔑 Доступ к Яндекс Диску истёк. Нажми «📁 Яндекс Диск», чтобы войти заново.")
            return
        except Exception as e:
            logger.error("Ошибка списка файлов: %s", e)
            await _edit(status, "😕 Яндекс Диск не ответил. Попробуй ещё раз через минуту.")
            return

    query = user_data.get("yd_query", "")
    files = user_data["yd_files"]
    if query:
        files = [f for f in files if query.lower() in f["name"].lower()]
    user_data["yd_view"] = files  # индексы в кнопках указывают сюда
    page = user_data.get("yd_page", 0) if not refresh else 0
    page = max(0, min(page, (len(files) - 1) // YADISK_PAGE_SIZE if files else 0))
    user_data["yd_page"] = page

    if files:
        head = f"🔎 Поиск «{esc(query)}»: {len(files)}" if query else f"📁 <b>Яндекс Диск</b> — {len(files)} файлов"
        text = f"{head}\nСвежие сверху. Выбери файл:"
    elif query:
        text = f"🔎 По запросу «{esc(query)}» ничего не нашёл."
    else:
        text = "📂 Не нашёл на диске файлов Word (.docx, .doc) или .txt."
    markup = kb.yadisk_files(files, page, YADISK_PAGE_SIZE, searching=bool(query))

    if edit:
        await _edit(status, text, markup)
    else:
        await status.reply_text(text, parse_mode=HTML, reply_markup=markup)


async def _yd_callback(query, ctx, action: str) -> None:
    user_id = query.from_user.id
    message = query.message
    token = tokens.get(user_id)

    if action == "check":
        if not token:
            await query.answer("Вход ещё не завершён. Нажми «Войти через Яндекс» 🙂", show_alert=True)
            return
        await query.answer()
        await _yd_render(message, ctx.user_data, user_id, token, refresh=True, edit=True)
        return

    if not token:
        await query.answer("Нужно войти в Яндекс Диск заново.", show_alert=True)
        return

    if action == "refresh":
        await query.answer("Обновляю…")
        await _yd_render(message, ctx.user_data, user_id, token, refresh=True, edit=True)
    elif action.startswith("p:"):
        await query.answer()
        ctx.user_data["yd_page"] = int(action[2:])
        await _yd_render(message, ctx.user_data, user_id, token, refresh=False, edit=True)
    elif action == "search":
        ctx.user_data["awaiting"] = "yd_search"
        await query.answer()
        await message.reply_text("🔎 Напиши часть названия файла:")
    elif action == "all":
        ctx.user_data["yd_query"] = ""
        ctx.user_data["yd_page"] = 0
        await query.answer()
        await _yd_render(message, ctx.user_data, user_id, token, refresh=False, edit=True)
    elif action == "logout":
        tokens.delete(user_id)
        ctx.user_data.pop("yd_files", None)
        await query.answer()
        await _edit(message, "🚪 Яндекс Диск отключён. Подключить снова — кнопка «📁 Яндекс Диск».")
    elif action.startswith("f:"):
        files = ctx.user_data.get("yd_view", [])
        index = int(action[2:])
        if index >= len(files):
            await query.answer("Список устарел — обнови его 🔄", show_alert=True)
            return
        await query.answer()
        await _yd_open_file(message, ctx, user_id, token, files[index])
    else:
        await query.answer()


async def _yd_open_file(message: Message, ctx, user_id: int, token: str, file: dict) -> None:
    """Скачивает файл с Яндекс Диска и открывает карточку."""
    ctx.user_data["ai_mode"] = False
    await _edit(message, f"⏳ Скачиваю <b>{esc(file['name'])}</b>…")
    try:
        content = await yd.download_file(token, file["path"])
    except yd.AuthError:
        tokens.delete(user_id)
        await _edit(message, "🔑 Доступ к Яндекс Диску истёк. Нажми «📁 Яндекс Диск», чтобы войти заново.")
        return
    except Exception as e:
        logger.error("Ошибка скачивания %s: %s", file["path"], e)
        await _edit(message, "❌ Не удалось скачать файл. Попробуй ещё раз.")
        return

    ext = Path(file["name"]).suffix.lower()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"input{ext}"
        path.write_bytes(content)
        await _edit(message, "🔍 Ищу вопросы и ответы…")
        result = await _parse_safely(message, str(path))
    if result is not None:
        await _open_doc(message, ctx, user_id, result, Path(file["name"]).stem)


async def notify_yadisk_connected(app, user_id: int, chat_id: int) -> None:
    """Вызывается после успешного OAuth: сразу присылаем список файлов."""
    message = await app.bot.send_message(chat_id, "✅ Яндекс Диск подключён!")
    user_data = app.user_data.get(user_id)
    if user_data is None:
        user_data = {}
    user_data["yd_query"] = ""
    await _yd_render(message, user_data, user_id, tokens.get(user_id), refresh=True, edit=False)


# ─── Ошибки ──────────────────────────────────────────────────────────────────

async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Глобальный обработчик: логируем и вежливо сообщаем пользователю."""
    if isinstance(ctx.error, BadRequest) and any(
        s in str(ctx.error).lower() for s in ("not modified", "query is too old")
    ):
        return
    logger.error("Необработанная ошибка", exc_info=ctx.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("😕 Что-то пошло не так. Попробуй ещё раз.")
        except Exception:
            pass


# ─── Утилиты ─────────────────────────────────────────────────────────────────

async def _edit(message: Message, text: str, markup=None) -> None:
    """edit_text, который не падает на «message is not modified»."""
    try:
        await message.edit_text(text, parse_mode=HTML, reply_markup=markup)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


async def _edit_markup(message: Message, markup) -> None:
    try:
        await message.edit_reply_markup(reply_markup=markup)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Склонение: 1 вопрос, 2 вопроса, 5 вопросов."""
    n = abs(n) % 100
    if 11 <= n <= 19:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many
