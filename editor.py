"""
Список вопросов и правка прямо в боте: исправить текст, ответ, зачёт,
комментарий, пометить сложным, удалить — без правки Word и повторной загрузки.
"""

from telegram import Message

import keyboards as kb
from config import QUESTION_PREVIEW_MAX_LEN
import session
from session import HTML, edit, esc

PAGE_SIZE = 10
FIELDS = {"question": "текст вопроса", "answer": "ответ", "accept": "зачёт", "comment": "комментарий"}
OPTIONAL = ("accept", "comment")


def _short(text: str, limit: int) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _number(q: dict, i: int) -> str:
    return str(q.get("orig_number") or i + 1)


async def show_list(message: Message, doc: dict, page: int) -> None:
    questions = doc["questions"]
    pages = max(1, (len(questions) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    indices = list(range(page * PAGE_SIZE, min(len(questions), (page + 1) * PAGE_SIZE)))

    lines = [f"📋 <b>{esc(doc['name'])}</b> — {len(questions)} шт. Нажми номер, чтобы исправить.\n"]
    current_tour = None
    for i in indices:
        q = questions[i]
        if q.get("tour") and q["tour"] != current_tour and len(doc["tours"]) > 1:
            current_tour = q["tour"]
            lines.append(f"<b>— {esc(current_tour)} —</b>")
        text = _short(q["question"], QUESTION_PREVIEW_MAX_LEN) or "🖼 (картинка)"
        lines.append(f"<b>{_number(q, i)}.</b> {esc(text)}{' ★' if q.get('hard') else ''}\n"
                     f"↳ <i>{esc(_short(q['answer'], 80))}</i>\n")

    markup = kb.question_list(page, pages, indices, [_number(questions[i], i) for i in indices])
    await _show(message, "\n".join(lines).rstrip(), markup)


async def show_editor(message: Message, doc: dict, i: int, confirm_delete: bool = False) -> None:
    questions = doc["questions"]
    i = max(0, min(i, len(questions) - 1))
    q = questions[i]
    parts = [f"✏️ <b>Вопрос {_number(q, i)}</b>" + (f" · {esc(q['tour'])}" if q.get("tour") else "")
             + (" · ★ сложный" if q.get("hard") else "")]
    if q.get("q_pictures"):
        parts.append(f"🖼 картинок в вопросе: {len(q['q_pictures'])}")
    parts.append(f"\n<b>Вопрос:</b> {esc(q['question']) or '—'}")
    parts.append(f"<b>Ответ:</b> {esc(q['answer'])}")
    if q.get("accept"):
        parts.append(f"<b>Зачёт:</b> {esc(q['accept'])}")
    if q.get("comment"):
        parts.append(f"<b>Комментарий:</b> {esc(q['comment'])}")
    if confirm_delete:
        parts.append("\n🗑 <b>Точно удалить этот вопрос?</b>")

    text = "\n".join(parts)
    if len(text) > 4000:
        text = text[:3990] + "…"
    await _show(message, text, kb.question_editor(i, q, len(questions), confirm_delete))


async def ask_field(message: Message, ctx, doc: dict, i: int, field: str) -> None:
    """Просим прислать новое значение поля следующим сообщением."""
    q = doc["questions"][i]
    ctx.user_data["awaiting"] = {"kind": "edit", "i": i, "field": field, "doc": doc.get("id")}
    current = q.get(field) or ""
    hint = " Чтобы убрать — пришли «-»." if field in OPTIONAL else ""
    text = f"✏️ Пришли новый {FIELDS[field]} для вопроса {_number(q, i)}.{hint}"
    if current:
        text += f"\n\nСейчас (нажми, чтобы скопировать):\n<code>{esc(current[:3500])}</code>"
    await message.reply_text(text, parse_mode=HTML)


async def apply_edit(message: Message, user_id: int, doc: dict, i: int, field: str, value: str) -> None:
    value = value.strip()
    if i >= len(doc["questions"]):
        await message.reply_text("Этот вопрос уже удалён 🤷")
        return
    q = doc["questions"][i]
    if value == "-" and field in OPTIONAL:
        q[field] = None
    elif value in ("", "-"):
        await message.reply_text(f"{FIELDS[field].capitalize()} не может быть пустым.")
        return
    else:
        q[field] = value
    session.history.save(user_id, doc)
    await message.reply_text("✅ Сохранил.")
    await show_editor(message, doc, i)


def toggle_hard(user_id: int, doc: dict, i: int) -> None:
    q = doc["questions"][i]
    q["hard"] = not q.get("hard")
    session.history.save(user_id, doc)


def delete_question(user_id: int, doc: dict, i: int) -> None:
    del doc["questions"][i]
    for n, q in enumerate(doc["questions"], 1):
        q["number"] = n
    session.history.save(user_id, doc)


async def _show(message: Message, text: str, markup) -> None:
    """Редактируем сообщение, если это текст; под документом — присылаем новое."""
    if message.text is not None:
        await edit(message, text, markup)
    else:
        await message.reply_text(text, parse_mode=HTML, reply_markup=markup)
