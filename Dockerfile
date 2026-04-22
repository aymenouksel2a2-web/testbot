FROM python:3.10-slim

# إزالة التعليقات الجانبية من داخل أمر RUN لتجنب أخطاء البناء
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    tesseract-ocr \
    tesseract-ocr-eng \
    libtesseract-dev \
    fonts-liberation \
    wget \
    && rm -rf /var/lib/apt/lists/*

RUN ln -s /usr/bin/tesseract /usr/local/bin/tesseract || true

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

CMD ["python", "bot.py"]
