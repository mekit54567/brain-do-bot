"""
История пакетов: последние файлы каждого пользователя на диске (DATA_DIR/history),
чтобы пересобрать презентацию одной кнопкой и пережить перезапуск бота.
"""

import json
import logging
import os
import pickle
import time
import uuid
from pathlib import Path

from config import DATA_DIR

logger = logging.getLogger(__name__)

KEEP = 10


class History:
    def __init__(self, root: Path = DATA_DIR / "history") -> None:
        self.root = root

    def _dir(self, user_id: int) -> Path:
        return self.root / str(user_id)

    def _index(self, user_id: int) -> list[dict]:
        try:
            return json.loads((self._dir(user_id) / "index.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except Exception as e:
            logger.error("Битый индекс истории %s: %s", user_id, e)
            return []

    def _write_index(self, user_id: int, index: list[dict]) -> None:
        path = self._dir(user_id) / "index.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    def save(self, user_id: int, doc: dict) -> str:
        """Сохраняет пакет (новый или обновлённый). Возвращает его id."""
        folder = self._dir(user_id)
        folder.mkdir(parents=True, exist_ok=True)
        doc_id = doc.get("id") or uuid.uuid4().hex[:10]
        doc["id"] = doc_id
        with open(folder / f"{doc_id}.pickle", "wb") as f:
            pickle.dump(doc, f)

        index = [item for item in self._index(user_id) if item["id"] != doc_id]
        index.insert(0, {"id": doc_id, "name": doc["name"], "count": len(doc["questions"]), "saved": time.time()})
        for old in index[KEEP:]:
            (folder / f"{old['id']}.pickle").unlink(missing_ok=True)
        self._write_index(user_id, index[:KEEP])
        return doc_id

    def entries(self, user_id: int) -> list[dict]:
        return self._index(user_id)

    def load(self, user_id: int, doc_id: str) -> dict | None:
        if not doc_id.isalnum():
            return None
        try:
            with open(self._dir(user_id) / f"{doc_id}.pickle", "rb") as f:
                return pickle.load(f)
        except Exception:
            return None

    def latest(self, user_id: int) -> dict | None:
        index = self._index(user_id)
        return self.load(user_id, index[0]["id"]) if index else None

    def others(self, user_id: int, exclude_id: str | None) -> list[dict]:
        """Все сохранённые пакеты, кроме указанного (для поиска повторов)."""
        docs = []
        for item in self._index(user_id):
            if item["id"] != exclude_id and (doc := self.load(user_id, item["id"])):
                docs.append(doc)
        return docs
