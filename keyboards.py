"""
Клавиатуры и кнопки Telegram бота.

callback_data ограничен 64 байтами, поэтому везде короткие коды,
а длинные данные (пути файлов) лежат в user_data и передаются по индексу.
"""

import time

from telegram import InlineKeyboardButton as Btn
from telegram import InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from generator import THEMES
from session import NUMBERING_LABELS

# Тексты кнопок главного меню (они же — команды в handle_text)
BTN_YADISK = "📁 Яндекс Диск"
BTN_HISTORY = "🕘 История"
BTN_SETTINGS = "⚙️ Настройки"
BTN_AI = "🤖 Поговорить с ИИ"
BTN_STATS = "📊 Статистика"
BTN_HELP = "❓ Помощь"
BTN_EXIT_AI = "❌ Выйти из ИИ"


def _on(flag: bool) -> str:
    return "✅" if flag else "☐"


def main_menu() -> ReplyKeyboardMarkup:
    """Главное меню с постоянными кнопками внизу."""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_YADISK), KeyboardButton(BTN_HISTORY)],
            [KeyboardButton(BTN_SETTINGS), KeyboardButton(BTN_AI)],
            [KeyboardButton(BTN_STATS), KeyboardButton(BTN_HELP)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Пришли .docx или вставь вопросы текстом",
    )


def exit_ai_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[KeyboardButton(BTN_EXIT_AI)]], resize_keyboard=True,
                               input_field_placeholder="Спроси что угодно…")


def _main_settings(s: dict, prefix: str, pdf_available: bool) -> list[list[Btn]]:
    rows = [
        [Btn(f"🎨 {THEMES[s['theme']]['label']}", callback_data=f"{prefix}:theme"),
         Btn(f"🔢 {NUMBERING_LABELS[s['numbering']]}", callback_data=f"{prefix}:num")],
        [Btn(f"{_on(s['shuffle'])} Перемешать", callback_data=f"{prefix}:shuffle")],
    ]
    if pdf_available:
        rows[1].append(Btn(f"{_on(s['pdf'])} + PDF", callback_data=f"{prefix}:pdf"))
    return rows


def _extra_settings(s: dict, prefix: str) -> list[list[Btn]]:
    return [
        [Btn(f"{_on(s['notes'])} Заметки ведущего", callback_data=f"{prefix}:notes"),
         Btn(f"{_on(s['q_on_answer'])} Вопрос на ответе", callback_data=f"{prefix}:qa")],
        [Btn(f"{_on(s['service'])} Титул, туры и финал", callback_data=f"{prefix}:service")],
    ]


def file_card(s: dict, tour_label: str | None, pdf_available: bool, preview_available: bool) -> InlineKeyboardMarkup:
    """Карточка загруженного файла: главное — одним экраном."""
    rows = _main_settings(s, "s", pdf_available)
    if tour_label:
        rows.append([Btn(f"📑 {tour_label}", callback_data="s:tour")])
    rows.append([Btn("🚀 Создать презентацию", callback_data="gen")])
    second = [Btn("📋 Вопросы и правка", callback_data="list:0")]
    if preview_available:
        second.insert(0, Btn("👁 Предпросмотр", callback_data="pv"))
    rows.append(second)
    rows.append([Btn("🧰 Ещё: таблица, раздатка, проверка…", callback_data="more")])
    return InlineKeyboardMarkup(rows)


def more_menu(s: dict, has_handouts: bool, ai_available: bool) -> InlineKeyboardMarkup:
    """Дополнительные настройки и файлы для игры."""
    rows = _extra_settings(s, "m")
    if s["service"]:
        rows[1].append(Btn("✏️ Название", callback_data="title"))
    files = [Btn("📊 Таблица результатов", callback_data="score")]
    if has_handouts:
        files.append(Btn("🖨 Раздатка", callback_data="handout"))
    rows.append(files)
    rows.append([Btn("🔍 Проверить вопросы" + (" (+ИИ)" if ai_available else ""), callback_data="review")])
    rows.append([Btn("↩️ Назад", callback_data="card")])
    return InlineKeyboardMarkup(rows)


def default_settings(s: dict, pdf_available: bool, custom_template: bool) -> InlineKeyboardMarkup:
    """Настройки по умолчанию (/settings)."""
    rows = _main_settings(s, "d", pdf_available) + _extra_settings(s, "d")
    rows.append([Btn(f"{_on(s['instant'])} ⚡ Сразу делать презентацию", callback_data="d:instant")])
    if custom_template:
        rows.append([Btn("🗑 Вернуть стандартный шаблон", callback_data="d:tpl_reset")])
    return InlineKeyboardMarkup(rows)


def after_generate() -> InlineKeyboardMarkup:
    """Под готовой презентацией."""
    return InlineKeyboardMarkup([
        [Btn("🎨 Другие настройки", callback_data="card"),
         Btn("📋 Вопросы и правка", callback_data="list:0")],
    ])


# ─── Список вопросов и правка ────────────────────────────────────────────────

def question_list(page: int, pages: int, indices: list[int], labels: list[str]) -> InlineKeyboardMarkup:
    """Страница списка: кнопки-номера открывают правку вопроса."""
    rows, row = [], []
    for i, label in zip(indices, labels):
        row.append(Btn(f"✏️ {label}", callback_data=f"ed:{i}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    nav = []
    if page > 0:
        nav.append(Btn("◀️", callback_data=f"list:{page - 1}"))
    nav.append(Btn(f"{page + 1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        nav.append(Btn("▶️", callback_data=f"list:{page + 1}"))
    rows.append(nav)
    rows.append([Btn("↩️ К настройкам", callback_data="card")])
    return InlineKeyboardMarkup(rows)


def question_editor(i: int, q: dict, total: int, confirm_delete: bool = False) -> InlineKeyboardMarkup:
    if confirm_delete:
        return InlineKeyboardMarkup([
            [Btn("🗑 Да, удалить", callback_data=f"edel!:{i}"), Btn("✖️ Отмена", callback_data=f"ed:{i}")],
        ])
    nav = []
    if i > 0:
        nav.append(Btn("◀️ Пред.", callback_data=f"ed:{i - 1}"))
    if i < total - 1:
        nav.append(Btn("След. ▶️", callback_data=f"ed:{i + 1}"))
    rows = [
        [Btn("✏️ Вопрос", callback_data=f"ef:{i}:question"), Btn("✏️ Ответ", callback_data=f"ef:{i}:answer")],
        [Btn("✏️ Зачёт", callback_data=f"ef:{i}:accept"), Btn("✏️ Комментарий", callback_data=f"ef:{i}:comment")],
        [Btn(f"{'★ Сложный' if q.get('hard') else '☆ Обычный'}", callback_data=f"eh:{i}"),
         Btn("🗑 Удалить", callback_data=f"edel:{i}")],
    ]
    if nav:
        rows.append(nav)
    rows.append([Btn("📋 К списку", callback_data=f"list:{i // 10}"), Btn("↩️ К настройкам", callback_data="card")])
    return InlineKeyboardMarkup(rows)


# ─── История, шаблоны ────────────────────────────────────────────────────────

def history_list(items: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for item in items:
        date = time.strftime("%d.%m", time.localtime(item["saved"]))
        name = item["name"] if len(item["name"]) <= 34 else item["name"][:32] + "…"
        rows.append([Btn(f"📄 {name} · {item['count']} · {date}", callback_data=f"h:{item['id']}")])
    return InlineKeyboardMarkup(rows)


def template_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [Btn("✅ Сделать моим шаблоном", callback_data="tpl:ok"), Btn("✖️ Отмена", callback_data="tpl:no")],
    ])


# ─── Доступ ──────────────────────────────────────────────────────────────────

def access_request() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[Btn("🙋 Попросить доступ", callback_data="acc:req")]])


def access_decision(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [Btn("✅ Пустить", callback_data=f"acc:ok:{user_id}"), Btn("❌ Отказать", callback_data=f"acc:no:{user_id}")],
    ])


def access_users(users: dict[str, dict]) -> InlineKeyboardMarkup:
    rows = [[Btn(f"❌ {info['name'][:30]}", callback_data=f"acc:rm:{uid}")] for uid, info in users.items()]
    return InlineKeyboardMarkup(rows or [[Btn("Пока никого", callback_data="noop")]])


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
