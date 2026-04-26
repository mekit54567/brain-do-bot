"""
Умный парсер Word-файлов с вопросами/ответами.
Читает параграф за параграфом, определяет тип каждого.
Поддерживает разные форматы Brain-Do файлов.
"""

import re
from docx import Document


# Паттерны для определения типа параграфа
RE_QUESTION = re.compile(r'^Вопрос\s*(\d+)\s*[:\.]?\s*(.*)$', re.DOTALL | re.IGNORECASE)
RE_ANSWER   = re.compile(r'^Ответ\s*[:\.]?\s*(.+)$', re.DOTALL | re.IGNORECASE)
RE_COMMENT  = re.compile(r'^Комментарий\s*[:\.]?\s*(.+)$', re.DOTALL | re.IGNORECASE)
RE_ZACHET   = re.compile(r'^Зачёт\s*[:\.]?\s*(.+)$', re.DOTALL | re.IGNORECASE)

# Мусорные строки которые нужно пропускать
RE_GARBAGE = re.compile(
    r'^(Синхронный|Окский|Открытый|Чемпионат|Турнир|Тур\s*\d|'
    r'\d{4}-\d{2}-\d{2}|'           # даты
    r'www\.|http|'                   # ссылки
    r'Давать|Зайти|Примечание|'      # редакторские пометки
    r'Автор\s*[:\.]|Источник\s*[:\.])' ,
    re.IGNORECASE
)


def clean(text: str) -> str:
    """Чистим текст от лишних пробелов и спецсимволов."""
    text = text.replace('\xa0', ' ')   # неразрывный пробел
    text = re.sub(r'\n+', ' ', text)   # переносы строк
    text = re.sub(r' {2,}', ' ', text) # множественные пробелы
    return text.strip()


def parse_questions(path: str) -> list[dict]:
    """
    Возвращает список словарей:
    {
        "number": int,
        "question": str,
        "answer": str,
        "comment": str | None,
        "hard": bool,
    }
    """
    doc = Document(path)
    questions = []
    
    current = None  # текущий вопрос который собираем
    auto_number = 0  # счётчик если номер не найден
    
    for para in doc.paragraphs:
        text = clean(para.text)
        if not text or text == '...':
            continue
        
        # Пропускаем мусорные строки
        if RE_GARBAGE.match(text):
            continue
        
        # Проверяем: это начало нового вопроса?
        m_q = RE_QUESTION.match(text)
        if m_q:
            # Сохраняем предыдущий вопрос если он полный
            if current and current.get('answer'):
                questions.append(current)
            
            auto_number += 1
            q_number = int(m_q.group(1)) if m_q.group(1) else auto_number
            q_text = clean(m_q.group(2)) if m_q.group(2) else ''
            
            hard = q_text.startswith('*')
            if hard:
                q_text = q_text.lstrip('* ').strip()
            
            current = {
                'number': auto_number,  # порядковый номер для нумерации слайдов
                'orig_number': q_number, # оригинальный номер из файла
                'question': q_text,
                'answer': None,
                'comment': None,
                'hard': hard,
            }
            continue
        
        if current is None:
            continue
        
        # Продолжение текста вопроса (если вопрос ещё не закончился)
        m_a = RE_ANSWER.match(text)
        m_c = RE_COMMENT.match(text)
        m_z = RE_ZACHET.match(text)
        
        if m_a:
            # Принимаем ответ только если вопрос уже есть и ответа ещё нет
            if current['answer'] is None and current['question']:
                current['answer'] = clean(m_a.group(1))
        elif m_c:
            current['comment'] = clean(m_c.group(1))
        elif m_z:
            # Зачёт — добавляем к комментарию или используем как комментарий
            zachet = clean(m_z.group(1))
            if current['comment']:
                current['comment'] = zachet + ' | ' + current['comment']
            else:
                current['comment'] = zachet
        elif current['answer'] is None:
            # Это продолжение текста вопроса
            if current['question']:
                current['question'] += ' ' + text
            else:
                current['question'] = text
    
    # Не забываем последний вопрос
    if current and current.get('answer'):
        questions.append(current)
    
    return questions
