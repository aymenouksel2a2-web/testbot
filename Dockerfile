FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium

COPY . .

ENV PORT=8080
ENV PYTHONUNBUFFERED=1

# استخدام مستخدم غير root لأمان أكثر (متوفر في صور Playwright الرسمية)
USER pwuser

CMD ["python", "bot.py"]
