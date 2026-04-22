FROM python:3.10-slim

# 1. تثبيت المتصفح Chrome
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    tesseract-ocr \  # <--- مهم: لاستخراج النص من الصور
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py .

CMD ["python", "bot.py"]
