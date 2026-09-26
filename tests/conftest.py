import os
import tempfile

# До импорта модулей бота: данные — во временную папку, без внешних сервисов
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="brain_do_test_")
os.environ["GROQ_API_KEY"] = ""
os.environ["YADISK_CLIENT_ID"] = "test-id"
os.environ["YADISK_CLIENT_SECRET"] = "test-secret"

import pytest  # noqa: E402

from tests.fakes import make_png  # noqa: E402


@pytest.fixture
def png_bytes() -> bytes:
    return make_png()


@pytest.fixture
def bot_env(tmp_path, monkeypatch):
    """Чистое состояние бота на каждый тест."""
    import access
    import handlers
    import session
    import storage
    from history import History
    from tests.fakes import FakeBot

    monkeypatch.setattr(storage, "STATS_FILE", tmp_path / "stats.json")
    monkeypatch.setattr(storage, "YADISK_TOKENS_FILE", tmp_path / "tokens.json")
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(access, "ACCESS_FILE", tmp_path / "access.json")
    monkeypatch.setattr(session, "TEMPLATES_DIR", tmp_path / "templates")

    session.stats = storage.Stats()
    session.tokens = storage.TokenStore()
    session.history = History(tmp_path / "history")
    session.access = access.Access()
    session.DOCS.clear()
    session.BUSY.clear()
    session.PENDING_TEMPLATES.clear()
    handlers.LAST_ORDER.clear()
    return FakeBot()
