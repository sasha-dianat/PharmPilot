FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y build-essential libpq-dev && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install poetry && \
    poetry config virtualenvs.create false && \
    poetry install --no-dev --no-interaction

COPY . .

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
