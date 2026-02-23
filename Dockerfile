FROM python:3.14-slim AS builder

WORKDIR /install

COPY src/requirements.txt .

RUN pip install --prefix=/install --no-cache-dir -r requirements.txt



FROM python:3.14-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

RUN groupadd -g 10001 app && useradd -u 10001 -g 10001 -m app

WORKDIR /app

RUN chown -R app:app /app

COPY --from=builder /install /usr/local

COPY --chown=app:app src/ .

USER app

ENV PYTHONUNBUFFERED=1
ENV DATA_ROOT=/data

EXPOSE 8080

CMD ["python3", "-m", "telegram_logger"]