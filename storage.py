"""
Хранилище данных бота (статистика, токены Яндекс Диска).
Простое JSON-хранилище на файловой системе.
"""

import json
import logging
from typing import Any

from config import STATS_FILE, YADISK_TOKENS_FILE

logger = logging.getLogger(__name__)


def _load(path: str, default: Any) -> Any:
    """Загружает JSON из файла, возвращает default при ошибке."""
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _save(path: str, data: Any) -> None:
    """Сохраняет данные в JSON файл."""
    try:
        with open(path, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.error("Ошибка сохранения %s: %s", path, e)


# ─── Статистика ──────────────────────────────────────────────────────────────

def load_stats() -> dict:
    return _load(STATS_FILE, {"total_presentations": 0, "total_questions": 0})


def save_stats(stats: dict) -> None:
    _save(STATS_FILE, stats)


# ─── Токены Яндекс Диска ─────────────────────────────────────────────────────

def load_yadisk_tokens() -> dict:
    return _load(YADISK_TOKENS_FILE, {})


def save_yadisk_tokens(tokens: dict) -> None:
    _save(YADISK_TOKENS_FILE, tokens)
