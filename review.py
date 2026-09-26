"""
Проверка пакета перед игрой.

Локально (мгновенно, без интернета): повторы внутри пакета и с прошлыми
пакетами из истории, подозрительно длинные ответы и вопросы.
Через ИИ (Groq): опечатки, ответы, не подходящие к вопросу, фактические
неточности. ИИ только указывает на проблемы — текст он не меняет.
"""

import difflib
import json
import logging
import re

import httpx

from config import GROQ_API_KEY, GROQ_BASE_URL, GROQ_MODEL

logger = logging.getLogger(__name__)

AI_CHUNK = 25          # вопросов в одном запросе к ИИ
AI_MAX_CHUNKS = 4      # не больше 100 вопросов за проверку
LONG_QUESTION = 900    # символов — на слайде будет мелкий шрифт
LONG_ANSWER = 120


def _norm(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _similar(a: str, b: str) -> float:
    """Похожесть текстов 0..1. Сначала дешёвое сравнение наборов слов — точное только для похожих."""
    a, b = _norm(a), _norm(b)
    words_a, words_b = set(a.split()), set(b.split())
    if not words_a or not words_b:
        return 0.0
    if len(words_a & words_b) / len(words_a | words_b) < 0.4:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def local_issues(questions: list[dict], label, history: list[dict]) -> list[str]:
    """label(q) → «Вопрос 5». history — прошлые пакеты [{name, questions}]."""
    issues = []

    for i, a in enumerate(questions):
        for b in questions[i + 1:]:
            if a["question"] and _similar(a["question"], b["question"]) > 0.85:
                issues.append(f"🔁 {label(a)} и {label(b)} почти одинаковые")
            elif _norm(a["answer"]) == _norm(b["answer"]) and a["answer"]:
                issues.append(f"🔁 Одинаковый ответ «{a['answer']}» у {label(a)} и {label(b)}")

    past = {}
    for doc in history:
        for q in doc["questions"]:
            past.setdefault(_norm(q["answer"]), []).append((doc["name"], q))
    for q in questions:
        for name, old in past.get(_norm(q["answer"]), []):
            if _similar(q["question"], old["question"]) > 0.6:
                issues.append(f"🕘 {label(q)} уже был в пакете «{name}»")
                break

    for q in questions:
        if len(q["question"]) > LONG_QUESTION:
            issues.append(f"📏 {label(q)} очень длинный ({len(q['question'])} символов) — на слайде будет мелкий шрифт")
        if len(q["answer"]) > LONG_ANSWER:
            issues.append(f"❓ Ответ к {label(q).lower()} подозрительно длинный — может, туда попал комментарий?")
    return issues


def ai_issues(questions: list[dict], label) -> list[str] | None:
    """Замечания ИИ. None — ИИ недоступен. Блокирующая — вызывать через to_thread."""
    if not GROQ_API_KEY:
        return None
    issues = []
    chunks = [questions[i:i + AI_CHUNK] for i in range(0, len(questions), AI_CHUNK)][:AI_MAX_CHUNKS]
    for chunk in chunks:
        result = _ask(chunk, label)
        if result is None:
            return issues or None
        issues += result
    return issues


def _ask(questions: list[dict], label) -> list[str] | None:
    lines = []
    for q in questions:
        line = f"[{label(q)}] Вопрос: {q['question']} | Ответ: {q['answer']}"
        if q.get("accept"):
            line += f" | Зачёт: {q['accept']}"
        if q.get("comment"):
            line += f" | Комментарий: {q['comment']}"
        lines.append(line.replace("\n", " "))

    prompt = (
        "Ты редактор интеллектуальной викторины Brain-Do. Проверь вопросы ниже и найди ТОЛЬКО реальные проблемы:\n"
        "- опечатки и ошибки в тексте;\n"
        "- ответ не подходит к вопросу или противоречит комментарию;\n"
        "- явные фактические ошибки;\n"
        "- вопрос допускает несколько равноправных ответов, не указанных в зачёте.\n"
        "Не придирайся к стилю. Если проблем нет — верни [].\n"
        'Верни СТРОГО JSON без пояснений: [{"q": "Вопрос 5", "problem": "кратко, по-русски"}]\n\n'
        + "\n".join(lines)
    )
    try:
        response = httpx.post(
            GROQ_BASE_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 1500,
            },
            timeout=60,
        )
        response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"]
        raw = re.sub(r"```json|```", "", raw).strip()
        items = json.loads(raw[raw.find("["): raw.rfind("]") + 1] or "[]")
    except Exception as e:
        logger.warning("ИИ-проверка не удалась: %s", e)
        return None
    return [f"🤖 {it.get('q', '')}: {it.get('problem', '')}".strip() for it in items
            if isinstance(it, dict) and it.get("problem")]
