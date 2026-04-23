FROM python:3.11-slim

WORKDIR /app

# تثبيت المتطلبات
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# نسخ الكود
COPY . .

# Railway يُخصّص منفذاً عبر متغير PORT
EXPOSE 8080

CMD ["python", "bot.py"]
