FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data

# LibreOffice — для .doc/.rtf/.odt и PDF-версии презентаций.
# Carlito — метрический аналог Calibri: PDF и предпросмотр выглядят как в PowerPoint.
# DejaVu — запасной шрифт для значков вроде ★.
RUN apt-get update && apt-get install -y \
    libreoffice \
    fonts-crosextra-carlito \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .

CMD ["python", "bot.py"]
