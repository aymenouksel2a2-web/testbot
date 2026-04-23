# استخدم Python 3.11 (أخف وأستقر)
FROM python:3.11-slim

# تثبيت أدوات النظام الأساسية
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# نسخ وتحميل مكتبات Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# تثبيت Chromium ومكتباته المطلوبة من Playwright
RUN playwright install chromium
RUN playwright install-deps chromium

# نسخ باقي الملفات
COPY . .

# تشغيل البوت
CMD ["python", "main.py"]
