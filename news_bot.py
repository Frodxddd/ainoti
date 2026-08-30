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
from bs4 import BeautifulSoup

# ---------- ตั้งค่า ----------
# หัวข้อข่าวที่จะดึง (แก้ query ตรงนี้ได้ตามต้องการ)
NEWS_QUERY = 'Anthropic OR Claude OR "AI agent" OR "new AI model" OR OpenAI OR Gemini'
NEWS_LANG = "en-US"          # ภาษาแหล่งข่าว (en-US ให้ผลลัพธ์เยอะและครอบคลุมที่สุด)
NEWS_COUNTRY = "US"
MAX_ITEMS = 8                # จำนวนข่าวสูงสุดต่อวัน

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

GEMINI_MODEL = "gemini-3.6-flash"
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


def fetch_article_text(url, max_chars=1500):
    """พยายามดึงเนื้อหาจริงของข่าวจากลิงก์ (best-effort, อาจได้บ้างไม่ได้บ้างแล้วแต่เว็บ)"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
        text = " ".join(p for p in paragraphs if len(p) > 40)
        return text[:max_chars] if text else None
    except Exception as e:
        print(f"ดึงเนื้อหาข่าวไม่สำเร็จ ({url[:60]}...): {type(e).__name__}: {e}")
        return None


def summarize_one_with_gemini(item):
    """สรุปข่าวทีละชิ้น โดยใช้เนื้อหาจริงที่ดึงมาได้ (ถ้ามี) เพื่อให้สรุปมีรายละเอียดที่แม่นยำ"""
    article_text = fetch_article_text(item["link"])

    if article_text:
        source_info = f"หัวข้อข่าว: {item['title']}\n\nเนื้อหาข่าว (บางส่วน): {article_text}"
        detail_note = (
            "ให้สรุปโดยอิงจากเนื้อหาข่าวจริงด้านล่าง ระบุรายละเอียดสำคัญที่เจาะจง "
            "เช่น ถ้าข่าวพูดถึง 'รายการ N ข้อ' ให้บอกว่ามีอะไรบ้างสั้นๆ ถ้าเนื้อหาไม่มีรายละเอียดพอ ให้สรุปเท่าที่มีข้อมูลจริงเท่านั้น ห้ามเดาหรือแต่งเติม"
        )
    else:
        source_info = f"หัวข้อข่าว: {item['title']}"
        detail_note = "ไม่มีเนื้อหาข่าวให้ ให้สรุปเท่าที่ตีความได้จากหัวข้อเท่านั้น ห้ามเดาหรือแต่งเติมรายละเอียดที่ไม่มีในหัวข้อ"

    prompt = (
        "ช่วยสรุปข่าวเทคโนโลยี/AI ข่าวนี้เป็นภาษาไทย 1-2 ประโยคสั้นๆ กระชับ (ไม่เกิน 40 คำ) "
        f"{detail_note} "
        "ไม่ต้องมีคำนำหรือคำลงท้าย ตอบแค่เนื้อหาสรุปเท่านั้น:\n\n" + source_info
    )

    body = {"contents": [{"parts": [{"text": prompt}]}]}
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(GEMINI_URL, json=body, timeout=60)
            if resp.status_code in (429, 503) and attempt < max_retries:
                wait = attempt * 10
                print(f"Gemini โหลดสูง/limit (status {resp.status_code}) รอ {wait} วิ แล้วลองใหม่")
                time.sleep(wait)
                continue
            if not resp.ok:
                print(f"Gemini ตอบกลับ error {resp.status_code}: {resp.text[:500]}")
            resp.raise_for_status()
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            return text
        except requests.exceptions.Timeout:
            print(f"เรียก Gemini timeout (ครั้งที่ {attempt})")
            if attempt < max_retries:
                time.sleep(attempt * 10)
                continue
            return None
        except Exception as e:
            print(f"เรียก Gemini ไม่สำเร็จ (ครั้งที่ {attempt}): {type(e).__name__}: {e}")
            if attempt >= max_retries:
                return None


def summarize_with_gemini(items):
    """สรุปข่าวทุกชิ้นเป็นภาษาไทย โดยดึงเนื้อหาจริงมาช่วยให้สรุปละเอียดขึ้น"""
    if not GEMINI_API_KEY:
        print("ไม่พบ GEMINI_API_KEY จะข้ามขั้นตอนสรุป และส่งแค่หัวข้อ+ลิงก์แทน")
        for item in items:
            item["summary"] = None
        return items

    for item in items:
        item["summary"] = summarize_one_with_gemini(item)

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
