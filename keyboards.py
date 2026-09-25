"""
Клавиатуры и кнопки Telegram бота.

callback_data ограничен 64 байтами, поэтому везде короткие коды,
а длинные данные (пути файлов) лежат в user_data и передаются по индексу.
"""

from telegram import InlineKeyboardButton as Btn
from telegram import InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from generator import THEMES

# Тексты кнопок главного меню (они же — команды в handle_text)
BTN_YADISK = "📁 Яндекс Диск"
BTN_SETTINGS = "⚙️ Настройки"
BTN_AI = "🤖 Поговорить с ИИ"
BTN_STATS = "📊 Статистика"
BTN_HELP = "❓ Помощь"
BTN_EXIT_AI = "❌ Выйти из ИИ"

NUMBERING_LABELS = {"seq": "1, 2, 3…", "orig": "как в файле", "none": "без номеров"}
NUMBERING_ORDER = ["seq", "orig", "none"]
THEME_ORDER = list(THEMES)


def _on(flag: bool) -> str:
    return "✅" if flag else "☐"


def main_menu() -> ReplyKeyboardMarkup:
    """Главное меню с постоянными кнопками внизу."""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_YADISK), KeyboardButton(BTN_SETTINGS)],
            [KeyboardButton(BTN_AI), KeyboardButton(BTN_STATS)],
            [KeyboardButton(BTN_HELP)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Пришли .docx или вставь вопросы текстом",
    )


def exit_ai_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[KeyboardButton(BTN_EXIT_AI)]], resize_keyboard=True,
                               input_field_placeholder="Спроси что угодно…")


def _settings_rows(settings: dict, prefix: str, pdf_available: bool) -> list[list[Btn]]:
    """Общие кнопки настроек. prefix: "s" — карточка файла, "d" — настройки по умолчанию."""
    theme = THEMES[settings["theme"]]["label"]
    numbering = NUMBERING_LABELS[settings["numbering"]]
    rows = [
        [Btn(f"🎨 {theme}", callback_data=f"{prefix}:theme"),
         Btn(f"🔢 {numbering}", callback_data=f"{prefix}:num")],
        [Btn(f"{_on(settings['shuffle'])} Перемешать", callback_data=f"{prefix}:shuffle")],
    ]
    if pdf_available:
        rows[1].append(Btn(f"{_on(settings['pdf'])} + PDF", callback_data=f"{prefix}:pdf"))
    return rows


def file_card(settings: dict, tour_label: str | None, pdf_available: bool) -> InlineKeyboardMarkup:
    """Карточка загруженного файла: настройки + «Создать» одним экраном."""
    rows = _settings_rows(settings, "s", pdf_available)
    if tour_label:
        rows.append([Btn(f"📑 {tour_label}", callback_data="s:tour")])
    rows.append([Btn("🚀 Создать презентацию", callback_data="gen")])
    rows.append([Btn("📋 Посмотреть вопросы", callback_data="list:0")])
    return InlineKeyboardMarkup(rows)


def default_settings(settings: dict, pdf_available: bool) -> InlineKeyboardMarkup:
    """Настройки по умолчанию (/settings)."""
    rows = _settings_rows(settings, "d", pdf_available)
    rows.append([Btn(f"{_on(settings['instant'])} ⚡ Сразу делать презентацию", callback_data="d:instant")])
    return InlineKeyboardMarkup(rows)


def after_generate() -> InlineKeyboardMarkup:
    """Под готовой презентацией."""
    return InlineKeyboardMarkup([
        [Btn("🎨 Другие настройки", callback_data="card"),
         Btn("📋 Список вопросов", callback_data="list:0")],
    ])


def question_list(page: int, pages: int) -> InlineKeyboardMarkup:
    nav = []
    if page > 0:
        nav.append(Btn("◀️", callback_data=f"list:{page - 1}"))
    nav.append(Btn(f"{page + 1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        nav.append(Btn("▶️", callback_data=f"list:{page + 1}"))
    return InlineKeyboardMarkup([nav, [Btn("↩️ К настройкам", callback_data="card")]])


# ─── Яндекс Диск ─────────────────────────────────────────────────────────────

def yadisk_auth(auth_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [Btn("🔑 Войти через Яндекс", url=auth_url)],
        [Btn("❓ Уже авторизовался", callback_data="yd:check")],
    ])


def yadisk_files(files: list[dict], page: int, page_size: int, searching: bool) -> InlineKeyboardMarkup:
    """Страница списка файлов. В callback — индекс в общем списке."""
    start = page * page_size
    rows = []
    for i, f in enumerate(files[start:start + page_size], start):
        name = f["name"]
        label = name if len(name) <= 42 else name[:40] + "…"
        rows.append([Btn(f"📄 {label}", callback_data=f"yd:f:{i}")])

    pages = max(1, (len(files) + page_size - 1) // page_size)
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(Btn("◀️", callback_data=f"yd:p:{page - 1}"))
        nav.append(Btn(f"{page + 1}/{pages}", callback_data="noop"))
        if page < pages - 1:
            nav.append(Btn("▶️", callback_data=f"yd:p:{page + 1}"))
        rows.append(nav)

    rows.append([
        Btn("✖️ Сбросить поиск" if searching else "🔎 Поиск", callback_data="yd:all" if searching else "yd:search"),
        Btn("🔄 Обновить", callback_data="yd:refresh"),
    ])
    rows.append([Btn("🚪 Отключить диск", callback_data="yd:logout")])
    return InlineKeyboardMarkup(rows)
