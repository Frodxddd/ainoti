"""
Daily Tech/AI News -> Discord bot
----------------------------------
1. ดึงข่าวเทคโนโลยี/AI จาก Google News RSS (ฟรี ไม่ต้องมี API key)
2. ส่งหัวข้อข่าวให้ Gemini (free tier) ช่วยสรุปสั้นๆ เป็นภาษาไทย
3. โพสต์ผลสรุปเข้า Discord ผ่าน Webhook

ใช้งานฟรีทั้งหมด ถ้ารันผ่าน GitHub Actions (ดู .github/workflows/daily-news.yml)
"""

import os
import sys
import time
import html
import urllib.parse
import requests
import feedparser

# ---------- ตั้งค่า ----------
# หัวข้อข่าวที่จะดึง (แก้ query ตรงนี้ได้ตามต้องการ)
NEWS_QUERY = 'Anthropic OR Claude OR "AI agent" OR "new AI model" OR OpenAI OR Gemini'
NEWS_LANG = "en-US"          # ภาษาแหล่งข่าว (en-US ให้ผลลัพธ์เยอะและครอบคลุมที่สุด)
NEWS_COUNTRY = "US"
MAX_ITEMS = 8                # จำนวนข่าวสูงสุดต่อวัน

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
)


def fetch_news():
    """ดึงข่าวจาก Google News RSS"""
    query = urllib.parse.quote(NEWS_QUERY)
    url = (
        f"https://news.google.com/rss/search?q={query}"
        f"&hl={NEWS_LANG}&gl={NEWS_COUNTRY}&ceid={NEWS_COUNTRY}:{NEWS_LANG.split('-')[0]}"
    )
    feed = feedparser.parse(url)
    items = []
    for entry in feed.entries[:MAX_ITEMS]:
        items.append(
            {
                "title": html.unescape(entry.title),
                "link": entry.link,
                "source": entry.get("source", {}).get("title", ""),
            }
        )
    return items


def summarize_with_gemini(items):
    """ส่งหัวข้อข่าวให้ Gemini สรุปสั้นๆ เป็นภาษาไทย ทีละข่าว"""
    if not GEMINI_API_KEY:
        print("ไม่พบ GEMINI_API_KEY จะข้ามขั้นตอนสรุป และส่งแค่หัวข้อ+ลิงก์แทน")
        return items

    numbered = "\n".join(f"{i+1}. {it['title']}" for i, it in enumerate(items))
    prompt = (
        "ต่อไปนี้คือหัวข้อข่าวเทคโนโลยี/AI ภาษาอังกฤษ "
        "ช่วยสรุปแต่ละข่าวเป็นภาษาไทย สั้นมากๆ แค่ 4-8 คำ บอกว่า 'ใคร ทำอะไร' "
        "เช่น 'Anthropic เปิดตัวโมเดลใหม่ ฉลาดขึ้น' หรือ 'OpenAI ปล่อยฟีเจอร์เอเจนต์ใหม่' "
        "ไม่ต้องมีคำฟุ่มเฟือย ไม่ต้องเป็นประโยคสมบูรณ์ "
        "ตอบกลับเป็นรายการโดยขึ้นต้นแต่ละบรรทัดด้วยหมายเลขให้ตรงกับต้นฉบับ "
        "ห้ามใส่คำอธิบายอื่นนอกจากสรุป:\n\n" + numbered
    )

    body = {"contents": [{"parts": [{"text": prompt}]}]}
    try:
        resp = requests.post(GEMINI_URL, json=body, timeout=30)
        if not resp.ok:
            print(f"Gemini ตอบกลับ error {resp.status_code}: {resp.text[:500]}")
        resp.raise_for_status()
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        lines = [l.strip() for l in text.strip().split("\n") if l.strip()]

        for i, item in enumerate(items):
            if i < len(lines):
                # ตัดหมายเลขนำหน้าออก เช่น "1. " หรือ "1) "
                cleaned = lines[i]
                for sep in [". ", ") "]:
                    if sep in cleaned[:5]:
                        cleaned = cleaned.split(sep, 1)[1]
                        break
                item["summary"] = cleaned
            else:
                item["summary"] = None
    except Exception as e:
        print(f"เรียก Gemini ไม่สำเร็จ: {type(e).__name__}: {e}")
        for item in items:
            item["summary"] = None

    return items


def build_discord_message(items):
    today = time.strftime("%d/%m/%Y")
    lines = [f"📰 **สรุปข่าวเทคโนโลยี/AI ประจำวันที่ {today}**\n"]
    for i, it in enumerate(items, 1):
        summary = it.get("summary")
        if summary:
            lines.append(f"**{i}.** {summary} — {it['link']}")
        else:
            lines.append(f"**{i}.** {it['title']} — {it['link']}")
    return "\n".join(lines)


def send_to_discord(message):
    if not DISCORD_WEBHOOK_URL:
        print("ไม่พบ DISCORD_WEBHOOK_URL", file=sys.stderr)
        sys.exit(1)

    # Discord webhook จำกัดข้อความไม่เกิน 2000 ตัวอักษรต่อครั้ง จึงแบ่งส่งเป็นก้อนๆ
    chunk = ""
    for line in message.split("\n"):
        if len(chunk) + len(line) + 1 > 1900:
            requests.post(DISCORD_WEBHOOK_URL, json={"content": chunk})
            chunk = ""
        chunk += line + "\n"
    if chunk.strip():
        requests.post(DISCORD_WEBHOOK_URL, json={"content": chunk})


def main():
    items = fetch_news()
    if not items:
        print("ไม่พบข่าวในตอนนี้")
        return
    items = summarize_with_gemini(items)
    message = build_discord_message(items)
    send_to_discord(message)
    print("ส่งข่าวเข้า Discord เรียบร้อยแล้ว")


if __name__ == "__main__":
    main()
