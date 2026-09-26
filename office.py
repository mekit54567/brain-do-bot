"""
Конвертация через LibreOffice: .doc/.rtf/.odt → .docx и .pptx → .pdf.
Если LibreOffice не установлен (например, на Render без Docker) —
бот просто прячет эти возможности.
"""

import shutil
import subprocess
import tempfile
import threading
import uuid
from functools import lru_cache
from pathlib import Path

# LibreOffice прожорлив по памяти — конвертируем строго по одному
_lock = threading.Lock()


@lru_cache(maxsize=1)
def _soffice() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


def libreoffice_available() -> bool:
    return _soffice() is not None


def convert(path: str, target: str, out_dir: str, timeout: int = 180) -> str:
    """Конвертирует файл в формат target ("docx", "pdf"). Возвращает путь к результату."""
    soffice = _soffice()
    if not soffice:
        raise RuntimeError("LibreOffice не установлен")

    # Отдельный профиль на каждый запуск — иначе зависший процесс блокирует остальные
    profile = Path(tempfile.gettempdir()) / f"lo_profile_{uuid.uuid4().hex}"
    try:
        with _lock:
            subprocess.run(
                [soffice, f"-env:UserInstallation={profile.as_uri()}", "--headless",
                 "--convert-to", target, "--outdir", out_dir, path],
                capture_output=True, timeout=timeout, check=True,
            )
    finally:
        shutil.rmtree(profile, ignore_errors=True)

    result = Path(out_dir) / f"{Path(path).stem}.{target}"
    if not result.exists():
        raise RuntimeError(f"LibreOffice не смог сконвертировать в {target}")
    return str(result)


def pptx_to_pdf(pptx: bytes) -> bytes:
    """PDF-версия презентации."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "presentation.pptx"
        src.write_bytes(pptx)
        return Path(convert(str(src), "pdf", tmp)).read_bytes()
