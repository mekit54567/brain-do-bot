"""
Генератор презентаций Brain-Do.

Берёт из template.pptx слайд-шаблон вопроса (1-й) и ответа (2-й), размножает
их прямо в памяти и собирает новый .pptx — без внешних скриптов, распаковки
на диск и «мёртвых» слайдов шаблона внутри файла.
"""

import hashlib
import io
import math
import re
import zipfile
from dataclasses import dataclass
from functools import lru_cache

from lxml import etree

from config import BASE_DIR

TEMPLATE_PATH = BASE_DIR / "template.pptx"
LOGO_DARK_PATH = BASE_DIR / "assets" / "logo_dark.png"

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"

SLIDE_CT = "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"
IMAGE_CT = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "bmp": "image/bmp"}
RE_SLIDE_PART = re.compile(r"^ppt/slides/(_rels/)?slide\d+\.xml(\.rels)?$")

EMU_PER_PT = 12700
LABEL_MAX_WIDTH = 3_000_000   # поля уже этого — метка «Вопрос N»
CHAR_WIDTH = 0.5              # средняя ширина символа Calibri в долях кегля (замер в PowerPoint)
LINE_HEIGHT = 1.1             # высота строки при интервале 90% (замер: 1.08) + запас
FRAME_PADDING_PT = 12         # внутренние поля рамки по вертикали
PICTURE_GAP = 250_000         # отступ между текстом и картинкой, EMU


def qn(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


THEMES = {
    "white": {"label": "⬜ Белая", "bg": None, "text": "000000", "accent": "CC0000", "dark_logo": False},
    "dark": {"label": "⬛ Тёмная", "bg": "1E1E1E", "text": "FFFFFF", "accent": "FF5A4E", "dark_logo": True},
}


# ─── Шаблон (читается один раз) ──────────────────────────────────────────────

@dataclass(frozen=True)
class _Template:
    files: dict
    q_slide: bytes
    q_rels: bytes
    a_slide: bytes
    a_rels: bytes


@lru_cache(maxsize=1)
def _template() -> _Template:
    with zipfile.ZipFile(TEMPLATE_PATH) as zf:
        files = {name: zf.read(name) for name in zf.namelist()}

    prs = etree.fromstring(files["ppt/presentation.xml"])
    rels = etree.fromstring(files["ppt/_rels/presentation.xml.rels"])
    targets = {rel.get("Id"): rel.get("Target") for rel in rels}
    slide_ids = prs.find(qn(P, "sldIdLst"))
    q_target, a_target = (targets[s.get(qn(R_NS, "id"))] for s in list(slide_ids)[:2])

    def part(target: str) -> tuple[bytes, bytes]:
        name = target.rsplit("/", 1)[-1]
        return files[f"ppt/slides/{name}"], files[f"ppt/slides/_rels/{name}.rels"]

    q_slide, q_rels = part(q_target)
    a_slide, a_rels = part(a_target)
    return _Template(files, q_slide, q_rels, a_slide, a_rels)


# ─── Публичный API ───────────────────────────────────────────────────────────

def generate_presentation(questions: list[dict], settings: dict) -> bytes:
    """Собирает .pptx и возвращает его содержимое."""
    theme = THEMES.get(settings.get("theme"), THEMES["white"])
    numbering = settings.get("numbering", "seq")
    tpl = _template()

    files = {name: data for name, data in tpl.files.items() if not RE_SLIDE_PART.match(name)}
    media = _Media(files)
    if theme["dark_logo"]:
        files["ppt/media/logo_dark.png"] = LOGO_DARK_PATH.read_bytes()

    slides: list[tuple[bytes, bytes]] = []
    for q in questions:
        label = question_label(q, numbering)
        slides.append(_build_slide(tpl.q_slide, tpl.q_rels, q, label, theme, media, answer=False))
        slides.append(_build_slide(tpl.a_slide, tpl.a_rels, q, label, theme, media, answer=True))

    for i, (slide_xml, rels_xml) in enumerate(slides, 1):
        files[f"ppt/slides/slide{i}.xml"] = slide_xml
        files[f"ppt/slides/_rels/slide{i}.xml.rels"] = rels_xml

    _update_presentation(files, len(slides))
    _update_content_types(files, len(slides), media.extensions)
    _update_app_props(files, len(slides))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # [Content_Types].xml по традиции первым
        zf.writestr("[Content_Types].xml", files.pop("[Content_Types].xml"))
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def question_label(q: dict, numbering: str) -> str:
    if numbering == "none":
        return "Вопрос"
    if numbering == "orig":
        return f"Вопрос {q.get('orig_number') or q['number']}"
    return f"Вопрос {q['number']}"


# ─── Слайды ──────────────────────────────────────────────────────────────────

class _Media:
    """Картинки из вопросов: складывает в ppt/media без дублей."""

    def __init__(self, files: dict) -> None:
        self.files = files
        self.by_hash: dict[str, str] = {}
        self.extensions: set[str] = set()

    def add(self, blob: bytes, ext: str) -> str:
        ext = "jpg" if ext == "jpeg" else ext
        digest = hashlib.sha1(blob).hexdigest()[:16]
        if digest not in self.by_hash:
            name = f"bd_img_{len(self.by_hash) + 1}.{ext}"
            self.files[f"ppt/media/{name}"] = blob
            self.by_hash[digest] = name
            self.extensions.add(ext)
        return self.by_hash[digest]


def _build_slide(slide_xml: bytes, rels_xml: bytes, q: dict, label: str, theme: dict,
                 media: _Media, answer: bool) -> tuple[bytes, bytes]:
    root = etree.fromstring(slide_xml)
    rels = etree.fromstring(rels_xml)
    pictures = q.get("a_pictures" if answer else "q_pictures") or []

    for sp, tf in _text_shapes(root):
        xfrm = sp.find(f"{qn(P, 'spPr')}/{qn(A, 'xfrm')}")
        off, ext = xfrm.find(qn(A, "off")), xfrm.find(qn(A, "ext"))
        width = int(ext.get("cx"))

        if width < LABEL_MAX_WIDTH:
            _set_label(tf, label, theme)
            continue

        box = [int(off.get("x")), int(off.get("y")), width, int(ext.get("cy"))]
        has_text = bool(q["answer"] if answer else q["question"])
        if pictures:
            box = _place_pictures(root, rels, pictures, box, media, text_too=has_text)
            off.set("x", str(box[0]))
            off.set("y", str(box[1]))
            ext.set("cx", str(box[2]))
            ext.set("cy", str(box[3]))

        if answer:
            _fill_body(tf, _answer_blocks(q), box, theme, max_pt=40, align="l")
        else:
            # По ширине — как в шаблоне; в узкой колонке рядом с картинкой — по левому краю
            align = "l" if pictures else "just"
            _fill_body(tf, _question_blocks(q), box, theme, max_pt=44, align=align)

    _apply_theme(root, rels, theme)
    return _xml(root), _xml(rels)


def _question_blocks(q: dict) -> list[tuple]:
    """(части [(текст, жирный)], относительный кегль, цвет|None) для каждого абзаца."""
    blocks = [([(line, False)], 1.0, None) for line in q["question"].split("\n") if line]
    if q.get("hard"):
        blocks.append(([("★ Сложный вопрос", True)], 0.5, "accent"))
    return blocks


def _answer_blocks(q: dict) -> list[tuple]:
    blocks = [([("Ответ: ", True), (q["answer"], False)], 1.0, None)]
    if q.get("accept"):
        blocks.append(([("Зачёт: ", True), (q["accept"], False)], 0.8, None))
    if q.get("comment"):
        blocks.append(([], 0.3, None))  # отбивка
        lines = q["comment"].split("\n")
        blocks.append(([("Комментарий: ", True), (lines[0], False)], 0.72, None))
        blocks += [([(line, False)], 0.72, None) for line in lines[1:] if line]
    return blocks


def _fill_body(tf, blocks: list[tuple], box: list[int], theme: dict, max_pt: int, align: str) -> None:
    _clear(tf)
    size = _fit_size(blocks, box, max_pt=max_pt, min_pt=16)
    for parts, rel, color in blocks:
        p = _new_paragraph(tf, algn=align)
        pt = size * rel if color != "accent" else max(size * rel, 18)  # пометка всегда читаемая
        sz = max(1000, int(round(pt)) * 100)
        text_color = theme["accent"] if color == "accent" else theme["text"]
        for text, bold in parts:
            p.append(_make_run(text, bold=bold, sz=sz, color=text_color))
    if tf.find(qn(A, "p")) is None:
        _new_paragraph(tf)

    # Убираем отступы из lstStyle шаблона и включаем «сжать при переполнении»
    for lvl in tf.findall(f"{qn(A, 'lstStyle')}//{qn(A, 'pPr')}"):
        lvl.set("marL", "0")
        lvl.set("indent", "0")
    body_pr = tf.find(qn(A, "bodyPr"))
    if body_pr is not None:
        for child in list(body_pr):
            if child.tag.endswith("Autofit"):
                body_pr.remove(child)
        etree.SubElement(body_pr, qn(A, "normAutofit"))


def _fit_size(blocks: list[tuple], box: list[int], max_pt: int, min_pt: int) -> int:
    """Самый крупный кегль, при котором текст помещается в рамку."""
    width_pt = (box[2] - 2 * 91425) / EMU_PER_PT
    height_pt = box[3] / EMU_PER_PT - FRAME_PADDING_PT
    texts = [("".join(t for t, _ in parts), rel) for parts, rel, _ in blocks]

    for size in range(max_pt, min_pt - 1, -1):
        total = 0.0
        for text, rel in texts:
            sz = size * rel
            chars_per_line = max(1, int(width_pt / (sz * CHAR_WIDTH)))
            total += _wrapped_lines(text, chars_per_line) * sz * LINE_HEIGHT
        if total <= height_pt:
            return size
    return min_pt


def _wrapped_lines(text: str, chars_per_line: int) -> int:
    """Сколько строк займёт текст при переносе по словам."""
    if not text:
        return 1
    lines, current = 1, 0
    for word in text.split():
        length = len(word)
        if current and current + 1 + length > chars_per_line:
            lines += 1
            current = 0
        if length > chars_per_line:
            lines += math.ceil(length / chars_per_line) - 1
            current = length % chars_per_line
        else:
            current += (1 if current else 0) + length
    return lines


def _place_pictures(root, rels, pictures: list, box: list[int], media: _Media, text_too: bool) -> list[int]:
    """Кладёт картинки справа от текста (или на всю область). Возвращает рамку текста."""
    x, y, w, h = box
    if text_too:
        text_w = int(w * 0.56)
        area = [x + text_w + PICTURE_GAP, y, w - text_w - PICTURE_GAP, h]
        box = [x, y, text_w, h]
    else:
        # Вопрос-картинка: картинка на всю область, под ней узкая полоса для пометок
        pic_h = int(h * 0.86)
        area = [x, y, w, pic_h]
        box = [x, y + pic_h, w, h - pic_h]

    sp_tree = root.find(f"{qn(P, 'cSld')}/{qn(P, 'spTree')}")
    shape_id = 1000
    cell_h = (area[3] - PICTURE_GAP * (len(pictures) - 1)) // len(pictures)
    existing = [int(r.get("Id", "rId0")[3:] or 0) for r in rels if r.get("Id", "").startswith("rId")]
    next_rid = max(existing, default=0) + 1

    for i, pic in enumerate(pictures):
        name = media.add(pic.blob, pic.ext)
        rid = f"rId{next_rid}"
        next_rid += 1
        rel = etree.SubElement(rels, qn(PKG_REL, "Relationship"))
        rel.set("Id", rid)
        rel.set("Type", f"{R_NS}/image")
        rel.set("Target", f"../media/{name}")

        cell_y = area[1] + i * (cell_h + PICTURE_GAP)
        scale = min(area[2] / max(pic.width, 1), cell_h / max(pic.height, 1))
        cx, cy = int(pic.width * scale), int(pic.height * scale)
        px = area[0] + (area[2] - cx) // 2
        py = cell_y + (cell_h - cy) // 2
        sp_tree.append(_picture_element(shape_id + i, rid, px, py, cx, cy))
    return box


def _picture_element(shape_id: int, rid: str, x: int, y: int, cx: int, cy: int):
    pic = etree.Element(qn(P, "pic"))
    nv = etree.SubElement(pic, qn(P, "nvPicPr"))
    c_nv = etree.SubElement(nv, qn(P, "cNvPr"))
    c_nv.set("id", str(shape_id))
    c_nv.set("name", f"Picture {shape_id}")
    c_nv_pic = etree.SubElement(nv, qn(P, "cNvPicPr"))
    etree.SubElement(c_nv_pic, qn(A, "picLocks")).set("noChangeAspect", "1")
    etree.SubElement(nv, qn(P, "nvPr"))

    fill = etree.SubElement(pic, qn(P, "blipFill"))
    etree.SubElement(fill, qn(A, "blip")).set(qn(R_NS, "embed"), rid)
    etree.SubElement(etree.SubElement(fill, qn(A, "stretch")), qn(A, "fillRect"))

    sp_pr = etree.SubElement(pic, qn(P, "spPr"))
    xfrm = etree.SubElement(sp_pr, qn(A, "xfrm"))
    off = etree.SubElement(xfrm, qn(A, "off"))
    off.set("x", str(x))
    off.set("y", str(y))
    ext = etree.SubElement(xfrm, qn(A, "ext"))
    ext.set("cx", str(cx))
    ext.set("cy", str(cy))
    geom = etree.SubElement(sp_pr, qn(A, "prstGeom"))
    geom.set("prst", "rect")
    etree.SubElement(geom, qn(A, "avLst"))
    return pic


def _apply_theme(root, rels, theme: dict) -> None:
    if theme["dark_logo"]:
        for rel in rels:
            if rel.get("Target", "").endswith("media/image1.png"):
                rel.set("Target", "../media/logo_dark.png")
    if not theme["bg"]:
        return
    c_sld = root.find(qn(P, "cSld"))
    bg = c_sld.find(qn(P, "bg"))
    if bg is not None:
        c_sld.remove(bg)
    bg = etree.Element(qn(P, "bg"))
    c_sld.insert(0, bg)
    bg_pr = etree.SubElement(bg, qn(P, "bgPr"))
    fill = etree.SubElement(bg_pr, qn(A, "solidFill"))
    etree.SubElement(fill, qn(A, "srgbClr")).set("val", theme["bg"])
    etree.SubElement(bg_pr, qn(A, "effectLst"))


# ─── Сборка пакета ───────────────────────────────────────────────────────────

def _update_presentation(files: dict, count: int) -> None:
    prs = etree.fromstring(files["ppt/presentation.xml"])
    rels = etree.fromstring(files["ppt/_rels/presentation.xml.rels"])

    for rel in list(rels):
        if rel.get("Type") == f"{R_NS}/slide":
            rels.remove(rel)
    used = [int(r.get("Id")[3:]) for r in rels if r.get("Id", "").startswith("rId") and r.get("Id")[3:].isdigit()]
    next_rid = max(used, default=0) + 1

    sld_id_lst = prs.find(qn(P, "sldIdLst"))
    for child in list(sld_id_lst):
        sld_id_lst.remove(child)

    for i in range(1, count + 1):
        rid = f"rId{next_rid + i - 1}"
        rel = etree.SubElement(rels, qn(PKG_REL, "Relationship"))
        rel.set("Id", rid)
        rel.set("Type", f"{R_NS}/slide")
        rel.set("Target", f"slides/slide{i}.xml")
        sld = etree.SubElement(sld_id_lst, qn(P, "sldId"))
        sld.set("id", str(255 + i))
        sld.set(qn(R_NS, "id"), rid)

    files["ppt/presentation.xml"] = _xml(prs)
    files["ppt/_rels/presentation.xml.rels"] = _xml(rels)


def _update_content_types(files: dict, count: int, image_exts: set[str]) -> None:
    root = etree.fromstring(files["[Content_Types].xml"])
    for el in list(root):
        if el.get("ContentType") == SLIDE_CT:
            root.remove(el)
    defaults = {el.get("Extension", "").lower() for el in root.findall(qn(CT_NS, "Default"))}
    for ext in sorted(image_exts | {"png"}):
        if ext not in defaults:
            d = etree.Element(qn(CT_NS, "Default"))
            d.set("Extension", ext)
            d.set("ContentType", IMAGE_CT[ext])
            root.insert(0, d)
    for i in range(1, count + 1):
        o = etree.SubElement(root, qn(CT_NS, "Override"))
        o.set("PartName", f"/ppt/slides/slide{i}.xml")
        o.set("ContentType", SLIDE_CT)
    files["[Content_Types].xml"] = _xml(root)


def _update_app_props(files: dict, count: int) -> None:
    app = files.get("docProps/app.xml")
    if app:
        files["docProps/app.xml"] = re.sub(rb"<Slides>\d+</Slides>", f"<Slides>{count}</Slides>".encode(), app)


def _xml(root) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


# ─── XML хелперы ─────────────────────────────────────────────────────────────

def _text_shapes(root):
    for sp in root.iter(qn(P, "sp")):
        tf = sp.find(qn(P, "txBody"))
        if tf is not None:
            yield sp, tf


def _set_label(tf, text: str, theme: dict) -> None:
    _clear(tf)
    p = _new_paragraph(tf, algn="ctr")
    p.append(_make_run(text, bold=True, sz=2400, color=theme["text"]))


def _make_run(text: str, bold: bool = False, sz: int = 3200, color: str | None = None):
    r = etree.Element(qn(A, "r"))
    r_pr = etree.SubElement(r, qn(A, "rPr"))
    r_pr.set("lang", "ru-RU")
    r_pr.set("sz", str(sz))
    r_pr.set("b", "1" if bold else "0")
    r_pr.set("i", "0")
    r_pr.set("u", "none")
    r_pr.set("strike", "noStrike")
    r_pr.set("cap", "none")
    if color:
        fill = etree.SubElement(r_pr, qn(A, "solidFill"))
        etree.SubElement(fill, qn(A, "srgbClr")).set("val", color)
    etree.SubElement(r_pr, qn(A, "latin")).set("typeface", "Calibri")
    etree.SubElement(r_pr, qn(A, "cs")).set("typeface", "Calibri")
    etree.SubElement(r, qn(A, "t")).text = text
    return r


def _new_paragraph(tf, algn: str = "just"):
    p = etree.SubElement(tf, qn(A, "p"))
    p_pr = etree.SubElement(p, qn(A, "pPr"))
    p_pr.set("marL", "0")
    p_pr.set("indent", "0")
    p_pr.set("algn", algn)
    ln_spc = etree.SubElement(p_pr, qn(A, "lnSpc"))
    etree.SubElement(ln_spc, qn(A, "spcPct")).set("val", "90000")
    # Отступы между абзацами обнуляем явно, иначе они наследуются из мастер-слайда
    for tag in ("spcBef", "spcAft"):
        etree.SubElement(etree.SubElement(p_pr, qn(A, tag)), qn(A, "spcPts")).set("val", "0")
    etree.SubElement(p_pr, qn(A, "buNone"))
    return p


def _clear(tf) -> None:
    for p in tf.findall(qn(A, "p")):
        tf.remove(p)
