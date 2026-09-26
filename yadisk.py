"""
Интеграция с Яндекс Диском.
OAuth авторизация и работа с файлами через REST API.
"""

import logging
import secrets
import time
from urllib.parse import urlencode

import httpx

from config import (
    SUPPORTED_EXTENSIONS,
    YADISK_CLIENT_ID,
    YADISK_CLIENT_SECRET,
    YADISK_MAX_FILES,
    YADISK_REDIRECT_URI,
)

logger = logging.getLogger(__name__)

YADISK_API = "https://cloud-api.yandex.net/v1/disk"
YADISK_OAUTH = "https://oauth.yandex.ru"
STATE_TTL = 15 * 60  # сколько живёт ссылка авторизации, сек


class AuthError(Exception):
    """Токен отозван или истёк — нужна повторная авторизация."""


# ─── OAuth ───────────────────────────────────────────────────────────────────

# state → (user_id, chat_id, создан). Случайный state защищает от подмены:
# раньше в state лежал голый user_id, и чужой код можно было привязать к любому
_pending: dict[str, tuple[int, int, float]] = {}


def is_configured() -> bool:
    return bool(YADISK_CLIENT_ID and YADISK_CLIENT_SECRET)


def auth_url(user_id: int, chat_id: int) -> str:
    """URL для OAuth авторизации пользователя."""
    now = time.time()
    for key, (*_, created) in list(_pending.items()):
        if now - created > STATE_TTL:
            _pending.pop(key, None)
    state = secrets.token_urlsafe(16)
    _pending[state] = (user_id, chat_id, now)
    return f"{YADISK_OAUTH}/authorize?" + urlencode({
        "response_type": "code",
        "client_id": YADISK_CLIENT_ID,
        "redirect_uri": YADISK_REDIRECT_URI,
        "state": state,
        "force_confirm": "true",
    })


def pop_state(state: str) -> tuple[int, int] | None:
    """Кому принадлежит state (одноразово)."""
    entry = _pending.pop(state, None)
    if not entry or time.time() - entry[2] > STATE_TTL:
        return None
    return entry[0], entry[1]


def exchange_code_for_token(code: str) -> str | None:
    """Обменивает authorization code на access token (синхронно — вызывается из веб-потока)."""
    try:
        response = httpx.post(
            f"{YADISK_OAUTH}/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": YADISK_CLIENT_ID,
                "client_secret": YADISK_CLIENT_SECRET,
                "redirect_uri": YADISK_REDIRECT_URI,
            },
            timeout=15,
        )
        return response.json().get("access_token")
    except Exception as e:
        logger.error("Ошибка обмена кода на токен: %s", e)
        return None


# ─── Файлы ───────────────────────────────────────────────────────────────────

async def list_files(token: str) -> list[dict]:
    """
    Все подходящие документы на диске одним запросом (плоский список),
    свежие сверху. Возвращает [{name, path, modified}].
    """
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            f"{YADISK_API}/resources/files",
            headers={"Authorization": f"OAuth {token}"},
            params={
                "limit": 1000,
                "media_type": "document,text",
                "fields": "items.name,items.path,items.modified",
            },
        )
    if response.status_code == 401:
        raise AuthError()
    response.raise_for_status()

    items = response.json().get("items", [])
    files = [
        {"name": it["name"], "path": it["path"], "modified": it.get("modified", "")}
        for it in items
        if it["name"].lower().endswith(SUPPORTED_EXTENSIONS) and not it["name"].startswith("~$")
    ]
    files.sort(key=lambda f: f["modified"], reverse=True)
    return files[:YADISK_MAX_FILES]


async def download_file(token: str, path: str) -> bytes:
    """Скачивает файл с Яндекс Диска."""
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        response = await client.get(
            f"{YADISK_API}/resources/download",
            headers={"Authorization": f"OAuth {token}"},
            params={"path": path},
        )
        if response.status_code == 401:
            raise AuthError()
        response.raise_for_status()
        file_response = await client.get(response.json()["href"])
        file_response.raise_for_status()
        return file_response.content
