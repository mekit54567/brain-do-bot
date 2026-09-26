"""
Общее состояние бота и утилиты, которыми пользуются все обработчики.
"""

import html
import random

from telegram import Message
from telegram.constants import ParseMode
from telegram.error import BadRequest

import generator
from access import Access
from config import DEFAULT_SETTINGS, TEMPLATES_DIR
from history import History
from storage import Stats, TokenStore

HTML = ParseMode.HTML

NUMBERING_LABELS = {"seq": "1, 2, 3…", "orig": "как в файле", "none": "без номеров"}
NUMBERING_ORDER = ["seq", "orig", "none"]

# Инициализируются в bot.py (и в тестах) до обработки первого сообщения
stats: Stats = None  # type: ignore[assignment]
tokens: TokenStore = None  # type: ignore[assignment]
history: History = None  # type: ignore[assignment]
access: Access = None  # type: ignore[assignment]

# Последний открытый пакет каждого пользователя (в памяти; копия — в истории на диске)
DOCS: dict[int, dict] = {}
# Кто сейчас ждёт презентацию (не в user_data — иначе флаг переживёт перезапуск)
BUSY: set[int] = set()
# Присланный .pptx, который пользователь ещё не подтвердил как шаблон
PENDING_TEMPLATES: dict[int, bytes] = {}


def esc(text) -> str:
    return html.escape(str(text), quote=False)


def plural(n: int, one: str, few: str, many: str) -> str:
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


async def edit(message: Message, text: str, markup=None) -> None:
    """edit_text, который не падает на «message is not modified»."""
    try:
        await message.edit_text(text, parse_mode=HTML, reply_markup=markup)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


async def edit_markup(message: Message, markup) -> None:
    try:
        await message.edit_reply_markup(reply_markup=markup)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


async def delete(message: Message) -> None:
    try:
        await message.delete()
    except BadRequest:
        pass


# ─── Настройки ───────────────────────────────────────────────────────────────

def theme_order(user_id: int) -> list[str]:
    return (["template"] if has_template(user_id) else []) + ["white", "dark"]


def settings(ctx, user_id: int) -> dict:
    """Настройки пользователя (запоминаются между файлами и перезапусками)."""
    current = {**DEFAULT_SETTINGS, **ctx.user_data.get("settings", {})}
    if current["theme"] not in theme_order(user_id):
        current["theme"] = "white"
    if current["numbering"] not in NUMBERING_LABELS:
        current["numbering"] = DEFAULT_SETTINGS["numbering"]
    ctx.user_data["settings"] = current
    return current


TOGGLES = {"shuffle": "shuffle", "pdf": "pdf", "notes": "notes", "qa": "q_on_answer",
           "service": "service", "instant": "instant"}


def toggle(current: dict, key: str, user_id: int) -> None:
    if key == "theme":
        order = theme_order(user_id)
        current["theme"] = order[(order.index(current["theme"]) + 1) % len(order)]
    elif key == "num":
        current["numbering"] = NUMBERING_ORDER[(NUMBERING_ORDER.index(current["numbering"]) + 1) % 3]
    elif key in TOGGLES:
        current[TOGGLES[key]] = not current[TOGGLES[key]]


# ─── Пакеты ──────────────────────────────────────────────────────────────────

def doc_for(user_id: int) -> dict | None:
    """Текущий пакет; после перезапуска — последний из истории."""
    if user_id not in DOCS:
        restored = history.latest(user_id)
        if restored:
            DOCS[user_id] = restored
    return DOCS.get(user_id)


def remember(user_id: int, doc: dict) -> None:
    DOCS[user_id] = doc
    history.save(user_id, doc)


def selected(doc: dict) -> list[dict]:
    if doc.get("tour") is None:
        return doc["questions"]
    return [q for q in doc["questions"] if q.get("tour") == doc["tour"]]


def prepare_questions(doc: dict, current: dict, shuffle: bool = True) -> list[dict]:
    """Вопросы в порядке презентации с порядковыми номерами (копии — оригинал не трогаем)."""
    questions = [dict(q) for q in selected(doc)]
    if shuffle and current["shuffle"]:
        random.shuffle(questions)
    for i, q in enumerate(questions, 1):
        q["number"] = i
    return questions


def label_for(current: dict):
    return lambda q: generator.question_label(q, current["numbering"] if current["numbering"] != "none" else "seq")


# ─── Свои шаблоны ────────────────────────────────────────────────────────────

def _template_path(user_id: int):
    return TEMPLATES_DIR / f"{user_id}.pptx"


def has_template(user_id: int) -> bool:
    return _template_path(user_id).exists()


def template_for(user_id: int) -> generator.Template:
    path = _template_path(user_id)
    if path.exists():
        try:
            return generator.load_template(path.read_bytes())
        except Exception:
            pass  # испорченный шаблон — молча используем стандартный
    return generator.standard_template()


def save_template(user_id: int, data: bytes) -> None:
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    _template_path(user_id).write_bytes(data)


def delete_template(user_id: int) -> None:
    _template_path(user_id).unlink(missing_ok=True)
