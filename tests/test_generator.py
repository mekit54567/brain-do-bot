"""Тесты генератора: python -m pytest"""

import io
import zipfile

import pytest
from lxml import etree

from generator import generate_presentation, question_label
from parser import Picture

pptx = pytest.importorskip("pptx")


def question(n, text="Кто написал «Войну и мир»?", **extra):
    q = {"number": n, "orig_number": n + 10, "question": text, "answer": "Лев Толстой",
         "accept": None, "comment": None, "hard": False, "tour": None, "q_pictures": [], "a_pictures": []}
    q.update(extra)
    return q


def slide_texts(data):
    prs = pptx.Presentation(io.BytesIO(data))
    return [[sh.text_frame.text for sh in s.shapes if sh.has_text_frame] for s in prs.slides]


@pytest.mark.parametrize("theme", ["white", "dark"])
def test_two_slides_per_question(theme):
    data = generate_presentation([question(1), question(2, comment="Коммент", accept="Толстой")], {"theme": theme})
    texts = slide_texts(data)
    assert len(texts) == 4
    assert "Кто написал «Войну и мир»?" in texts[0]
    assert any(t.startswith("Ответ: Лев Толстой") and "Зачёт: Толстой" in t and "Комментарий: Коммент" in t
               for t in texts[3])


def test_package_has_no_orphan_slides_and_all_parts_typed():
    data = generate_presentation([question(1)], {"theme": "dark"})
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        ct = etree.fromstring(zf.read("[Content_Types].xml"))
    slides = {n for n in names if n.startswith("ppt/slides/slide")}
    assert slides == {"ppt/slides/slide1.xml", "ppt/slides/slide2.xml"}

    defaults = {el.get("Extension") for el in ct if el.tag.endswith("Default")}
    overrides = {el.get("PartName").lstrip("/") for el in ct if el.tag.endswith("Override")}
    for name in names:
        ext = name.rsplit(".", 1)[-1]
        assert ext in defaults or name in overrides, f"нет content type для {name}"
    assert "ppt/media/logo_dark.png" in names


def test_pictures_go_to_media_once(png_bytes):
    pic = Picture(png_bytes, "png", "image/png", 4, 3)
    data = generate_presentation(
        [question(1, q_pictures=[pic]), question(2, text="", q_pictures=[pic], a_pictures=[pic])], {}
    )
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        media = [n for n in zf.namelist() if n.startswith("ppt/media/bd_img")]
    assert media == ["ppt/media/bd_img_1.png"]
    prs = pptx.Presentation(io.BytesIO(data))
    pictures = [sum(1 for sh in s.shapes if sh.shape_type == 13) for s in prs.slides]
    assert pictures == [2, 1, 2, 2]  # логотип + картинка вопроса


def test_long_text_gets_smaller_font():
    short = generate_presentation([question(1)], {})
    long = generate_presentation([question(1, text="Очень длинный вопрос. " * 60)], {})

    def body_size(data):
        prs = pptx.Presentation(io.BytesIO(data))
        body = max((sh for sh in prs.slides[0].shapes if sh.has_text_frame), key=lambda sh: sh.width)
        return body.text_frame.paragraphs[0].runs[0].font.size.pt

    assert body_size(long) < body_size(short) == 44


def test_labels():
    q = question(3)
    assert question_label(q, "seq") == "Вопрос 3"
    assert question_label(q, "orig") == "Вопрос 13"
    assert question_label(q, "none") == "Вопрос"
