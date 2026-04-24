FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

ENV PORT=8080 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PERSISTENT_DIR=/tmp/gratisfy-data

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install chromium

COPY --chown=pwuser:pwuser . .

RUN mkdir -p /tmp/gratisfy-data \
    && chown -R pwuser:pwuser /tmp/gratisfy-data /app

USER pwuser

CMD ["python", "bot.py"]
