"""
Генератор презентаций Brain-Do.
Использует XML подход: копирует слайды-шаблоны и редактирует текст.
"""

import copy
import os
import re
import shutil
import subprocess
import tempfile
from lxml import etree

TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "template.pptx")
SCRIPTS = os.path.dirname(__file__)  # скрипты лежат рядом с generator.py

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"

def qn(ns, tag):
    return f"{{{ns}}}{tag}"

THEMES = {
    "white": {"bg": None,     "text": "000000", "accent": "CC0000"},
    "dark":  {"bg": "1E1E1E", "text": "FFFFFF", "accent": "FF4444"},
    "blue":  {"bg": "1E3A5F", "text": "FFFFFF", "accent": "4FC3F7"},
    "red":   {"bg": "8B0000", "text": "FFFFFF", "accent": "FFD700"},
}


def generate_presentation(questions: list, output_path: str, settings: dict):
    theme = THEMES.get(settings.get("theme", "white"), THEMES["white"])
    show_timer = settings.get("timer")
    show_numbering = settings.get("numbering", True)

    with tempfile.TemporaryDirectory() as tmpdir:
        unpacked = os.path.join(tmpdir, "unpacked")

        result = subprocess.run(
            ["python3", f"{SCRIPTS}/unpack.py", TEMPLATE_PATH, unpacked],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"Unpack failed: {result.stderr}")

        prs_xml_path = os.path.join(unpacked, "ppt", "presentation.xml")
        tree = etree.parse(prs_xml_path)
        root = tree.getroot()
        sldIdLst = root.find(f".//{{{P}}}sldIdLst")

        rels_path = os.path.join(unpacked, "ppt", "_rels", "presentation.xml.rels")
        rels_tree = etree.parse(rels_path)
        rels_root = rels_tree.getroot()

        def get_target(rId):
            for rel in rels_root:
                if rel.get("Id") == rId:
                    return rel.get("Target")
            return None

        slide_ids = list(sldIdLst)
        q_rId = slide_ids[0].get(qn(R_NS, "id"))
        a_rId = slide_ids[1].get(qn(R_NS, "id"))

        q_target = get_target(q_rId)  # e.g. "slides/slide1.xml"
        a_target = get_target(a_rId)

        slides_dir = os.path.join(unpacked, "ppt", "slides")
        q_slide_path = os.path.join(unpacked, "ppt", q_target)
        a_slide_path = os.path.join(unpacked, "ppt", a_target)

        q_xml_root = copy.deepcopy(etree.parse(q_slide_path).getroot())
        a_xml_root = copy.deepcopy(etree.parse(a_slide_path).getroot())

        # Удаляем старые слайды из sldIdLst и rels
        for sid in list(sldIdLst):
            sldIdLst.remove(sid)
        for rel in list(rels_root):
            t = rel.get("Target", "")
            if "slides/slide" in t and "Layout" not in t and "Master" not in t:
                rels_root.remove(rel)

        max_rId = max(
            (int(rel.get("Id", "rId0").replace("rId", "") or 0) for rel in rels_root),
            default=2
        )

        new_slide_num = 1
        new_rId_num = max_rId + 1
        new_slide_id = 300

        for q in questions:
            for is_answer in [False, True]:
                src_xml_root = a_xml_root if is_answer else q_xml_root
                src_target = a_target if is_answer else q_target

                # Записываем копию файла
                fname = f"slide{new_slide_num}.xml"
                fpath = os.path.join(slides_dir, fname)

                # Делаем глубокую копию через сериализацию
                src_bytes = etree.tostring(src_xml_root, xml_declaration=True, encoding="utf-8")
                elem = etree.fromstring(src_bytes)

                if is_answer:
                    apply_answer(elem, q, show_numbering, theme)
                else:
                    apply_question(elem, q, show_numbering, show_timer, theme)

                etree.ElementTree(elem).write(
                    fpath, xml_declaration=True, encoding="utf-8", pretty_print=True
                )

                # Копируем .rels
                src_rels = os.path.join(
                    unpacked, "ppt", "slides", "_rels",
                    os.path.basename(src_target) + ".rels"
                )
                dst_rels = os.path.join(
                    unpacked, "ppt", "slides", "_rels", f"{fname}.rels"
                )
                if os.path.exists(src_rels) and src_rels != dst_rels:
                    shutil.copy2(src_rels, dst_rels)

                # sldId
                sld_elem = etree.SubElement(sldIdLst, qn(P, "sldId"))
                sld_elem.set("id", str(new_slide_id))
                sld_elem.set(qn(R_NS, "id"), f"rId{new_rId_num}")

                # rel
                rel = etree.SubElement(rels_root, "Relationship")
                rel.set("Id", f"rId{new_rId_num}")
                rel.set("Type", f"{R_NS}/slide")
                rel.set("Target", f"slides/{fname}")

                new_slide_num += 1
                new_rId_num += 1
                new_slide_id += 1

        tree.write(prs_xml_path, xml_declaration=True, encoding="utf-8", pretty_print=True)
        rels_tree.write(rels_path, xml_declaration=True, encoding="utf-8", pretty_print=True)
        update_content_types(unpacked, new_slide_num - 1)

        result = subprocess.run(
            ["python3", f"{SCRIPTS}/pack.py", unpacked, output_path,
             "--original", TEMPLATE_PATH, "--validate", "false"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"Pack failed: {result.stderr}\n{result.stdout}")


def update_content_types(unpacked, num_slides):
    ct_path = os.path.join(unpacked, "[Content_Types].xml")
    tree = etree.parse(ct_path)
    root = tree.getroot()
    CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"

    for elem in list(root):
        part = elem.get("PartName", "")
        if "/slides/slide" in part and "Layout" not in part and "Master" not in part:
            root.remove(elem)

    for i in range(1, num_slides + 1):
        override = etree.SubElement(root, f"{{{CT_NS}}}Override")
        override.set("PartName", f"/ppt/slides/slide{i}.xml")
        override.set("ContentType",
            "application/vnd.openxmlformats-officedocument.presentationml.slide+xml")

    tree.write(ct_path, xml_declaration=True, encoding="utf-8", pretty_print=True)


def auto_sz(text: str, base: int = 6000, min_sz: int = 2600) -> int:
    """Автоподбор размера шрифта по длине текста."""
    length = len(text)
    if length <= 60:
        return base              # ~60pt
    elif length <= 120:
        return int(base * 0.80) # ~48pt
    elif length <= 220:
        return int(base * 0.65) # ~39pt
    elif length <= 350:
        return int(base * 0.52) # ~31pt
    elif length <= 500:
        return int(base * 0.43) # ~26pt
    else:
        return max(min_sz, int(base * 0.37))  # ~22pt минимум

def find_shapes(root):
    """Возвращает все sp элементы с их шириной."""
    results = []
    for sp in root.findall(f".//{{{P}}}sp"):
        # txBody может быть в p: или a: namespace в зависимости от шаблона
        tf_p = sp.find(f"{{{P}}}txBody")
        tf = tf_p if tf_p is not None else sp.find(f".//{{{A}}}txBody")
        if tf is None:
            continue
        spPr = sp.find(f"{{{P}}}spPr")
        xfrm = spPr.find(f"{{{A}}}xfrm") if spPr is not None else None
        ext = xfrm.find(f"{{{A}}}ext") if xfrm is not None else None
        width = int(ext.get("cx", "99999999")) if ext is not None else 99999999
        all_text = "".join(r.text or "" for r in tf.iter(f"{{{A}}}t"))
        results.append((sp, tf, width, all_text))
    return results


def apply_question(root, q, show_numbering, timer, theme):
    for sp, tf, width, all_text in find_shapes(root):
        if width < 3_000_000:
            # Маленькое поле — метка "Вопрос N"
            label = f"Вопрос {q['number']}" if show_numbering else "Вопрос"
            set_single_text(tf, label, bold=True, sz=2400, color=theme["text"])
        else:
            # Большое поле — текст вопроса
            clear_tf(tf)
            sz = auto_sz(q["question"])
            add_paragraph(tf, q["question"], sz=sz, color=theme["text"])

            # Центрируем по вертикали и включаем нормальный autofit
            bodyPr = tf.find(f"{{{A}}}bodyPr")
            if bodyPr is not None:
                bodyPr.set("anchor", "ctr")
                # Заменяем noAutofit на normAutofit
                for child in list(bodyPr):
                    if "Autofit" in child.tag or "autofit" in child.tag.lower():
                        bodyPr.remove(child)
                etree.SubElement(bodyPr, f"{{{A}}}normAutofit")
            if timer:
                add_paragraph(tf, f"⏱ {timer} секунд", sz=2200,
                               italic=True, color=theme["accent"])
            if q.get("hard"):
                add_paragraph(tf, "★ Сложный вопрос", sz=2000,
                               bold=True, color="CC0000")
    apply_theme_bg(root, theme)


def apply_answer(root, q, show_numbering, theme):
    for sp, tf, width, all_text in find_shapes(root):
        if width < 3_000_000:
            # Маленькое поле — метка "Вопрос N"
            label = f"Вопрос {q['number']}" if show_numbering else "Вопрос"
            set_single_text(tf, label, bold=True, sz=2400, color=theme["text"])
        else:
            # Большое поле — ответ и комментарий
            clear_tf(tf)
            p = new_paragraph(tf, algn="l")
            p.append(make_run("Ответ: ", bold=True, sz=3600, color=theme["text"]))
            p.append(make_run(q["answer"], sz=3600, color=theme["text"]))

            if q.get("comment"):
                add_paragraph(tf, "", sz=1000)
                p2 = new_paragraph(tf, algn="l")
                p2.append(make_run("Комментарий: ", bold=True, sz=2600, color=theme["text"]))
                p2.append(make_run(q["comment"], sz=2600, color=theme["text"]))

    apply_theme_bg(root, theme)


def apply_theme_bg(root, theme):
    if not theme.get("bg"):
        return
    cSld = root.find(qn(P, "cSld"))
    if cSld is None:
        return
    bg = cSld.find(qn(P, "bg"))
    if bg is None:
        bg = etree.Element(qn(P, "bg"))
        cSld.insert(0, bg)
    bgPr = bg.find(qn(P, "bgPr"))
    if bgPr is None:
        bgPr = etree.SubElement(bg, qn(P, "bgPr"))
    for child in list(bgPr):
        bgPr.remove(child)
    solidFill = etree.SubElement(bgPr, qn(A, "solidFill"))
    srgbClr = etree.SubElement(solidFill, qn(A, "srgbClr"))
    srgbClr.set("val", theme["bg"])


# ─── XML хелперы ─────────────────────────────────────────────────────────────

def make_run(text, bold=False, italic=False, sz=3200, color=None):
    r = etree.Element(qn(A, "r"))
    rPr = etree.SubElement(r, qn(A, "rPr"))
    rPr.set("lang", "ru-RU")
    rPr.set("sz", str(sz))
    rPr.set("b", "1" if bold else "0")
    rPr.set("i", "1" if italic else "0")
    rPr.set("u", "none")
    rPr.set("strike", "noStrike")
    rPr.set("cap", "none")
    if color:
        sf = etree.SubElement(rPr, qn(A, "solidFill"))
        sc = etree.SubElement(sf, qn(A, "srgbClr"))
        sc.set("val", color)
    lat = etree.SubElement(rPr, qn(A, "latin"))
    lat.set("typeface", "Calibri")
    t = etree.SubElement(r, qn(A, "t"))
    t.text = text
    return r


def new_paragraph(tf, algn="l"):
    p = etree.SubElement(tf, qn(A, "p"))
    pPr = etree.SubElement(p, qn(A, "pPr"))
    pPr.set("algn", algn)
    lnSpc = etree.SubElement(pPr, qn(A, "lnSpc"))
    spc = etree.SubElement(lnSpc, qn(A, "spcPct"))
    spc.set("val", "90000")
    etree.SubElement(pPr, qn(A, "buNone"))
    return p


def add_paragraph(tf, text, sz=3200, bold=False, italic=False, color=None):
    p = new_paragraph(tf)
    if text:
        p.append(make_run(text, bold=bold, italic=italic, sz=sz, color=color))
    return p


def set_single_text(tf, text, bold=False, sz=2400, color=None):
    clear_tf(tf)
    p = new_paragraph(tf, algn="ctr")
    p.append(make_run(text, bold=bold, sz=sz, color=color))


def clear_tf(tf):
    """Удаляем все параграфы из txBody (поддерживает p: и a: namespace)."""
    for p in list(tf.findall(f"{{{A}}}p")):
        tf.remove(p)


def add_signature_para(tf):
    p = new_paragraph(tf, algn="r")
    p.append(make_run("powered by Nikita to папа ❤️",
                       italic=True, sz=1400, color="999999"))
