"""
Хранилище данных бота (статистика, токены Яндекс Диска).
Простое JSON-хранилище в DATA_DIR с атомарной записью.
"""

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from config import DATA_DIR, STATS_FILE, YADISK_TOKENS_FILE

logger = logging.getLogger(__name__)

# Токены пишутся и из потока веб-сервера (OAuth callback), и из бота
_lock = threading.Lock()


def _load(path: Path, default: Any) -> Any:
    """Загружает JSON из файла, возвращает default при ошибке."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        logger.error("Ошибка чтения %s: %s", path, e)
        return default


def _save(path: Path, data: Any) -> None:
    """Атомарно сохраняет JSON: пишем во временный файл и подменяем."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as e:
        logger.error("Ошибка сохранения %s: %s", path, e)


# ─── Статистика ──────────────────────────────────────────────────────────────

class Stats:
    """Общая и персональная статистика."""

    def __init__(self) -> None:
        data = _load(STATS_FILE, {})
        self.total_presentations: int = data.get("total_presentations", 0)
        self.total_questions: int = data.get("total_questions", 0)
        self.users: dict[str, dict] = data.get("users", {})

    def record(self, user_id: int, questions: int) -> None:
        self.total_presentations += 1
        self.total_questions += questions
        user = self.users.setdefault(str(user_id), {"presentations": 0, "questions": 0})
        user["presentations"] += 1
        user["questions"] += questions
        self._save()

    def user(self, user_id: int) -> dict:
        return self.users.get(str(user_id), {"presentations": 0, "questions": 0})

    def _save(self) -> None:
        _save(STATS_FILE, {
            "total_presentations": self.total_presentations,
            "total_questions": self.total_questions,
            "users": self.users,
        })


# ─── Токены Яндекс Диска ─────────────────────────────────────────────────────

class TokenStore:
    """Токены Яндекс Диска по user_id (потокобезопасно)."""

    def __init__(self) -> None:
        self._tokens: dict[str, str] = _load(YADISK_TOKENS_FILE, {})

    def get(self, user_id: int) -> str | None:
        return self._tokens.get(str(user_id))

    def set(self, user_id: int, token: str) -> None:
        with _lock:
            self._tokens[str(user_id)] = token
            _save(YADISK_TOKENS_FILE, self._tokens)

    def delete(self, user_id: int) -> None:
        with _lock:
            if self._tokens.pop(str(user_id), None) is not None:
                _save(YADISK_TOKENS_FILE, self._tokens)
