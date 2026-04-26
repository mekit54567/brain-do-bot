FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    libxslt1-dev \
    libxml2-dev \
    libreoffice \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN pip install -r requirements.txt

CMD ["python", "bot.py"]