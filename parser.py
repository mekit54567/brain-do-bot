"""
Умный парсер Word-файлов с вопросами/ответами.
Читает параграф за параграфом, определяет тип каждого.
Поддерживает разные форматы Brain-Do файлов.
Если обычный парсер не справился — использует Llama 4 Scout через Groq.
"""

import re
import os
import json
import requests
from docx import Document


# Паттерны для определения типа параграфа
RE_QUESTION = re.compile(r'^Вопрос\s*(\d+)\s*[:\.]?\s*(.*)$', re.DOTALL | re.IGNORECASE)
RE_ANSWER   = re.compile(r'^Ответ\s*[:\.]?\s*(.+)$', re.DOTALL | re.IGNORECASE)
RE_COMMENT  = re.compile(r'^Комментарий\s*[:\.]?\s*(.+)$', re.DOTALL | re.IGNORECASE)
RE_ZACHET   = re.compile(r'^Зачёт\s*[:\.]?\s*(.+)$', re.DOTALL | re.IGNORECASE)

# Мусорные строки которые нужно пропускать
RE_GARBAGE = re.compile(
    r'^(Синхронный|Окский|Открытый|Чемпионат|Турнир|Тур\s*\d|'
    r'\d{4}-\d{2}-\d{2}|'
    r'www\.|http|'
    r'Давать|Зайти|Примечание|'
    r'Автор\s*[:\.]|Источник\s*[:\.])' ,
    re.IGNORECASE
)


def clean(text: str) -> str:
    text = text.replace('\xa0', ' ')
    text = re.sub(r'\n+', ' ', text)
    text = re.sub(r' {2,}', ' ', text)
    return text.strip()


def parse_questions(path: str) -> list[dict]:
    """
    1. Обычный парсер разбирает файл
    2. Groq Scout всегда проверяет и дополняет результат
    """
    questions = _parse_regular(path)
    
    # Scout всегда проверяет — вдруг нашёл больше или исправил
    ai_questions = _parse_with_llama(path, existing=questions)
    if ai_questions and len(ai_questions) >= len(questions):
        return ai_questions

    return questions


def _parse_regular(path: str) -> list[dict]:
    """Обычный парсер по ключевым словам."""
    doc = Document(path)
    questions = []
    current = None
    auto_number = 0

    for para in doc.paragraphs:
        text = clean(para.text)
        if not text or text == '...':
            continue
        if RE_GARBAGE.match(text):
            continue

        m_q = RE_QUESTION.match(text)
        if m_q:
            if current and current.get('answer'):
                questions.append(current)

            auto_number += 1
            q_number = int(m_q.group(1)) if m_q.group(1) else auto_number
            q_text = clean(m_q.group(2)) if m_q.group(2) else ''

            hard = q_text.startswith('*')
            if hard:
                q_text = q_text.lstrip('* ').strip()

            current = {
                'number': auto_number,
                'orig_number': q_number,
                'question': q_text,
                'answer': None,
                'comment': None,
                'hard': hard,
            }
            continue

        if current is None:
            continue

        m_a = RE_ANSWER.match(text)
        m_c = RE_COMMENT.match(text)
        m_z = RE_ZACHET.match(text)

        if m_a:
            if current['answer'] is None and current['question']:
                current['answer'] = clean(m_a.group(1))
        elif m_c:
            current['comment'] = clean(m_c.group(1))
        elif m_z:
            zachet = clean(m_z.group(1))
            if current['comment']:
                current['comment'] = zachet + ' | ' + current['comment']
            else:
                current['comment'] = zachet
        elif current['answer'] is None:
            if current['question']:
                current['question'] += ' ' + text
            else:
                current['question'] = text

    if current and current.get('answer'):
        questions.append(current)

    return questions


def _parse_with_llama(path: str, existing: list = None) -> list[dict]:
    """Проверка и дополнение через Llama 4 Scout (Groq)."""
    groq_key = os.environ.get("GROQ_API_KEY")
    if not groq_key:
        return []

    doc = Document(path)
    text = "\n".join(
        p.text.replace('\xa0', ' ').strip()
        for p in doc.paragraphs
        if p.text.strip()
    )[:8000]

    existing_json = json.dumps(
        [{"number": q["number"], "question": q["question"], "answer": q["answer"]} 
         for q in (existing or [])],
        ensure_ascii=False
    )

    prompt = f"""Ты помощник который извлекает вопросы и ответы из текста Brain-Do (интеллектуальная игра).

Уже найденные вопросы (могут быть неполными или содержать ошибки):
{existing_json}

Исходный текст:
{text}

Задача:
1. Проверь найденные вопросы — исправь если что-то не так
2. Добавь вопросы которые пропустили
3. Игнорируй служебные строки (названия турниров, даты, редакторские пометки)

Верни ТОЛЬКО валидный JSON массив без пояснений:
[
  {{"number": 1, "question": "текст вопроса", "answer": "текст ответа", "comment": "комментарий или null"}},
  ...
]"""

    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {groq_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "meta-llama/llama-4-scout-17b-16e-instruct",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 4000,
            },
            timeout=30
        )
        data = response.json()
        raw = data["choices"][0]["message"]["content"]
        raw = re.sub(r"```json|```", "", raw).strip()
        items = json.loads(raw)

        questions = []
        for i, item in enumerate(items):
            questions.append({
                "number": i + 1,
                "orig_number": item.get("number", i + 1),
                "question": str(item.get("question", "")),
                "answer": str(item.get("answer", "")),
                "comment": item.get("comment") or None,
                "hard": False,
            })
        return questions

    except Exception as e:
        print(f"Llama fallback error: {e}")
        return []
