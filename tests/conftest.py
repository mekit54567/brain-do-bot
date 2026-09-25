import struct
import zlib

import pytest


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


@pytest.fixture
def png_bytes() -> bytes:
    """Настоящий PNG 4×3 (синий) без внешних библиотек."""
    width, height = 4, 3
    raw = b"".join(b"\x00" + b"\x28\x78\xc8" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(raw))
        + _chunk(b"IEND", b"")
    )
