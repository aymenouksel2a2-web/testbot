FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium

COPY . .

ENV PORT=8080
ENV PYTHONUNBUFFERED=1
ENV PERSISTENT_DIR=/tmp/gratisfy-data

# إنشاء مجلد البيانات المستمر مع صلاحيات للمستخدم pwuser
RUN mkdir -p /tmp/gratisfy-data && chown -R pwuser:pwuser /tmp/gratisfy-data

USER pwuser

CMD ["python", "bot.py"]
