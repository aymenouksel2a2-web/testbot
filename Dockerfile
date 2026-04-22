# استخدام النسخة الرسمية من Playwright المجهزة بكل مكتبات لينكس المطلوبة
FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy

WORKDIR /app

# نسخ وتثبيت المتطلبات
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# تشغيل البوت
CMD ["python", "main.py"]
