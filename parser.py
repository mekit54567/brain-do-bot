"""
Гибкий парсер Word-файлов с вопросами/ответами.
Поддерживает разные форматы — жирные заголовки, обычный текст, с комментарием и без.
"""

import re
from docx import Document


def parse_questions(path: str) -> list[dict]:
    """
    Возвращает список словарей:
    {
        "number": int,
        "question": str,
        "answer": str,
        "comment": str | None,  # может отсутствовать
        "hard": bool,           # True если вопрос начинается со *
    }
    """
    doc = Document(path)
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]

    # Склеиваем весь текст для гибкого парсинга
    full_text = "\n".join(paragraphs)

    questions = []

    # Пробуем найти блоки через регулярки — ищем "Вопрос" и "Ответ"
    # Паттерн: ищем блок от одного "Вопрос" до следующего
    pattern = re.compile(
        r"Вопрос\s*[\d№:.\-–—]*\s*:?\s*(.+?)"  # вопрос
        r"Ответ\s*:?\s*(.+?)"                    # ответ
        r"(?:(?:Комментарий|Зачёт)\s*:?\s*(.+?))?"  # комментарий (опционально)
        r"(?=Вопрос\s*[\d№]|\Z)",                # до следующего вопроса или конца
        re.DOTALL | re.IGNORECASE
    )

    matches = list(pattern.finditer(full_text))

    for i, m in enumerate(matches):
        q_text = clean(m.group(1))
        a_text = clean(m.group(2))
        c_text = clean(m.group(3)) if m.group(3) else None

        if not q_text or not a_text:
            continue

        # Убираем лишнее что могло попасть в ответ (до следующего ключевого слова)
        a_text = split_at_keywords(a_text)
        if c_text:
            c_text = split_at_keywords(c_text)

        hard = q_text.startswith("*")
        if hard:
            q_text = q_text.lstrip("* ").strip()

        questions.append({
            "number": i + 1,
            "question": q_text,
            "answer": a_text,
            "comment": c_text,
            "hard": hard,
        })

    return questions


def clean(text: str) -> str:
    """Убирает лишние пробелы и переносы."""
    if not text:
        return ""
    # Убираем маркеры ссылок типа [Вопрос 1](https://...)
    text = re.sub(r"\[([^\]]+)\]\(https?://[^\)]+\)", r"\1", text)
    # Убираем отдельные ссылки
    text = re.sub(r"https?://\S+", "", text)
    # Убираем лишние пробелы
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def split_at_keywords(text: str) -> str:
    """Обрезаем текст если встречаем следующий ключевой блок."""
    for kw in ["Зачёт:", "Зачет:", "Комментарий:", "Источник:", "Автор:"]:
        idx = text.find(kw)
        if idx > 0:
            text = text[:idx].strip()
    return text
