"""
Администрирование: закрытый доступ, приглашения и отчёты об ошибках админам.
"""

import logging
import time
import traceback

from telegram import LinkPreviewOptions, Update
from telegram.error import TelegramError
from telegram.ext import ApplicationHandlerStop, ContextTypes

import keyboards as kb
import session
from config import ADMIN_IDS
from session import HTML, edit, esc

logger = logging.getLogger(__name__)

ERROR_THROTTLE = 600  # одну и ту же ошибку присылаем админу не чаще раза в 10 минут
_last_error: dict[str, float] = {}


def _name(user) -> str:
    return user.full_name + (f" (@{user.username})" if user.username else "")


async def _notify_admins(bot, text: str, markup=None) -> None:
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode=HTML, reply_markup=markup)
        except TelegramError as e:
            logger.warning("Не удалось написать админу %s: %s", admin_id, e)


# ─── Закрытый доступ ─────────────────────────────────────────────────────────

async def access_guard(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Стоит перед всеми обработчиками. В закрытом режиме чужих дальше не пускает."""
    user = update.effective_user
    if user is None or session.access.is_allowed(user.id):
        return

    message = update.effective_message
    text = (message.text or "") if message else ""
    if text.startswith("/id"):
        return
    if text.startswith("/start inv_"):
        token = text.split("inv_", 1)[1].strip()
        if session.access.use_invite(token, user.id, _name(user)):
            await _notify_admins(ctx.bot, f"🎟 <b>{esc(_name(user))}</b> вошёл по приглашению.")
            return  # дальше обычный /start

    query = update.callback_query
    if query and query.data == "acc:req":
        if session.access.request(user.id, _name(user)):
            await _notify_admins(
                ctx.bot,
                f"🙋 <b>{esc(_name(user))}</b> (id <code>{user.id}</code>) просит доступ к боту.",
                kb.access_decision(user.id),
            )
            await query.answer("Запрос отправлен ✅")
            await edit(query.message, "⏳ Запрос отправлен. Как только админ одобрит — я напишу.")
        else:
            await query.answer("Запрос уже отправлен, ждём ответа ⏳", show_alert=True)
    elif query:
        await query.answer("🔒 Нет доступа", show_alert=True)
    elif message:
        await message.reply_text(
            "🔒 <b>Это закрытый бот.</b>\n\nМожно попросить доступ — админ получит уведомление.",
            parse_mode=HTML,
            reply_markup=kb.access_request(),
        )
    raise ApplicationHandlerStop


async def access_callback(query, ctx, data: str) -> None:
    """acc:ok|no|rm:<user_id> — решения админа."""
    if data == "acc:req":
        await query.answer("У тебя уже есть доступ 🙂")
        return
    if not session.access.is_admin(query.from_user.id):
        await query.answer("Только для админа", show_alert=True)
        return

    _, action, raw_id = data.split(":")
    user_id = int(raw_id)
    name = session.access.requests.get(raw_id, session.access.approved.get(raw_id, {})).get("name", raw_id)

    if action == "ok":
        session.access.approve(user_id)
        await query.answer("Пустил ✅")
        await edit(query.message, f"✅ Доступ открыт: <b>{esc(name)}</b>")
        await _send(ctx.bot, user_id, "✅ Доступ к боту открыт! Нажми /start")
    elif action == "no":
        session.access.deny(user_id)
        await query.answer("Отказано")
        await edit(query.message, f"❌ Отказано: <b>{esc(name)}</b>")
        await _send(ctx.bot, user_id, "😔 Админ не одобрил запрос на доступ.")
    elif action == "rm":
        session.access.revoke(user_id)
        await query.answer(f"Доступ закрыт: {name}")
        await edit(query.message, _users_text(), kb.access_users(session.access.approved))


async def _send(bot, chat_id: int, text: str) -> None:
    try:
        await bot.send_message(chat_id, text)
    except TelegramError:
        pass  # пользователь мог заблокировать бота


# ─── Команды ─────────────────────────────────────────────────────────────────

async def cmd_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    role = "\n👑 Ты админ этого бота." if session.access.is_admin(user.id) else ""
    await update.effective_message.reply_text(
        f"🪪 Твой Telegram ID: <code>{user.id}</code>{role}\n\n"
        "Чтобы стать админом, добавь этот номер в переменную <code>ADMIN_IDS</code> на сервере.",
        parse_mode=HTML,
    )


async def cmd_invite(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update):
        return
    token = session.access.create_invite()
    link = f"https://t.me/{ctx.bot.username}?start=inv_{token}"
    note = "" if session.access.private else (
        "\n\nℹ️ Сейчас бот открыт для всех (<code>ACCESS_MODE=open</code>) — приглашения понадобятся, "
        "когда включишь закрытый режим."
    )
    await update.effective_message.reply_text(
        f"🎟 <b>Приглашение</b> — одноразовое, действует 7 дней:\n{link}{note}",
        parse_mode=HTML,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


async def cmd_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update):
        return
    await update.effective_message.reply_text(
        _users_text(), parse_mode=HTML, reply_markup=kb.access_users(session.access.approved)
    )
    for raw_id, info in session.access.requests.items():
        await update.effective_message.reply_text(
            f"🙋 Ждёт ответа: <b>{esc(info['name'])}</b> (id <code>{raw_id}</code>)",
            parse_mode=HTML,
            reply_markup=kb.access_decision(int(raw_id)),
        )


def _users_text() -> str:
    mode = "🔒 закрытый" if session.access.private else "🌍 открыт для всех"
    count = len(session.access.approved)
    return (f"👥 <b>Пользователи</b> · режим: {mode}\n"
            f"Одобрено: {count}. Нажми на человека, чтобы закрыть ему доступ.")


async def _require_admin(update: Update) -> bool:
    if session.access.is_admin(update.effective_user.id):
        return True
    await update.effective_message.reply_text("Эта команда только для админа. Свой ID — /id")
    return False


# ─── Ошибки ──────────────────────────────────────────────────────────────────

def describe(update: object) -> str:
    """Что делал пользователь — без содержимого его сообщений."""
    if not isinstance(update, Update):
        return "—"
    who = f"пользователь {update.effective_user.id}" if update.effective_user else "без пользователя"
    message = update.effective_message
    if update.callback_query:
        what = f"кнопка «{update.callback_query.data}»"
    elif message and message.document:
        what = f"файл {message.document.file_name or ''}"
    elif message and (message.text or "").startswith("/"):
        what = f"команда {message.text.split()[0]}"
    elif message:
        what = f"сообщение ({len(message.text or '')} символов)"
    else:
        what = "обновление"
    return f"{who}, {what}"


async def report_error(bot, error: BaseException | None, where: str) -> None:
    """Присылает админам краткий отчёт об ошибке (не чаще раза в 10 минут на одну ошибку)."""
    if not ADMIN_IDS or error is None:
        return
    key = f"{type(error).__name__}:{str(error)[:80]}"
    now = time.time()
    if now - _last_error.get(key, 0) < ERROR_THROTTLE:
        return
    _last_error[key] = now
    tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))[-2500:]
    await _notify_admins(bot, f"⚠️ <b>Ошибка в боте</b>\n{esc(where)}\n\n<pre>{esc(tb)}</pre>")
