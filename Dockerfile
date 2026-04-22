# استخدام نظام تشغيل مصغر لتقليل حجم الحاوية (حوالي 5 ميجابايت)
FROM alpine:latest

# تثبيت الحزم والأدوات اللازمة لمعالجة النصوص والشبكة
RUN apk add --no-cache unzip wget curl jq

# إنشاء مسارات العمل
WORKDIR /etc/xray

# تحميل نواة Xray واستخراجها في مسار النظام
RUN wget -O xray.zip https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip \
    && unzip xray.zip -d /usr/local/bin/ \
    && rm xray.zip \
    && chmod +x /usr/local/bin/xray

# استيراد ملف الإعدادات الخاص بك
COPY config.json /etc/xray/config.json

# الأمر الحرج: قراءة ملف الإعداد، استبدال المنفذ بالمنفذ الممنوح من بيئة Railway، ثم تشغيل النواة
CMD jq ".inbounds[0].port = ${PORT}" /etc/xray/config.json > /tmp/config.json && /usr/local/bin/xray -config /tmp/config.json
