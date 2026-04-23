FROM python:3.11-slim

WORKDIR /app

# ── Dependencies النظامية لـ Chromium + خطوط لضمان قراءة النصوص في Screenshot ──
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 \
    libpango-1.0-0 libcairo2 libatspi2.0-0 libgtk-3-0 libcurl4 \
    fonts-noto-color-emoji fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── تثبيت Chromium فقط ──
RUN playwright install chromium

COPY . .
EXPOSE 8080
CMD ["python", "bot.py"]
