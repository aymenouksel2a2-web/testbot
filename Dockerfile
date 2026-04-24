FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
ENV PYTHONUNBUFFERED=1
ENV PERSISTENT_DIR=/tmp/gratisfy-data

# إنشاء مجلد بيانات للجلسة مع صلاحيات المستخدم غير الجذري
RUN mkdir -p /tmp/gratisfy-data && chown -R pwuser:pwuser /tmp/gratisfy-data /app

USER pwuser

CMD ["python", "bot.py"]
