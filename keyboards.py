"""
Клавиатуры и кнопки Telegram бота.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton


def main_menu() -> ReplyKeyboardMarkup:
    """Главное меню с постоянными кнопками внизу."""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📁 Яндекс Диск"), KeyboardButton("📊 Статистика")],
            [KeyboardButton("🤖 Поговорить с ИИ"), KeyboardButton("❓ Помощь")],
        ],
        resize_keyboard=True,
    )


def exit_ai_menu() -> ReplyKeyboardMarkup:
    """Кнопка выхода из режима ИИ."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton("❌ Выйти из ИИ")]],
        resize_keyboard=True,
    )


def theme_keyboard() -> InlineKeyboardMarkup:
    """Выбор темы оформления презентации."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⬜ Белая (классика)", callback_data="theme_white"),
                InlineKeyboardButton("⬛ Тёмная", callback_data="theme_dark"),
            ],
        ]
    )


def options_keyboard(settings: dict) -> InlineKeyboardMarkup:
    """Настройки генерации презентации."""
    shuffle_icon = "✅" if settings["shuffle"] else "☐"
    numbering_icon = "✅" if settings["numbering"] else "☐"

    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"{shuffle_icon} Перемешать вопросы", callback_data="toggle_shuffle")],
            [InlineKeyboardButton(f"{numbering_icon} Нумерация вопросов", callback_data="toggle_numbering")],
            [InlineKeyboardButton("🚀 Создать презентацию!", callback_data="generate")],
        ]
    )


def question_list_keyboard() -> InlineKeyboardMarkup:
    """Предложение показать список вопросов после генерации."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋 Показать список", callback_data="show_list")],
            [InlineKeyboardButton("✖️ Не надо", callback_data="hide_list")],
        ]
    )


def yadisk_auth_keyboard(auth_url: str) -> InlineKeyboardMarkup:
    """Кнопки для авторизации Яндекс Диска."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔑 Войти через Яндекс", url=auth_url)],
            [InlineKeyboardButton("❓ Уже авторизовался", callback_data="yadisk_check")],
        ]
    )


def yadisk_files_keyboard(files: list[dict]) -> InlineKeyboardMarkup:
    """Список файлов с Яндекс Диска в виде кнопок."""
    buttons = []
    for f in files:
        name = f["name"]
        label = name[:40] + "..." if len(name) > 40 else name
        buttons.append(
            [InlineKeyboardButton(f"📄 {label}", callback_data=f"yadisk_file:{f['path']}")]
        )
    buttons.append([InlineKeyboardButton("🔄 Обновить", callback_data="yadisk_refresh")])
    return InlineKeyboardMarkup(buttons)
