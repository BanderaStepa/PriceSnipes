# Образ бота «Для рішучих».
# Railway знаходить цей файл і збирає образ сам. Вручну:  docker build -t rishuchi-bot .
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY bot/ bot/
COPY images/ images/
COPY content.yaml .

# Перевірка контенту під час збірки: якщо в content.yaml помилка, нова версія не збереться
# (у журналі збірки буде список помилок), а попередня версія бота продовжить працювати.
RUN python -m bot.check_content

# Процес працює від root: постійний диск (Volume) на Railway монтується з правами root.
# База даних — на Volume (/data на Railway визначається автоматично; для docker compose див. docker-compose.yml).
CMD ["python", "-m", "bot"]
