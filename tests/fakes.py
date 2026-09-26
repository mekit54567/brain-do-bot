"""
Подставной Telegram для тестов обработчиков: запоминает все сообщения,
правки и ответы на кнопки, проверяет HTML-разметку так же строго, как Telegram.
"""

import itertools
import struct
import zlib
from html.parser import HTMLParser
from types import SimpleNamespace

ALLOWED_TAGS = {"b", "i", "u", "s", "code", "pre", "a", "tg-spoiler", "blockquote"}
_ids = itertools.count(1)


class _HTMLCheck(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack = []

    def handle_starttag(self, tag, attrs):
        assert tag in ALLOWED_TAGS, f"Telegram не знает тег <{tag}>"
        self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack and self.stack[-1] == tag, f"незакрытый/лишний тег </{tag}>"
        self.stack.pop()


def check_html(text: str, parse_mode) -> None:
    if parse_mode and str(parse_mode).upper().endswith("HTML"):
        checker = _HTMLCheck()
        checker.feed(text)
        checker.close()
        assert not checker.stack, f"незакрытые теги: {checker.stack}"
    assert len(text) <= 4096, "сообщение длиннее лимита Telegram"


def buttons(markup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row] if markup else []


def callback_data(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data] if markup else []


class FakeMessage:
    def __init__(self, bot, chat_id: int, text=None, markup=None, document=None, kind="text", **extra):
        self.bot = bot
        self.chat_id = chat_id
        self.message_id = next(_ids)
        self.text = text if kind == "text" else None
        self.caption = extra.pop("caption", None)
        self.reply_markup = markup
        self.document = document
        self.kind = kind
        self.deleted = False
        self.__dict__.update(extra)

    async def reply_text(self, text, parse_mode=None, reply_markup=None, **kw):
        return self.bot._send(self.chat_id, text=text, markup=reply_markup, parse_mode=parse_mode)

    async def reply_photo(self, photo, caption=None, parse_mode=None, reply_markup=None, **kw):
        return self.bot._send(self.chat_id, kind="photo", caption=caption, markup=reply_markup,
                              parse_mode=parse_mode, photo=photo)

    async def edit_text(self, text, parse_mode=None, reply_markup=None, **kw):
        assert self.text is not None, "edit_text у сообщения без текста"
        check_html(text, parse_mode)
        self.text = text
        self.reply_markup = reply_markup
        self.bot.log.append(("edit", self))
        return self

    async def edit_reply_markup(self, reply_markup=None, **kw):
        self.reply_markup = reply_markup
        return self

    async def delete(self):
        self.deleted = True
        return True


class FakeFile:
    def __init__(self, data: bytes) -> None:
        self.data = data

    async def download_to_drive(self, path):
        with open(path, "wb") as f:
            f.write(self.data)

    async def download_as_bytearray(self):
        return bytearray(self.data)


class FakeBot:
    username = "brain_do_test_bot"

    def __init__(self) -> None:
        self.log: list = []
        self.files: dict[str, bytes] = {}

    def _send(self, chat_id, text=None, markup=None, parse_mode=None, kind="text", **extra):
        if text is not None:
            check_html(text, parse_mode)
        if extra.get("caption"):
            check_html(extra["caption"], parse_mode)
        message = FakeMessage(self, chat_id, text=text, markup=markup, kind=kind, **extra)
        self.log.append(("send", message))
        return message

    def sent(self, chat_id=None, kind=None) -> list[FakeMessage]:
        return [m for action, m in self.log if action == "send"
                and (chat_id is None or m.chat_id == chat_id) and (kind is None or m.kind == kind)]

    def last(self, chat_id=None, kind=None) -> FakeMessage:
        return self.sent(chat_id, kind)[-1]

    async def get_file(self, file_id):
        return FakeFile(self.files[file_id])

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None, **kw):
        return self._send(chat_id, text=text, markup=reply_markup, parse_mode=parse_mode)

    async def send_document(self, chat_id, document, caption=None, parse_mode=None, reply_markup=None, **kw):
        return self._send(chat_id, kind="document", caption=caption, parse_mode=parse_mode, markup=reply_markup,
                          filename=document.filename, data=document.input_file_content)

    async def send_media_group(self, chat_id, media, **kw):
        return [self._send(chat_id, kind="photo", caption=m.caption,
                           photo=getattr(m.media, "input_file_content", m.media)) for m in media]

    async def send_chat_action(self, *a, **kw):
        return True


class User(SimpleNamespace):
    def __init__(self, id: int, first_name="Папа", username=None):
        super().__init__(id=id, first_name=first_name, username=username, full_name=first_name)


class Harness:
    """Один пользователь в личке с ботом."""

    def __init__(self, bot: FakeBot, user_id: int = 7, name: str = "Папа") -> None:
        self.bot = bot
        self.user = User(user_id, name)
        self.user_data: dict = {}

    def ctx(self, args=None):
        return SimpleNamespace(user_data=self.user_data, bot=self.bot, args=args or [], error=None)

    def _update(self, message=None, query=None):
        return SimpleNamespace(effective_user=self.user, effective_message=message, message=message,
                               callback_query=query)

    def text_update(self, text: str):
        message = FakeMessage(self.bot, self.user.id, text=text)
        return self._update(message)

    def document_update(self, file_name: str, data: bytes):
        file_id = f"file{next(_ids)}"
        self.bot.files[file_id] = data
        document = SimpleNamespace(file_name=file_name, file_size=len(data), file_id=file_id)
        message = FakeMessage(self.bot, self.user.id, kind="document", document=document)
        return self._update(message)

    def query_update(self, data: str, message: FakeMessage):
        answers = []

        async def answer(text=None, show_alert=False, **kw):
            answers.append((text, show_alert))

        query = SimpleNamespace(data=data, from_user=self.user, message=message, answer=answer, answers=answers)
        return self._update(message, query), answers


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def make_png(width: int = 4, height: int = 3) -> bytes:
    """Настоящий PNG (синий) без внешних библиотек."""
    raw = b"".join(b"\x00" + b"\x28\x78\xc8" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(raw))
        + _chunk(b"IEND", b"")
    )
