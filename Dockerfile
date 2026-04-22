FROM python:3.10-slim

# تثبيت الحزم الأساسية + Chromium + Tesseract (مع المكتبات اللازمة)
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    tesseract-ocr \          # محرك التعرف على النصوص
    tesseract-ocr-eng \      # حزمة اللغة الإنجليزية
    libtesseract-dev \       # ملفات التطوير (مهمة)
    fonts-liberation \       # خطوط
    wget \                   # للتحميل إن لزم
    && rm -rf /var/lib/apt/lists/*

# العملية هذه مهمة جداً لضمان وجود الرابط الصحيح
RUN ln -s /usr/bin/tesseract /usr/local/bin/tesseract || true

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py .

# تشغيل
CMD ["python", "bot.py"]
