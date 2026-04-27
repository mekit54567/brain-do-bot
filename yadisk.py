"""
Интеграция с Яндекс Диском.
OAuth авторизация и работа с файлами через REST API.
"""

import logging
from typing import Optional

import requests

from config import (
    YADISK_CLIENT_ID,
    YADISK_CLIENT_SECRET,
    YADISK_REDIRECT_URI,
    YADISK_MAX_FILES,
    YADISK_MAX_FILES_PER_DIR,
)

logger = logging.getLogger(__name__)

YADISK_API = "https://cloud-api.yandex.net/v1/disk"
YADISK_OAUTH = "https://oauth.yandex.ru"


def auth_url(user_id: int) -> str:
    """Возвращает URL для OAuth авторизации пользователя."""
    return (
        f"{YADISK_OAUTH}/authorize"
        f"?response_type=code"
        f"&client_id={YADISK_CLIENT_ID}"
        f"&redirect_uri={YADISK_REDIRECT_URI}"
        f"&state={user_id}"
        f"&force_confirm=true"
    )


def exchange_code_for_token(code: str) -> Optional[str]:
    """Обменивает authorization code на access token."""
    try:
        response = requests.post(
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


def list_docx_files(token: str, path: str = "disk:/") -> list[dict]:
    """
    Рекурсивно ищет .docx файлы на Яндекс Диске.
    Возвращает список словарей с ключами name и path.
    """
    try:
        response = requests.get(
            f"{YADISK_API}/resources",
            headers={"Authorization": f"OAuth {token}"},
            params={
                "path": path,
                "limit": 100,
                "fields": "name,path,type,_embedded",
            },
            timeout=15,
        )
        items = response.json().get("_embedded", {}).get("items", [])
        files: list[dict] = []

        for item in items:
            if item["type"] == "file" and item["name"].endswith(".docx"):
                files.append({"name": item["name"], "path": item["path"]})
            elif item["type"] == "dir":
                sub_files = list_docx_files(token, item["path"])
                files.extend(sub_files[:YADISK_MAX_FILES_PER_DIR])

        return files[:YADISK_MAX_FILES]

    except Exception as e:
        logger.error("Ошибка получения списка файлов: %s", e)
        return []


def download_file(token: str, path: str) -> Optional[bytes]:
    """Скачивает файл с Яндекс Диска и возвращает его содержимое."""
    try:
        # Получаем временную ссылку для скачивания
        response = requests.get(
            f"{YADISK_API}/resources/download",
            headers={"Authorization": f"OAuth {token}"},
            params={"path": path},
            timeout=15,
        )
        download_url = response.json().get("href")
        if not download_url:
            return None

        file_response = requests.get(download_url, timeout=30)
        return file_response.content

    except Exception as e:
        logger.error("Ошибка скачивания файла %s: %s", path, e)
        return None
