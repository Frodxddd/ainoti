# บอทสรุปข่าวเทคโนโลยี/AI ส่งเข้า Discord ทุกวัน (ฟรี)

ระบบนี้จะ:
1. ดึงข่าวเทคโนโลยี/AI ล่าสุดจาก Google News RSS
2. ให้ AI (Gemini free tier) สรุปแต่ละข่าวเป็นภาษาไทยสั้นๆ
3. โพสต์เข้าช่อง Discord ที่คุณกำหนด
4. รันอัตโนมัติทุกวันผ่าน GitHub Actions (ฟรี)

ไม่มีค่าใช้จ่ายใดๆ ทั้งสิ้น ถ้าใช้ตามขั้นตอนนี้

---

## ขั้นตอนติดตั้ง

### 1. สร้าง Discord Webhook
1. เปิดเซิร์ฟเวอร์ Discord ของคุณ → ไปที่ช่องที่ต้องการให้ส่งข่าว
2. คลิกไอคอนตั้งค่าช่อง (⚙️) → **Integrations** → **Webhooks** → **New Webhook**
3. ตั้งชื่อ (เช่น "News Bot") แล้วกด **Copy Webhook URL**
4. เก็บ URL นี้ไว้ (จะใช้ในขั้นตอนที่ 4)

### 2. ขอ Gemini API Key (ฟรี)
1. ไปที่ https://aistudio.google.com/app/apikey
2. ล็อกอินด้วยบัญชี Google แล้วกด **Create API Key**
3. คัดลอกคีย์เก็บไว้ (Gemini มี free tier ให้ใช้ต่อวันโดยไม่มีค่าใช้จ่าย)

### 3. สร้าง GitHub Repository
1. สร้าง repo ใหม่บน GitHub (private หรือ public ก็ได้ ทั้งคู่ใช้ GitHub Actions ฟรีได้)
2. อัปโหลดไฟล์ทั้งหมดในโฟลเดอร์นี้ขึ้น repo (`news_bot.py`, `requirements.txt`, `.github/workflows/daily-news.yml`)

### 4. ใส่ Secrets ใน GitHub
ใน repo ของคุณ ไปที่ **Settings → Secrets and variables → Actions → New repository secret** แล้วเพิ่ม 2 ตัว:
- `GEMINI_API_KEY` = คีย์จากขั้นตอนที่ 2
- `DISCORD_WEBHOOK_URL` = URL จากขั้นตอนที่ 1

### 5. ทดสอบรัน
ไปที่แท็บ **Actions** ในหน้า repo → เลือก workflow "Daily Tech/AI News to Discord" → กด **Run workflow** เพื่อทดสอบทันที (ไม่ต้องรอถึงเวลา)

ถ้าทำงานถูกต้อง ข่าวจะโผล่ในช่อง Discord ของคุณภายในไม่กี่วินาที

---

## ปรับแต่งได้

เปิดไฟล์ `news_bot.py` แล้วแก้ตรงนี้ตามต้องการ:
- `NEWS_QUERY` → เปลี่ยนคำค้นหาข่าว (ตอนนี้ตั้งเป็นข่าวเทคโนโลยี/AI)
- `MAX_ITEMS` → จำนวนข่าวต่อวัน
- เวลาในไฟล์ `.github/workflows/daily-news.yml` (`cron: "0 0 * * *"`) → เวลาที่รันในแต่ละวัน (ปัจจุบันตั้งไว้ 07:00 น. เวลาไทย)

## หมายเหตุ
- Google News RSS และ GitHub Actions (schedule job) ไม่มีค่าใช้จ่าย
- Gemini free tier มีโควตาการเรียกใช้ต่อวันจำกัดแต่เพียงพอสำหรับการรันวันละครั้ง
- ถ้าไม่ต้องการให้ AI สรุป และอยากได้แค่หัวข้อข่าว+ลิงก์ ให้ลบ/ไม่ตั้งค่า `GEMINI_API_KEY` ไว้ก็ได้ ระบบจะข้ามขั้นตอนสรุปให้อัตโนมัติ
