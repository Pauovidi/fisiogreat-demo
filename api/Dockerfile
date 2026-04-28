FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

COPY app ./app
ENV PYTHONPATH=/app
ENV PORT=8080

RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

CMD ["sh","-c","python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*' --log-level info"]
