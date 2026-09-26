"""
Доступ к боту.

ACCESS_MODE=open    — пользоваться может любой (как раньше).
ACCESS_MODE=private — только админы (ADMIN_IDS) и одобренные ими люди:
                      по запросу из бота или по одноразовой ссылке-приглашению.
"""

import json
import logging
import os
import secrets
import threading
import time

from config import ACCESS_MODE, ADMIN_IDS, DATA_DIR

logger = logging.getLogger(__name__)

ACCESS_FILE = DATA_DIR / "access.json"
INVITE_TTL = 7 * 24 * 3600


class Access:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        try:
            data = json.loads(ACCESS_FILE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {}
        except Exception as e:
            logger.error("Битый access.json: %s", e)
            data = {}
        self.approved: dict[str, dict] = data.get("approved", {})
        self.requests: dict[str, dict] = data.get("requests", {})
        self.invites: dict[str, float] = data.get("invites", {})

    @property
    def private(self) -> bool:
        return ACCESS_MODE == "private"

    def is_admin(self, user_id: int) -> bool:
        return user_id in ADMIN_IDS

    def is_allowed(self, user_id: int) -> bool:
        return not self.private or self.is_admin(user_id) or str(user_id) in self.approved

    def request(self, user_id: int, name: str) -> bool:
        """Запрос доступа. False — уже просил (не спамим админов)."""
        with self._lock:
            if str(user_id) in self.requests:
                return False
            self.requests[str(user_id)] = {"name": name, "at": time.time()}
            self._save()
            return True

    def approve(self, user_id: int, name: str | None = None) -> None:
        with self._lock:
            request = self.requests.pop(str(user_id), {})
            self.approved[str(user_id)] = {"name": name or request.get("name", str(user_id)), "since": time.time()}
            self._save()

    def deny(self, user_id: int) -> None:
        with self._lock:
            self.requests.pop(str(user_id), None)
            self._save()

    def revoke(self, user_id: int) -> None:
        with self._lock:
            self.approved.pop(str(user_id), None)
            self._save()

    def create_invite(self) -> str:
        with self._lock:
            now = time.time()
            self.invites = {t: at for t, at in self.invites.items() if now - at < INVITE_TTL}
            token = secrets.token_urlsafe(9)
            self.invites[token] = now
            self._save()
            return token

    def use_invite(self, token: str, user_id: int, name: str) -> bool:
        """Одноразовое приглашение: пускает пользователя и сгорает."""
        with self._lock:
            created = self.invites.pop(token, None)
            if created is None or time.time() - created > INVITE_TTL:
                return False
            self.requests.pop(str(user_id), None)
            self.approved[str(user_id)] = {"name": name, "since": time.time()}
            self._save()
            return True

    def _save(self) -> None:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            tmp = ACCESS_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "approved": self.approved, "requests": self.requests, "invites": self.invites,
            }, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, ACCESS_FILE)
        except Exception as e:
            logger.error("Не удалось сохранить доступы: %s", e)
