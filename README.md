# Gratisfy Ultra Bot

هذه نسخة محسّنة مبنية على الملف الأصلي، وتبقي نفس آلية العمل: تشغيل متصفح Playwright، إرسال الرسالة من واجهة Gratisfy، ثم استخراج الرد من الصفحة.

## المتغيرات المطلوبة

```env
BOT_TOKEN=ضع_توكن_بوت_تيليجرام
RAILWAY_PUBLIC_DOMAIN=your-app.up.railway.app
LOGIN_EMAIL=اختياري
LOGIN_PASSWORD=اختياري
TARGET_MODEL=Grok Uncensored
```

## متغيرات اختيارية

```env
GRATISFY_URL=https://gratisfy.xyz/chat
STREAM_INTERVAL=3
EXTRACT_TIMEOUT=120
RESPONSE_STABLE_ROUNDS=2
SCREENSHOT_QUALITY=60
VIEWPORT_WIDTH=960
VIEWPORT_HEIGHT=540
PERSISTENT_DIR=/tmp/gratisfy-data
LOG_LEVEL=INFO
```

## أوامر Telegram

- `/start` عرض التعليمات
- `/stream` بدء جلسة المتصفح
- `/stop` إيقاف الجلسة
- `/status` عرض حالة الجلسة
