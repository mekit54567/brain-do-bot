"""
Умный парсер Word-файлов с вопросами/ответами.

Логика:
1. Обычный парсер читает файл по ключевым словам
2. Scout проверяет — не пропустил ли парсер что-то
3. Scout возвращает только "якоря" (5 слов начала + 5 слов конца)
4. По якорям ищем полный текст в исходном файле — дословно
5. Текст никогда не проходит через модель — только координаты
"""

import re
import os
import json
import requests
import difflib
from docx import Document


# ─── Паттерны ────────────────────────────────────────────────────────────────
RE_QUESTION     = re.compile(r'^Вопрос\s*(\d+)\s*[:\.]?\s*(.*)$', re.DOTALL | re.IGNORECASE)
RE_QUESTION_NUM = re.compile(r'^(\d+)[\.:\s]+(.+)$', re.DOTALL)
RE_ANSWER       = re.compile(r'^Ответ\s*[:\.]?\s*(.+)$', re.DOTALL | re.IGNORECASE)
RE_COMMENT      = re.compile(r'^Комментарий\s*[:\.]?\s*(.+)$', re.DOTALL | re.IGNORECASE)
RE_ZACHET       = re.compile(r'^Зачёт\s*[:\.]?\s*(.+)$', re.DOTALL | re.IGNORECASE)

RE_GARBAGE = re.compile(
    r'^(Синхронный|Окский|Открытый|Чемпионат|Турнир|Тур\s*\d|'
    r'\d{4}-\d{2}-\d{2}|www\.|http|Давать|Зайти|Примечание|'
    r'Автор\s*[:\.]|Источник\s*[:\.]|'
    r'С\s+Н2О|С\s+новым|Редактор|Апелляция)',
    re.IGNORECASE
)

ANCHOR_WORDS = 5  # сколько слов берём с начала и конца для якоря


# ─── Основная функция ────────────────────────────────────────────────────────
def parse_questions(path: str) -> list[dict]:
    """
    Парсит вопросы из docx файла.
    Обычный парсер + Scout для проверки пропущенных (с якорным поиском).
    """
    # Шаг 1: обычный парсер
    questions = _parse_regular(path)

    # Шаг 2: Scout проверяет через якоря
    extra = _scout_find_missing(path, questions)
    if extra:
        # Добавляем пропущенные вопросы и перенумеровываем
        questions.extend(extra)
        questions.sort(key=lambda q: q.get('orig_number', q['number']))
        for i, q in enumerate(questions):
            q['number'] = i + 1

    return questions


# ─── Обычный парсер ──────────────────────────────────────────────────────────
def _parse_regular(path: str) -> list[dict]:
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

        m_q  = RE_QUESTION.match(text)
        m_qn = RE_QUESTION_NUM.match(text) if not m_q else None

        if m_q or m_qn:
            if current and current.get('answer'):
                questions.append(current)
            auto_number += 1
            if m_q:
                q_number = int(m_q.group(1)) if m_q.group(1) else auto_number
                q_text   = clean(m_q.group(2)) if m_q.group(2) else ''
            else:
                q_number = int(m_qn.group(1))
                q_text   = clean(m_qn.group(2))

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
            current['comment'] = zachet + (' | ' + current['comment'] if current['comment'] else '')
        elif current['answer'] is None:
            current['question'] += (' ' + text) if current['question'] else text

    if current and current.get('answer'):
        questions.append(current)

    return questions


# ─── Scout с якорным поиском ─────────────────────────────────────────────────
def _scout_find_missing(path: str, existing: list) -> list[dict]:
    """
    Scout возвращает якоря пропущенных вопросов.
    Мы ищем их в исходном файле и берём дословно.
    """
    groq_key = os.environ.get("GROQ_API_KEY")
    if not groq_key:
        return []

    # Читаем все параграфы из файла
    doc = Document(path)
    paragraphs = [clean(p.text) for p in doc.paragraphs if p.text.strip()]
    full_text = "\n".join(paragraphs)[:8000]

    # Показываем Scout только номера и короткие якоря найденных вопросов
    existing_summary = json.dumps(
        [{"n": q["number"], "q_start": _words(q["question"], ANCHOR_WORDS, "start")}
         for q in existing],
        ensure_ascii=False
    )

    prompt = f"""Ты проверяешь полноту списка вопросов Brain-Do.

УЖЕ НАЙДЕННЫЕ вопросы (начало каждого):
{existing_summary}

ПОЛНЫЙ ТЕКСТ ДОКУМЕНТА:
{full_text}

ЗАДАЧА: найди вопросы которых НЕТ в списке выше.

Для каждого ПРОПУЩЕННОГО вопроса верни якоря — первые {ANCHOR_WORDS} и последние {ANCHOR_WORDS} слов вопроса и ответа.
Если пропущенных нет — верни пустой массив [].

СТРОГО верни ТОЛЬКО JSON без пояснений:
[
  {{
    "q_start": "первые {ANCHOR_WORDS} слов вопроса",
    "q_end": "последние {ANCHOR_WORDS} слов вопроса",
    "a_start": "первые {ANCHOR_WORDS} слов ответа",
    "a_end": "последние {ANCHOR_WORDS} слов ответа",
    "comment_start": "первые слова комментария или null"
  }}
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
                "temperature": 0.0,
                "max_tokens": 2000,
            },
            timeout=30
        )
        data = response.json()
        raw = data["choices"][0]["message"]["content"]
        raw = re.sub(r"```json|```", "", raw).strip()
        anchors = json.loads(raw)

        if not anchors:
            return []

        # По каждому якорю ищем полный текст в файле
        extra = []
        auto_number = len(existing) + 1

        for anchor in anchors:
            q_text = _find_by_anchors(
                paragraphs,
                anchor.get("q_start", ""),
                anchor.get("q_end", "")
            )
            a_text = _find_by_anchors(
                paragraphs,
                anchor.get("a_start", ""),
                anchor.get("a_end", "")
            )

            if q_text and a_text:
                extra.append({
                    "number": auto_number,
                    "orig_number": auto_number,
                    "question": q_text,
                    "answer": a_text,
                    "comment": None,
                    "hard": False,
                })
                auto_number += 1

        return extra

    except Exception as e:
        print(f"Scout anchor error: {e}")
        return []


# ─── Якорный поиск ───────────────────────────────────────────────────────────
def _find_by_anchors(paragraphs: list, start_anchor: str, end_anchor: str) -> str:
    """
    Ищет параграф(ы) в документе по якорям начала и конца.
    Возвращает полный дословный текст.
    """
    if not start_anchor:
        return ""

    start_words = _normalize(start_anchor)
    end_words   = _normalize(end_anchor)

    best_score = 0
    best_text  = ""

    # Собираем "окна" текста — одиночные параграфы и пары соседних
    candidates = []
    for i, p in enumerate(paragraphs):
        candidates.append(p)
        if i + 1 < len(paragraphs):
            candidates.append(p + " " + paragraphs[i + 1])

    for candidate in candidates:
        norm = _normalize(candidate)
        if not norm:
            continue

        # Считаем совпадение начала и конца
        score_start = difflib.SequenceMatcher(
            None, start_words[:40], norm[:40]
        ).ratio()

        score_end = difflib.SequenceMatcher(
            None, end_words[-40:], norm[-40:]
        ).ratio() if end_words else 0.5

        score = (score_start * 0.6) + (score_end * 0.4)

        if score > best_score and score > 0.4:
            best_score = score
            best_text  = candidate

    # Убираем служебные префиксы типа "Ответ:", "Комментарий:"
    best_text = re.sub(r'^(Ответ|Комментарий|Зачёт)\s*[:\.]?\s*', '', best_text, flags=re.IGNORECASE)

    return clean(best_text)


def _normalize(text: str) -> str:
    """Нормализуем для сравнения — нижний регистр, убираем лишнее."""
    text = text.lower().replace('\xa0', ' ')
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _words(text: str, n: int, side: str) -> str:
    """Берём n слов с начала или конца строки."""
    words = text.split()
    if side == "start":
        return " ".join(words[:n])
    else:
        return " ".join(words[-n:])


def clean(text: str) -> str:
    text = text.replace('\xa0', ' ')
    text = re.sub(r'\n+', ' ', text)
    text = re.sub(r' {2,}', ' ', text)
    return text.strip()
