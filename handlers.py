"""
Обработчики команд, сообщений и кнопок Telegram бота.

Всё тяжёлое (парсинг, генерация, предпросмотр, LibreOffice) уходит в поток
через asyncio.to_thread, сеть — через httpx.AsyncClient, поэтому бот не
замирает для остальных, пока кто-то делает презентацию.
"""

import asyncio
import logging
import re
import tempfile
from pathlib import Path

import httpx
from telegram import InputFile, InputMediaPhoto, Message, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import ContextTypes

import admin
import editor
import extras
import generator
import keyboards as kb
import office
import preview
import review
import session
import yadisk as yd
from config import (
    AI_CONTEXT_MAX_CHARS,
    AI_MAX_HISTORY,
    AI_MAX_TOKENS,
    AI_SYSTEM_PROMPT,
    AI_TEMPERATURE,
    GROQ_API_KEY,
    GROQ_BASE_URL,
    GROQ_MODEL,
    MAX_FILE_SIZE,
    SIGNATURE,
    SUPPORTED_EXTENSIONS,
    YADISK_PAGE_SIZE,
)
from parser import ParseResult, looks_like_questions, parse_file, parse_text
from session import HTML, NUMBERING_LABELS, delete, edit, edit_markup, esc, plural

logger = logging.getLogger(__name__)

# Порядок вопросов последней презентации — чтобы таблица и раздатка совпали
# с номерами на слайдах, даже если вопросы перемешаны. {user_id: (doc_id, вопросы)}
LAST_ORDER: dict[int, tuple[str, list[dict]]] = {}

SAMPLE_QUESTION = {
    "number": 1, "orig_number": 1, "hard": False, "tour": None, "q_pictures": [], "a_pictures": [],
    "question": "Кто написал «Войну и мир»? Так будет выглядеть вопрос в твоём шаблоне.",
    "answer": "Лев Толстой", "accept": None, "comment": "А так — комментарий.",
}


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
        "2. Проверь, что я нашёл: «👁 Предпросмотр» покажет слайды, «📋 Вопросы и правка» — список, "
        "где можно исправить любой вопрос.\n"
        "3. Жми «🚀 Создать презентацию» — готово!\n\n"
        "💡 <b>Формат</b> — пиши как удобно:\n"
        "<code>Вопрос 1: Кто написал «Войну и мир»?\n"
        "Ответ: Лев Толстой\n"
        "Зачёт: Толстой\n"
        "Комментарий: Написал в 1869 году.</code>\n"
        "Номер можно писать как «5.», «5)» или списком Word. Таблицы тоже понимаю.\n\n"
        "★ <b>Сложный вопрос</b> — поставь <code>*</code> в начале текста вопроса.\n"
        "🖼 <b>Картинки</b> из вопросов и комментариев переносятся на слайды.\n"
        "📑 <b>Туры</b> — если в файле есть «Тур 1», «Тур 2», можно сделать презентацию на один тур.\n"
        "📝 <b>Заметки ведущего</b> — ответ виден в режиме докладчика, а залу нет.\n\n"
        "🧰 <b>Ещё</b> в карточке файла: титульный слайд и разделители туров, таблица результатов "
        "для Excel, раздатка для печати, проверка на опечатки и повторы.\n"
        "🖼 <b>Свой шаблон</b> — пришли .pptx: 1-й слайд станет образцом вопроса, 2-й — ответа.\n"
        "🕘 <b>История</b> — последние 10 пакетов, пересобрать одной кнопкой.\n"
        "⚡ <b>Быстрый режим</b> в «Настройках» — презентация сразу после загрузки.\n\n"
        "📋 <b>Команды:</b> /start · /settings · /history · /yadisk · /stats · /id · /help",
        parse_mode=HTML,
        reply_markup=kb.main_menu(),
    )


async def cmd_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Настройки по умолчанию."""
    user_id = update.effective_user.id
    await update.effective_message.reply_text(
        _settings_text(user_id), parse_mode=HTML, reply_markup=_defaults_markup(ctx, user_id)
    )


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Статистика: общая и личная."""
    stats = session.stats
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


async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Последние пакеты — открыть и пересобрать без загрузки файла."""
    items = session.history.entries(update.effective_user.id)
    if not items:
        await update.effective_message.reply_text("🕘 История пока пуста — пришли первый файл с вопросами.")
        return
    await update.effective_message.reply_text(
        "🕘 <b>Последние пакеты</b> (название · вопросов · дата):", parse_mode=HTML,
        reply_markup=kb.history_list(items),
    )


async def cmd_yadisk(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Яндекс Диск. /yadisk текст — сразу поиск по названию."""
    ctx.user_data["yd_query"] = " ".join(ctx.args or []).strip()
    ctx.user_data["yd_page"] = 0
    await _yd_open(update.effective_message, ctx, update.effective_user.id, refresh=True)


# ─── Текстовые сообщения ─────────────────────────────────────────────────────

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопки меню, ответы на вопросы бота, ИИ-чат или вопросы, вставленные текстом."""
    message = update.effective_message
    user_id = update.effective_user.id
    text = message.text.strip()

    menu = {
        kb.BTN_SETTINGS: cmd_settings,
        kb.BTN_STATS: cmd_stats,
        kb.BTN_HELP: cmd_help,
        kb.BTN_HISTORY: cmd_history,
        kb.BTN_AI: _activate_ai_mode,
        kb.BTN_EXIT_AI: _deactivate_ai_mode,
    }
    if text in menu or text == kb.BTN_YADISK:
        ctx.user_data.pop("awaiting", None)
        if text == kb.BTN_YADISK:
            ctx.user_data["yd_query"] = ""
            ctx.user_data["yd_page"] = 0
            await _yd_open(message, ctx, user_id, refresh=True)
        else:
            await menu[text](update, ctx)
        return

    awaiting = ctx.user_data.pop("awaiting", None)
    if awaiting:
        await _handle_awaited(message, ctx, user_id, awaiting, text)
        return

    if ctx.user_data.get("ai_mode"):
        await _handle_ai_chat(update, ctx, text)
        return

    if looks_like_questions(text):
        status = await message.reply_text("🔍 Ищу вопросы и ответы…")
        result = await asyncio.to_thread(parse_text, text)
        await _open_doc(status, ctx, user_id, result, "Вопросы из сообщения")
        return

    await message.reply_text(
        "📎 Пришли мне файл с вопросами и ответами или вставь их текстом, например:\n\n"
        "<code>1. Кто написал «Войну и мир»?\nОтвет: Лев Толстой</code>",
        parse_mode=HTML,
        reply_markup=kb.main_menu(),
    )


async def _handle_awaited(message: Message, ctx, user_id: int, awaiting, text: str) -> None:
    """Ответ на вопрос бота: поиск по диску, правка поля, название турнира."""
    kind = awaiting.get("kind") if isinstance(awaiting, dict) else awaiting
    if kind == "yd_search":
        ctx.user_data["yd_query"] = text
        ctx.user_data["yd_page"] = 0
        await _yd_open(message, ctx, user_id, refresh=False)
        return

    doc = session.doc_for(user_id)
    if doc is None or (awaiting.get("doc") and doc.get("id") != awaiting["doc"]):
        await message.reply_text("Этот файл уже закрыт — открой его заново через 🕘 Историю.")
        return
    if kind == "edit":
        await editor.apply_edit(message, user_id, doc, awaiting["i"], awaiting["field"], text)
    elif kind == "title":
        doc["title"] = text[:120]
        session.history.save(user_id, doc)
        await message.reply_text(f"✅ Название: «{esc(doc['title'])}»", parse_mode=HTML)
        await _show_card(message, ctx, user_id, edit=False)


# ─── Документы ───────────────────────────────────────────────────────────────

async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Пакет вопросов → карточка; .pptx → предложение сделать его шаблоном."""
    message = update.effective_message
    doc = message.document
    file_name = doc.file_name or "file.docx"
    ext = Path(file_name).suffix.lower()

    if doc.file_size and doc.file_size > MAX_FILE_SIZE:
        await message.reply_text("⚠️ Файл больше 20 МБ — Telegram не даёт ботам скачивать такие. "
                                 "Сожми картинки или раздели файл.")
        return
    if ext == ".pptx":
        await _receive_template(message, ctx, update.effective_user.id)
        return
    if ext not in SUPPORTED_EXTENSIONS:
        await message.reply_text(
            "⚠️ Я понимаю файлы Word (.docx, .doc), .rtf, .odt и .txt, а .pptx — как шаблон оформления.\n"
            "Если у тебя PDF — открой его в Word и сохрани как .docx."
        )
        return
    if ext in (".doc", ".rtf", ".odt") and not office.libreoffice_available():
        await message.reply_text("⚠️ Этот формат я пока не умею открывать. Сохрани файл как .docx и пришли снова.")
        return

    ctx.user_data["ai_mode"] = False
    status = await message.reply_text("⏳ Читаю файл…")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"input{ext}"
        tg_file = await ctx.bot.get_file(doc.file_id)
        await tg_file.download_to_drive(path)
        await edit(status, "🔍 Ищу вопросы и ответы…")
        result = await _parse_safely(status, str(path))

    if result is not None:
        await _open_doc(status, ctx, update.effective_user.id, result, Path(file_name).stem)


async def _parse_safely(status: Message, path: str) -> ParseResult | None:
    try:
        return await asyncio.to_thread(parse_file, path)
    except Exception as e:
        logger.exception("Ошибка чтения файла: %s", e)
        await edit(status, "😕 Не получилось прочитать файл. Он точно открывается в Word? "
                           "Попробуй пересохранить его как .docx.")
        return None


async def _open_doc(status: Message, ctx, user_id: int, result: ParseResult, name: str) -> None:
    """Запоминает разобранный файл (и в историю) и показывает карточку."""
    if not result.questions:
        tips = (
            "❌ <b>Не нашёл вопросов с ответами.</b>\n\n"
            "Проверь, что у каждого вопроса есть строка «Ответ: …». Например:\n"
            "<code>Вопрос 1: Текст вопроса\nОтвет: Текст ответа</code>"
        )
        if result.skipped:
            tips += "\n\nНашёл, но без ответа:\n" + "\n".join(f"• {esc(s)}" for s in result.skipped[:5])
        await edit(status, tips)
        return

    session.remember(user_id, {
        "name": name,
        "title": None,
        "questions": result.questions,
        "skipped": result.skipped,
        "tours": result.tours,
        "tour": None,
        "ai_checked": result.ai_checked,
        "ai_added": result.ai_added,
    })
    if session.settings(ctx, user_id)["instant"]:
        await _generate(status, ctx, user_id)
    else:
        await _show_card(status, ctx, user_id, edit=True)


# ─── Свой шаблон ─────────────────────────────────────────────────────────────

async def _receive_template(message: Message, ctx, user_id: int) -> None:
    status = await message.reply_text("🖼 Смотрю шаблон…")
    tg_file = await ctx.bot.get_file(message.document.file_id)
    data = bytes(await tg_file.download_as_bytearray())
    try:
        template = await asyncio.to_thread(generator.validate_template, data)
    except generator.TemplateError as e:
        await edit(status, f"⚠️ {esc(e)}\n\nШаблон — это .pptx, где 1-й слайд — образец вопроса, "
                           "2-й — образец ответа, и на них есть большое текстовое поле.")
        return

    session.PENDING_TEMPLATES[user_id] = data
    caption = ("🖼 <b>Сделать это моим шаблоном оформления?</b>\n"
               "1-й слайд станет образцом вопроса, 2-й — ответа. Вот как будет выглядеть вопрос:")
    if preview.available():
        specs = generator.build_specs([SAMPLE_QUESTION], {"theme": "template"}, template)
        png = await asyncio.to_thread(preview.render, template, specs[0])
        await delete(status)
        await message.reply_photo(png, caption=caption, parse_mode=HTML, reply_markup=kb.template_confirm())
    else:
        await edit(status, caption.split("\n")[0] + "\n1-й слайд станет образцом вопроса, 2-й — ответа.",
                   kb.template_confirm())


async def _template_callback(query, ctx, action: str) -> None:
    user_id = query.from_user.id
    data = session.PENDING_TEMPLATES.pop(user_id, None)
    await edit_markup(query.message, None)
    if action == "ok" and data:
        session.save_template(user_id, data)
        session.settings(ctx, user_id)["theme"] = "template"
        await query.answer("Шаблон сохранён ✅")
        await query.message.reply_text(
            "✅ <b>Шаблон сохранён!</b> Теперь презентации будут в нём (тема «🖼 Как в шаблоне»).\n"
            "Вернуть стандартный — в ⚙️ Настройках.", parse_mode=HTML)
    elif action == "ok":
        await query.answer("Пришли шаблон ещё раз — я его забыл после перезапуска", show_alert=True)
    else:
        await query.answer("Ок, не сохраняю")


# ─── Карточка файла ──────────────────────────────────────────────────────────

def _card_text(doc: dict, user_id: int, current: dict) -> str:
    questions = doc["questions"]
    hard = sum(1 for q in questions if q.get("hard"))
    pictures = sum(len(q.get("q_pictures") or []) + len(q.get("a_pictures") or []) for q in questions)
    facts = [f"✅ Нашёл <b>{len(questions)}</b> {plural(len(questions), 'вопрос', 'вопроса', 'вопросов')}"]
    if hard:
        facts.append(f"★ {hard} {plural(hard, 'сложный', 'сложных', 'сложных')}")
    if pictures:
        facts.append(f"🖼 {pictures} {plural(pictures, 'картинка', 'картинки', 'картинок')}")
    if len(doc["tours"]) > 1:
        facts.append(f"📑 {len(doc['tours'])} {plural(len(doc['tours']), 'тур', 'тура', 'туров')}")

    lines = [f"📄 <b>{esc(doc['name'])}</b>", " · ".join(facts)]
    if doc.get("ai_added"):
        lines.append(f"🤖 ИИ нашёл ещё {doc['ai_added']}, которые я сначала пропустил")
    if doc.get("skipped"):
        shown = "; ".join(esc(s) for s in doc["skipped"][:4])
        more = f" и ещё {len(doc['skipped']) - 4}" if len(doc["skipped"]) > 4 else ""
        lines.append(f"⚠️ Не взял: {shown}{more}")
    if session.has_template(user_id):
        lines.append("🖼 Оформление: свой шаблон")
    if current["service"]:
        lines.append(f"🎬 Титул: «{esc(doc.get('title') or doc['name'])}»")
    lines.append("\nНастрой, если нужно, и жми «Создать» 👇")
    return "\n".join(lines)


def _tour_label(doc: dict) -> str | None:
    if len(doc["tours"]) < 2:
        return None
    if doc.get("tour") is None:
        return "Все туры"
    count = len(session.selected(doc))
    return f"{doc['tour']} ({count} {plural(count, 'вопрос', 'вопроса', 'вопросов')})"


async def _show_card(message: Message, ctx, user_id: int, edit: bool) -> None:
    doc = session.doc_for(user_id)
    current = session.settings(ctx, user_id)
    markup = kb.file_card(current, _tour_label(doc), office.libreoffice_available(), preview.available())
    text = _card_text(doc, user_id, current)
    if edit and message.text is not None:
        await session.edit(message, text, markup)
    else:
        await message.reply_text(text, parse_mode=HTML, reply_markup=markup)


async def _show_more(message: Message, ctx, user_id: int) -> None:
    doc = session.doc_for(user_id)
    current = session.settings(ctx, user_id)
    text = _card_text(doc, user_id, current).rsplit("\n\n", 1)[0] + (
        "\n\n🧰 <b>Дополнительно</b>\n"
        "📝 Заметки ведущего — ответ виден только в режиме докладчика.\n"
        "🎬 Титул, туры и финал — служебные слайды в начале, между турами и в конце."
    )
    markup = kb.more_menu(current, extras.has_handouts(session.selected(doc)), bool(GROQ_API_KEY))
    if message.text is not None:
        await session.edit(message, text, markup)
    else:
        await message.reply_text(text, parse_mode=HTML, reply_markup=markup)


# ─── Генерация ───────────────────────────────────────────────────────────────

def _file_stem(doc: dict) -> str:
    name = doc["name"] + (f" — {doc['tour']}" if doc.get("tour") else "")
    name = re.sub(r'[\\/:*?"<>|\n\r\t]+', " ", name).strip(" .")
    return (name[:80] or "presentation").strip()


async def _generate(status: Message, ctx, user_id: int) -> None:
    """Делает презентацию (и PDF), отправляет, убирает статус-сообщение."""
    doc = session.doc_for(user_id)
    current = dict(session.settings(ctx, user_id))
    questions = session.prepare_questions(doc, current)
    template = session.template_for(user_id)
    title = doc.get("title") or doc["name"]

    session.BUSY.add(user_id)
    chat_id = status.chat_id
    try:
        await edit(status, f"⚙️ Собираю презентацию: {len(questions)} вопросов…")
        await ctx.bot.send_chat_action(chat_id, ChatAction.UPLOAD_DOCUMENT)
        pptx = await asyncio.to_thread(generator.generate_presentation, questions, current, template, title)
        LAST_ORDER[user_id] = (doc.get("id"), questions)

        pdf, pdf_failed = None, False
        if current["pdf"] and office.libreoffice_available():
            await edit(status, "📄 Делаю PDF-версию…")
            try:
                pdf = await asyncio.to_thread(office.pptx_to_pdf, pptx)
            except Exception as e:
                logger.warning("PDF не получился: %s", e)
                pdf_failed = True

        stem = _file_stem(doc)
        await ctx.bot.send_document(
            chat_id,
            InputFile(pptx, filename=f"{stem}.pptx"),
            caption=_result_caption(doc, questions, current, pdf_failed),
            parse_mode=HTML,
            reply_markup=kb.after_generate(),
        )
        if pdf:
            await ctx.bot.send_document(chat_id, InputFile(pdf, filename=f"{stem}.pdf"))

        session.stats.record(user_id, len(questions))
        await delete(status)
    except Exception as e:
        logger.exception("Ошибка генерации: %s", e)
        await edit(status, "😕 Что-то пошло не так при генерации. Попробуй ещё раз или пришли файл заново.")
        await admin.report_error(ctx.bot, e, f"пользователь {user_id}, генерация презентации")
    finally:
        session.BUSY.discard(user_id)


def _result_caption(doc: dict, questions: list, current: dict, pdf_failed: bool) -> str:
    details = [generator.THEMES[current["theme"]]["label"], f"🔢 {NUMBERING_LABELS[current['numbering']]}"]
    if current["shuffle"]:
        details.append("🔀 перемешано")
    if doc.get("tour"):
        details.append(f"📑 {esc(doc['tour'])}")
    slides = len(questions) * 2
    if current["service"]:
        tours = {q.get("tour") for q in questions if q.get("tour")}
        slides += 2 + (0 if current["shuffle"] else len(tours))
    lines = [
        f"✅ <b>Готово!</b> {len(questions)} {plural(len(questions), 'вопрос', 'вопроса', 'вопросов')} · "
        f"{slides} {plural(slides, 'слайд', 'слайда', 'слайдов')}",
        " · ".join(details),
    ]
    if current["notes"]:
        lines.append("📝 Ответы — в заметках ведущего")
    if doc.get("ai_checked"):
        lines.append("🤖 ИИ проверил, что ничего не пропущено")
    if pdf_failed:
        lines.append("⚠️ PDF сделать не получилось — только .pptx")
    lines.append(f"\n<i>{SIGNATURE}</i>")
    return "\n".join(lines)


def _ordered_questions(doc: dict, user_id: int, current: dict) -> list[dict]:
    """Порядок как в последней презентации этого пакета, иначе — как в файле."""
    last = LAST_ORDER.get(user_id)
    if last and last[0] == doc.get("id"):
        return last[1]
    return session.prepare_questions(doc, current, shuffle=False)


# ─── Предпросмотр, таблица, раздатка, проверка ───────────────────────────────

async def _send_preview(message: Message, ctx, user_id: int) -> None:
    doc = session.doc_for(user_id)
    current = session.settings(ctx, user_id)
    template = session.template_for(user_id)
    questions = session.prepare_questions(doc, current, shuffle=False)
    specs = generator.build_specs(questions, current, template, doc.get("title") or doc["name"])

    picks = [i for i, s in enumerate(specs) if s.kind == "title"][:1]
    first = next(i for i, s in enumerate(specs) if s.kind == "question")
    picks += [first, first + 1]
    question_specs = [i for i, s in enumerate(specs) if s.kind == "question"]
    longest = max(question_specs, key=lambda i: sum(len(r.text) for p in specs[i].paras for r in p.runs))
    with_picture = next((i for i in question_specs if specs[i].pictures), None)
    for i in (longest, with_picture):
        if i is not None and i not in picks and len(picks) < 4:
            picks.append(i)

    dark = current["theme"] == "dark"
    images = await asyncio.to_thread(lambda: [preview.render(template, specs[i], dark_logo=dark) for i in picks])
    media = [InputMediaPhoto(png) for png in images]
    media[0] = InputMediaPhoto(images[0], caption=f"👁 Предпросмотр: {len(picks)} из {len(specs)} слайдов")
    await ctx.bot.send_media_group(message.chat_id, media)

    # Карточку — под картинками, чтобы не листать вверх
    await _show_card(message, ctx, user_id, edit=False)
    await delete(message)


async def _send_scoring(message: Message, ctx, user_id: int) -> None:
    doc = session.doc_for(user_id)
    current = session.settings(ctx, user_id)
    questions = _ordered_questions(doc, user_id, current)
    data = await asyncio.to_thread(extras.scoring_table, questions, current["numbering"], doc.get("title") or doc["name"])
    await ctx.bot.send_document(
        message.chat_id, InputFile(data, filename=f"{_file_stem(doc)} — таблица.xlsx"),
        caption="📊 Таблица результатов: ставь 1 за взятый вопрос — суммы по турам и места посчитаются сами.",
    )


async def _send_handout(message: Message, ctx, user_id: int) -> bool:
    doc = session.doc_for(user_id)
    current = session.settings(ctx, user_id)
    questions = _ordered_questions(doc, user_id, current)
    data = await asyncio.to_thread(extras.handout_document, questions, current["numbering"],
                                   doc.get("title") or doc["name"])
    if not data:
        return False
    await ctx.bot.send_document(
        message.chat_id, InputFile(data, filename=f"{_file_stem(doc)} — раздатка.docx"),
        caption="🖨 Раздаточный материал: каждый вопрос на своей странице — печатай по числу команд.",
    )
    return True


async def _send_review(message: Message, ctx, user_id: int) -> None:
    doc = session.doc_for(user_id)
    current = session.settings(ctx, user_id)
    label = session.label_for(current)
    status = await message.reply_text("🔍 Проверяю вопросы" + (" — ИИ читает пакет, это до минуты…" if GROQ_API_KEY else "…"))

    past = await asyncio.to_thread(session.history.others, user_id, doc.get("id"))
    local = await asyncio.to_thread(review.local_issues, doc["questions"], label, past)
    ai = await asyncio.to_thread(review.ai_issues, doc["questions"], label)

    lines = [f"🔍 <b>Проверка «{esc(doc['name'])}»</b>\n"]
    lines += [esc(issue) for issue in local] or ["✅ Повторов и подозрительных мест не нашёл."]
    if ai is None:
        lines.append("\n🤖 ИИ-проверка недоступна" + ("" if GROQ_API_KEY else " (не настроен ключ Groq)") + ".")
    elif ai:
        lines.append("\n<b>Замечания ИИ</b> (он может ошибаться — проверь сам):")
        lines += [esc(issue) for issue in ai]
    else:
        lines.append("\n🤖 ИИ проблем не нашёл.")
    lines.append("\nИсправить — «📋 Вопросы и правка».")

    await delete(status)
    chunk = ""
    for line in lines:  # режем по строкам, чтобы не разорвать HTML-теги
        if len(chunk) + len(line) > 3900:
            await message.reply_text(chunk, parse_mode=HTML)
            chunk = ""
        chunk += line + "\n"
    await message.reply_text(chunk, parse_mode=HTML)


# ─── Inline кнопки ───────────────────────────────────────────────────────────

DOC_ACTIONS = ("s:", "m:", "list:", "ed:", "ef:", "eh:", "edel:", "edel!:")
DOC_COMMANDS = ("gen", "card", "more", "pv", "score", "handout", "review", "title")


async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Маршрутизатор нажатий inline-кнопок. Каждая ветка отвечает на query ровно один раз."""
    query = update.callback_query
    data = query.data or ""
    user_id = query.from_user.id
    message = query.message

    if data == "noop":
        await query.answer()
        return
    if data.startswith("acc:"):
        await admin.access_callback(query, ctx, data)
        return
    if data.startswith("d:"):
        await _defaults_callback(query, ctx, data[2:])
        return
    if data.startswith("yd:"):
        await _yd_callback(query, ctx, data[3:])
        return
    if data.startswith("tpl:"):
        await _template_callback(query, ctx, data[4:])
        return
    if data.startswith("h:"):
        doc = session.history.load(user_id, data[2:])
        if doc is None:
            await query.answer("Этот пакет уже удалён из истории", show_alert=True)
            return
        session.DOCS[user_id] = doc
        await query.answer()
        await _show_card(message, ctx, user_id, edit=True)
        return

    # Всё ниже требует открытого пакета
    if not (data.startswith(DOC_ACTIONS) or data in DOC_COMMANDS):
        await query.answer("Эта кнопка устарела — пришли файл заново 🙂", show_alert=True)
        return
    doc = session.doc_for(user_id)
    if doc is None:
        await query.answer("Я не помню этот файл. Пришли его ещё раз 🙂", show_alert=True)
        return

    if data.startswith(("ed:", "ef:", "eh:", "edel:", "edel!:")):
        await _editor_callback(query, ctx, doc, data)
        return

    current = session.settings(ctx, user_id)
    if data.startswith("s:"):
        key = data[2:]
        if key == "tour":
            options = [None] + doc["tours"]
            doc["tour"] = options[(options.index(doc.get("tour")) + 1) % len(options)]
        else:
            session.toggle(current, key, user_id)
        await query.answer()
        await _show_card(message, ctx, user_id, edit=True)
    elif data.startswith("m:"):
        session.toggle(current, data[2:], user_id)
        await query.answer()
        await _show_more(message, ctx, user_id)
    elif data == "gen":
        if user_id in session.BUSY:
            await query.answer("Уже делаю, секунду… ⏳")
            return
        session.BUSY.add(user_id)  # до первого await — иначе двойное нажатие проскочит
        try:
            await query.answer("Поехали! 🚀")
        finally:
            await _generate(message, ctx, user_id)
    elif data == "card":
        await query.answer()
        await _show_card(message, ctx, user_id, edit=True)
    elif data == "more":
        await query.answer()
        await _show_more(message, ctx, user_id)
    elif data == "pv":
        await query.answer("Рисую слайды… 🎨")
        await ctx.bot.send_chat_action(message.chat_id, ChatAction.UPLOAD_PHOTO)
        await _send_preview(message, ctx, user_id)
    elif data == "score":
        await query.answer("Готовлю таблицу… 📊")
        await _send_scoring(message, ctx, user_id)
    elif data == "handout":
        await query.answer("Собираю раздатку… 🖨")
        if not await _send_handout(message, ctx, user_id):
            await message.reply_text("В вопросах нет картинок и «Раздаточного материала» — печатать нечего 🙂")
    elif data == "review":
        await query.answer()
        await _send_review(message, ctx, user_id)
    elif data == "title":
        ctx.user_data["awaiting"] = {"kind": "title", "doc": doc.get("id")}
        await query.answer()
        await message.reply_text("✏️ Пришли название турнира для титульного слайда:")
    elif data.startswith("list:"):
        await query.answer()
        await editor.show_list(message, doc, int(data[5:]))


async def _editor_callback(query, ctx, doc: dict, data: str) -> None:
    user_id = query.from_user.id
    action, _, rest = data.partition(":")
    raw_i, _, field = rest.partition(":")
    i = int(raw_i)
    if i >= len(doc["questions"]):
        await query.answer("Список изменился — открой его заново 📋", show_alert=True)
        return

    if action == "ed":
        await query.answer()
        await editor.show_editor(query.message, doc, i)
    elif action == "ef" and field in editor.FIELDS:
        await query.answer()
        await editor.ask_field(query.message, ctx, doc, i, field)
    elif action == "eh":
        editor.toggle_hard(user_id, doc, i)
        await query.answer("★ Сложный" if doc["questions"][i]["hard"] else "Обычный вопрос")
        await editor.show_editor(query.message, doc, i)
    elif action == "edel":
        await query.answer()
        await editor.show_editor(query.message, doc, i, confirm_delete=True)
    elif action == "edel!":
        editor.delete_question(user_id, doc, i)
        await query.answer("Удалил 🗑")
        if doc["questions"]:
            await editor.show_list(query.message, doc, min(i, len(doc["questions"]) - 1) // editor.PAGE_SIZE)
        else:
            await edit(query.message, "В пакете не осталось вопросов — пришли новый файл.")
    else:
        await query.answer()


async def _defaults_callback(query, ctx, key: str) -> None:
    user_id = query.from_user.id
    if key == "tpl_reset":
        session.delete_template(user_id)
        await query.answer("Вернул стандартный шаблон")
    else:
        session.toggle(session.settings(ctx, user_id), key, user_id)
        await query.answer()
    await session.edit(query.message, _settings_text(user_id), _defaults_markup(ctx, user_id))


def _defaults_markup(ctx, user_id: int):
    return kb.default_settings(session.settings(ctx, user_id), office.libreoffice_available(),
                               session.has_template(user_id))


def _settings_text(user_id: int) -> str:
    template = "свой ✅" if session.has_template(user_id) else "стандартный Brain-Do"
    return (
        "⚙️ <b>Настройки по умолчанию</b>\n\n"
        "Они применяются к каждому новому файлу — выбрал один раз и забыл. "
        "В карточке файла их можно поменять перед созданием.\n\n"
        "📝 <b>Заметки ведущего</b> — ответ в режиме докладчика.\n"
        "❓ <b>Вопрос на ответе</b> — текст вопроса мелко над ответом, чтобы зал вспомнил.\n"
        "🎬 <b>Титул, туры и финал</b> — служебные слайды.\n"
        "⚡ <b>Сразу делать презентацию</b> — без карточки, файл сразу после загрузки.\n\n"
        f"🖼 <b>Шаблон:</b> {template}. Чтобы поставить свой — пришли .pptx "
        "(1-й слайд — образец вопроса, 2-й — ответа)."
    )


# ─── ИИ-чат ──────────────────────────────────────────────────────────────────

async def _activate_ai_mode(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ctx.user_data["ai_mode"] = True
    ctx.user_data["ai_history"] = []
    doc = session.doc_for(update.effective_user.id)
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
                    "messages": [{"role": "system", "content": _ai_system_prompt(session.DOCS.get(update.effective_user.id))}]
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

    token = session.tokens.get(user_id)
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
            await session.edit(message, "📂 Загружаю список файлов…")
        else:
            status = await message.reply_text("📂 Загружаю список файлов…")
            edit = True
        try:
            user_data["yd_files"] = await yd.list_files(token)
        except yd.AuthError:
            session.tokens.delete(user_id)
            await session.edit(status, "🔑 Доступ к Яндекс Диску истёк. Нажми «📁 Яндекс Диск», чтобы войти заново.")
            return
        except Exception as e:
            logger.error("Ошибка списка файлов: %s", e)
            await session.edit(status, "😕 Яндекс Диск не ответил. Попробуй ещё раз через минуту.")
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
        await session.edit(status, text, markup)
    else:
        await status.reply_text(text, parse_mode=HTML, reply_markup=markup)


async def _yd_callback(query, ctx, action: str) -> None:
    user_id = query.from_user.id
    message = query.message
    token = session.tokens.get(user_id)

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
        ctx.user_data["awaiting"] = {"kind": "yd_search"}
        await query.answer()
        await message.reply_text("🔎 Напиши часть названия файла:")
    elif action == "all":
        ctx.user_data["yd_query"] = ""
        ctx.user_data["yd_page"] = 0
        await query.answer()
        await _yd_render(message, ctx.user_data, user_id, token, refresh=False, edit=True)
    elif action == "logout":
        session.tokens.delete(user_id)
        ctx.user_data.pop("yd_files", None)
        await query.answer()
        await session.edit(message, "🚪 Яндекс Диск отключён. Подключить снова — кнопка «📁 Яндекс Диск».")
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
    await session.edit(message, f"⏳ Скачиваю <b>{esc(file['name'])}</b>…")
    try:
        content = await yd.download_file(token, file["path"])
    except yd.AuthError:
        session.tokens.delete(user_id)
        await session.edit(message, "🔑 Доступ к Яндекс Диску истёк. Нажми «📁 Яндекс Диск», чтобы войти заново.")
        return
    except Exception as e:
        logger.error("Ошибка скачивания %s: %s", file["path"], e)
        await session.edit(message, "❌ Не удалось скачать файл. Попробуй ещё раз.")
        return

    ext = Path(file["name"]).suffix.lower()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"input{ext}"
        path.write_bytes(content)
        await session.edit(message, "🔍 Ищу вопросы и ответы…")
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
    await _yd_render(message, user_data, user_id, session.tokens.get(user_id), refresh=True, edit=False)


# ─── Ошибки ──────────────────────────────────────────────────────────────────

async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Глобальный обработчик: логируем, сообщаем админам и вежливо — пользователю."""
    if isinstance(ctx.error, BadRequest) and any(
        s in str(ctx.error).lower() for s in ("not modified", "query is too old")
    ):
        return
    logger.error("Необработанная ошибка", exc_info=ctx.error)
    await admin.report_error(ctx.bot, ctx.error, admin.describe(update))
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("😕 Что-то пошло не так. Попробуй ещё раз.")
        except Exception:
            pass
