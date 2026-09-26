"""
Предпросмотр слайдов картинками.

Рисуем то же описание слайда (SlideSpec), из которого собирается .pptx, —
поэтому картинка совпадает с файлом. Работает за миллисекунды и без
LibreOffice; нужен только TTF-шрифт (Carlito в Docker, Calibri в Windows).
"""

import io
import os
from functools import lru_cache
from pathlib import Path

from lxml import etree
from PIL import Image, ImageDraw, ImageFont

from generator import (
    LOGO_DARK_PATH, A, P, R_NS, EMU_PER_PT, Box, SlideSpec, Template, _ph_kind, _rels_map, _resolve,
    master_path, placeholder_chain, qn,
)

FONT_DIRS = [
    "/usr/share/fonts/truetype/crosextra",
    "/usr/share/fonts/truetype/dejavu",
    "C:/Windows/Fonts",
    "/Library/Fonts",
]
FONT_FILES = {
    False: ["Carlito-Regular.ttf", "calibri.ttf", "DejaVuSans.ttf", "Arial.ttf"],
    True: ["Carlito-Bold.ttf", "calibrib.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"],
}
SYMBOL_FONT_FILES = ["DejaVuSans.ttf", "seguisym.ttf", "Symbola.ttf"]  # для ★ и прочих значков
SYMBOLS = set("★☆✓✔✗✘→←↑↓♪♫☺")
LINE_HEIGHT = 1.08     # замер в PowerPoint для интервала 90%
INSET_X, INSET_Y = 91425, 45700


@lru_cache(maxsize=2)
def _font_path(bold: bool) -> str | None:
    env = os.environ.get("PREVIEW_FONT_BOLD" if bold else "PREVIEW_FONT")
    if env and Path(env).exists():
        return env
    for folder in FONT_DIRS:
        for name in FONT_FILES[bold]:
            path = Path(folder) / name
            if path.exists():
                return str(path)
    return None


def available() -> bool:
    return _font_path(False) is not None


@lru_cache(maxsize=64)
def _font(bold: bool, px: int) -> ImageFont.FreeTypeFont:
    path = _font_path(bold) or _font_path(False)
    return ImageFont.truetype(path, max(px, 6))


@lru_cache(maxsize=1)
def _symbol_font_path() -> str | None:
    for folder in FONT_DIRS:
        for name in SYMBOL_FONT_FILES:
            path = Path(folder) / name
            if path.exists():
                return str(path)
    return None


def _font_for(word: str, bold: bool, px: int) -> ImageFont.FreeTypeFont:
    """В Calibri/Carlito нет ★ — такие слова рисуем запасным шрифтом, как это делает PowerPoint."""
    symbol_path = _symbol_font_path()
    if symbol_path and SYMBOLS.intersection(word):
        return _cached_truetype(symbol_path, max(px, 6))
    return _font(bold, px)


@lru_cache(maxsize=16)
def _cached_truetype(path: str, px: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, px)


def render(template: Template, spec: SlideSpec, dark_logo: bool = False, width: int = 1280) -> bytes:
    """PNG одного слайда."""
    scale = width / template.width
    height = round(template.height * scale)
    source = template.question if spec.source == "question" else template.answer

    bg, default_text = _template_colors(template, source.path)
    img = Image.new("RGB", (width, height), _rgb(spec.bg) if spec.bg else bg)

    for blob, box in _static_pictures(template, source.path):
        if dark_logo and template.standard and _is_logo(blob, template):
            blob = LOGO_DARK_PATH.read_bytes()
        _paste(img, blob, box, scale)

    draw = ImageDraw.Draw(img)
    text_color = _rgb(spec.text_color) if spec.text_color else default_text

    if spec.label and source.label:
        size_pt, bold, align = _label_style(template, source)
        _draw_paragraphs(draw, [([(spec.label, bold, None)], size_pt, align)], source.label, scale, "t", text_color)

    paras = [([(r.text, r.bold, r.color) for r in p.runs], p.size, p.align) for p in spec.paras]
    _draw_paragraphs(draw, paras, spec.body, scale, spec.anchor, text_color)

    for pic, box in spec.pictures:
        _paste(img, pic.blob, box, scale)

    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


# ─── Текст ───────────────────────────────────────────────────────────────────

def _draw_paragraphs(draw, paras, box, scale: float, anchor: str, default_color) -> None:
    left = (box.x + INSET_X) * scale
    top = (box.y + INSET_Y) * scale
    max_w = (box.w - 2 * INSET_X) * scale
    max_h = (box.h - 2 * INSET_Y) * scale

    laid_out = []  # (строки, px, выравнивание); строка — [(слово, жирный, цвет, ширина)]
    total_h = 0.0
    for runs, size_pt, align in paras:
        px = size_pt * EMU_PER_PT * scale
        lines = _wrap(runs, px, max_w)
        laid_out.append((lines, px, align))
        total_h += len(lines) * px * LINE_HEIGHT

    y = top + max(0.0, (max_h - total_h) / 2) if anchor == "ctr" else top
    for lines, px, align in laid_out:
        space = _font(False, round(px)).getlength(" ")
        for i, line in enumerate(lines):
            words_w = sum(w for *_, w in line)
            gaps = max(len(line) - 1, 1)
            gap = space
            x = left
            if align == "ctr":
                x = left + (max_w - words_w - space * (len(line) - 1)) / 2
            elif align == "just" and i < len(lines) - 1 and len(line) > 1:
                gap = (max_w - words_w) / gaps
            for word, bold, color, w in line:
                fill = _rgb(color) if color else default_color
                draw.text((x, y), word, font=_font_for(word, bold, round(px)), fill=fill)
                x += w + gap
            y += px * LINE_HEIGHT


def _wrap(runs, px: float, max_w: float) -> list[list[tuple]]:
    """Перенос по словам с учётом жирности каждого куска."""
    words = []
    for text, bold, color in runs:
        for word in text.split():
            words.append((word, bold, color, _font_for(word, bold, round(px)).getlength(word)))
    if not words:
        return [[]]
    space = _font(False, round(px)).getlength(" ")
    lines, current, current_w = [], [], 0.0
    for word in words:
        needed = word[3] if not current else current_w + space + word[3]
        if current and needed > max_w:
            lines.append(current)
            current, current_w = [word], word[3]
        else:
            current.append(word)
            current_w = needed
    lines.append(current)
    return lines


def _label_style(template: Template, source) -> tuple[float, bool, str]:
    """Кегль, жирность и выравнивание метки — с наследованием из макета и мастера."""
    root = etree.fromstring(source.xml)
    sp = next(sp for sp in root.iter(qn(P, "sp"))
              if sp.find(f"{qn(P, 'nvSpPr')}/{qn(P, 'cNvPr')}").get("id") == source.label_id)
    size = bold = align = None
    r_pr = sp.find(f".//{qn(A, 'r')}/{qn(A, 'rPr')}")
    if r_pr is not None:
        size, bold = r_pr.get("sz"), r_pr.get("b")
    p_pr = sp.find(f".//{qn(A, 'p')}/{qn(A, 'pPr')}")
    if p_pr is not None:
        align = p_pr.get("algn")

    lvl1 = f"{qn(P, 'txBody')}/{qn(A, 'lstStyle')}/{qn(A, 'lvl1pPr')}"
    chain = placeholder_chain(template.files, source.path, sp)
    levels = [sp.find(lvl1)] + [candidate.find(lvl1) for _, candidate in chain]
    master = master_path(template.files, source.path)
    if master:
        ph = sp.find(f"{qn(P, 'nvSpPr')}/{qn(P, 'nvPr')}/{qn(P, 'ph')}")
        style = "otherStyle" if ph is None else "titleStyle" if _ph_kind(ph.get("type")) == "title" else "bodyStyle"
        levels.append(etree.fromstring(template.files[master]).find(
            f"{qn(P, 'txStyles')}/{qn(P, style)}/{qn(A, 'lvl1pPr')}"))

    for level in levels:
        if level is None:
            continue
        align = align or level.get("algn")
        defaults = level.find(qn(A, "defRPr"))
        if defaults is not None:
            size = size or defaults.get("sz")
            bold = bold if bold is not None else defaults.get("b")
    return (int(size) / 100 if size else 18), bold == "1", align or "l"


# ─── Картинки и цвета шаблона ────────────────────────────────────────────────

def _paste(img: Image.Image, blob: bytes, box, scale: float) -> None:
    try:
        pic = Image.open(io.BytesIO(blob)).convert("RGBA")
    except Exception:
        return
    w, h = max(1, round(box.w * scale)), max(1, round(box.h * scale))
    pic = pic.resize((w, h), Image.LANCZOS)
    img.paste(pic, (round(box.x * scale), round(box.y * scale)), pic)


def _static_pictures(template: Template, slide_path: str) -> list[tuple[bytes, object]]:
    """Картинки мастера, макета и самого слайда (логотипы и т.п.)."""
    chain = [slide_path]
    for rel_type in ("/slideLayout", "/slideMaster"):
        parent = next((t for typ, t in _rels_map(template.files, chain[-1]).values() if typ.endswith(rel_type)), None)
        if parent:
            chain.append(_resolve(chain[-1], parent))

    result = []
    for part in reversed(chain):
        rels = _rels_map(template.files, part)
        root = etree.fromstring(template.files[part])
        for pic in root.iter(qn(P, "pic")):
            blip = pic.find(f".//{qn(A, 'blip')}")
            off = pic.find(f"{qn(P, 'spPr')}/{qn(A, 'xfrm')}/{qn(A, 'off')}")
            ext = pic.find(f"{qn(P, 'spPr')}/{qn(A, 'xfrm')}/{qn(A, 'ext')}")
            rid = blip.get(qn(R_NS, "embed")) if blip is not None else None
            if not rid or off is None or ext is None or rid not in rels:
                continue
            blob = template.files.get(_resolve(part, rels[rid][1]))
            if blob:
                box = Box(int(off.get("x")), int(off.get("y")), int(ext.get("cx")), int(ext.get("cy")))
                result.append((blob, box))
    return result


def _is_logo(blob: bytes, template: Template) -> bool:
    return blob == template.files.get("ppt/media/image1.png")


def _template_colors(template: Template, slide_path: str) -> tuple[tuple, tuple]:
    """(фон, цвет текста) из слайда → макета → мастера; схемные цвета — через тему."""
    theme = _theme_colors(template)
    bg = None
    part = slide_path
    for rel_type in (None, "/slideLayout", "/slideMaster"):
        if rel_type:
            parent = next((t for typ, t in _rels_map(template.files, part).values() if typ.endswith(rel_type)), None)
            if not parent:
                break
            part = _resolve(part, parent)
        root = etree.fromstring(template.files[part])
        fill = root.find(f"{qn(P, 'cSld')}/{qn(P, 'bg')}/{qn(P, 'bgPr')}/{qn(A, 'solidFill')}")
        ref = root.find(f"{qn(P, 'cSld')}/{qn(P, 'bg')}/{qn(P, 'bgRef')}")
        color = _color_of(fill, theme) if fill is not None else _color_of(ref, theme) if ref is not None else None
        if color:
            bg = color
            break
    return bg or (255, 255, 255), theme.get("dk1", (0, 0, 0))


def _theme_colors(template: Template) -> dict[str, tuple]:
    colors = {}
    theme_path = next((n for n in template.files if n.startswith("ppt/theme/theme") and n.endswith(".xml")), None)
    if not theme_path:
        return colors
    scheme = etree.fromstring(template.files[theme_path]).find(f".//{qn(A, 'clrScheme')}")
    if scheme is None:
        return colors
    for el in scheme:
        name = etree.QName(el).localname
        srgb, sys_clr = el.find(qn(A, "srgbClr")), el.find(qn(A, "sysClr"))
        value = srgb.get("val") if srgb is not None else sys_clr.get("lastClr") if sys_clr is not None else None
        if value:
            colors[name] = _rgb(value)
    aliases = {"bg1": "lt1", "tx1": "dk1", "bg2": "lt2", "tx2": "dk2"}
    for alias, real in aliases.items():
        if real in colors:
            colors[alias] = colors[real]
    return colors


def _color_of(el, theme: dict) -> tuple | None:
    srgb = el.find(qn(A, "srgbClr"))
    if srgb is not None:
        return _rgb(srgb.get("val"))
    scheme = el.find(qn(A, "schemeClr"))
    if scheme is not None:
        return theme.get(scheme.get("val"))
    return None


def _rgb(hex_color: str) -> tuple:
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
