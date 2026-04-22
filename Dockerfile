FROM python:3.11-slim

# إعداد مسار العمل داخل الحاوية
WORKDIR /app

# نسخ وتثبيت المكتبات
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# تثبيت متصفح Chromium مع كافة اعتماديته داخل نظام Linux
RUN playwright install --with-deps chromium

# نسخ باقي الكود
COPY . .

# أمر التشغيل
CMD ["python", "bot.py"]
