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
import re
import time
import html
import difflib
import urllib.parse
import requests
import feedparser
from bs4 import BeautifulSoup

# ---------- ตั้งค่า ----------
# หัวข้อข่าวที่จะดึง (แก้ query ตรงนี้ได้ตามต้องการ)
NEWS_QUERY = (
    '"AI agent" OR "AI agents" OR "MCP" OR "Model Context Protocol" OR '
    '"new AI model" OR "releases" AI OR Anthropic OR Claude OR OpenAI OR '
    '"agentic AI" OR "AI coding"'
)
NEWS_LANG = "en-US"          # ภาษาแหล่งข่าว (en-US ให้ผลลัพธ์เยอะและครอบคลุมที่สุด)
NEWS_COUNTRY = "US"
FETCH_POOL_SIZE = 20         # ดึงข่าวมาเยอะๆ ก่อน แล้วค่อยกรองเอาที่ดีที่สุด
MAX_ITEMS = 8                # จำนวนข่าวสุดท้ายที่จะส่งเข้า Discord ต่อวัน
TITLE_SIMILARITY_THRESHOLD = 0.55  # ถ้าหัวข้อคล้ายกันเกินนี้ ถือว่าเป็นข่าวซ้ำ (0-1)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

GEMINI_MODEL = "gemini-2.5-flash-lite"
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
)


def fetch_news():
    """ดึงข่าวจาก Google News RSS แล้วจัดกลุ่มข่าวที่พูดเรื่องเดียวกัน (หัวข้อคล้ายกันมาก)
    ข่าวที่มีหลายสำนักข่าวรายงานพร้อมกัน = ข่าวใหญ่/สำคัญ จะถูกจัดให้ขึ้นก่อน"""
    query = urllib.parse.quote(NEWS_QUERY)
    url = (
        f"https://news.google.com/rss/search?q={query}"
        f"&hl={NEWS_LANG}&gl={NEWS_COUNTRY}&ceid={NEWS_COUNTRY}:{NEWS_LANG.split('-')[0]}"
    )
    feed = feedparser.parse(url)
    raw_items = []
    for entry in feed.entries[:FETCH_POOL_SIZE]:
        raw_items.append(
            {
                "title": html.unescape(entry.title),
                "link": entry.link,
                "source": entry.get("source", {}).get("title", ""),
            }
        )

    # จัดกลุ่มข่าวที่หัวข้อคล้ายกันมาก (ข่าวเดียวกันจากหลายสำนัก) เข้าด้วยกัน
    clusters = []  # each: {"items": [...]}
    for item in raw_items:
        matched_cluster = None
        for cluster in clusters:
            if difflib.SequenceMatcher(
                None, item["title"].lower(), cluster["items"][0]["title"].lower()
            ).ratio() > TITLE_SIMILARITY_THRESHOLD:
                matched_cluster = cluster
                break
        if matched_cluster:
            matched_cluster["items"].append(item)
        else:
            clusters.append({"items": [item]})

    # ข่าวที่มีหลายสำนักรายงาน (คลัสเตอร์ใหญ่กว่า) ถือว่าสำคัญกว่า -> เรียงจากมากไปน้อย
    clusters.sort(key=lambda c: len(c["items"]), reverse=True)

    result = []
    for cluster in clusters[:MAX_ITEMS]:
        representative = cluster["items"][0]
        representative["source_count"] = len(cluster["items"])
        result.append(representative)

    return result


def fetch_article_text(url, max_chars=800):
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


def call_gemini(prompt):
    """เรียก Gemini ครั้งเดียวพร้อม retry ตามเวลาที่ Google แนะนำจริงๆ"""
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(GEMINI_URL, json=body, timeout=90)
            if resp.status_code in (429, 503) and attempt < max_retries:
                # พยายามอ่านเวลาที่ Google บอกให้รอจริงๆ จากข้อความ error เช่น "retry in 45.29s"
                match = re.search(r"retry in ([\d.]+)s", resp.text)
                wait = float(match.group(1)) + 2 if match else attempt * 20
                print(f"Gemini โหลดสูง/limit (status {resp.status_code}) รอ {wait:.0f} วิ แล้วลองใหม่")
                time.sleep(wait)
                continue
            if not resp.ok:
                print(f"Gemini ตอบกลับ error {resp.status_code}: {resp.text[:500]}")
            resp.raise_for_status()
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
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
    """สรุปข่าวทุกชิ้นเป็นภาษาไทยด้วย Gemini เรียกครั้งเดียวรวมทุกข่าว (ประหยัด quota มาก)
    โดยดึงเนื้อหาจริงจากแต่ละลิงก์มาก่อน (ไม่เสีย quota เพราะเป็นแค่การโหลดหน้าเว็บธรรมดา)"""
    if not GEMINI_API_KEY:
        print("ไม่พบ GEMINI_API_KEY จะข้ามขั้นตอนสรุป และส่งแค่หัวข้อ+ลิงก์แทน")
        for item in items:
            item["summary"] = None
        return items

    blocks = []
    for i, item in enumerate(items, 1):
        article_text = fetch_article_text(item["link"])
        if article_text:
            blocks.append(f"{i}. หัวข้อ: {item['title']}\nเนื้อหาบางส่วน: {article_text}")
        else:
            blocks.append(f"{i}. หัวข้อ: {item['title']}\n(ไม่มีเนื้อหาเพิ่มเติม)")

    combined = "\n\n".join(blocks)
    prompt = (
        "ต่อไปนี้คือข่าวเทคโนโลยี/AI หลายข่าว สำหรับผู้อ่านที่เป็นนักพัฒนา (dev) ที่สนใจ AI agent, "
        "MCP (Model Context Protocol), โมเดล AI ใหม่ๆ และความเปลี่ยนแปลงของวงการ AI "
        "แต่ละข่าวมีหัวข้อและเนื้อหาบางส่วนกำกับด้วยหมายเลข "
        "ช่วยสรุปแต่ละข่าวเป็นภาษาไทย ข่าวละ 1-2 ประโยคสั้นๆ กระชับ (ไม่เกิน 40 คำต่อข่าว) "
        "ให้สรุปโดยอิงจากเนื้อหาจริงที่ให้มา ระบุรายละเอียดสำคัญที่เจาะจง เช่น ถ้าข่าวพูดถึง 'รายการ N ข้อ' "
        "ให้บอกว่ามีอะไรบ้างสั้นๆ ถ้าเป็นข่าวเปิดตัวโมเดล/ฟีเจอร์ใหม่ ให้เน้นว่ามันทำอะไรได้ใหม่ที่ dev น่าจะสนใจ "
        "ถ้าข่าวไหนไม่มีเนื้อหาเพิ่มเติม ให้สรุปเท่าที่ตีความได้จากหัวข้อเท่านั้น "
        "ห้ามเดาหรือแต่งเติมข้อมูลที่ไม่มีในต้นฉบับ\n\n"
        "ตอบกลับเป็นรายการโดยขึ้นต้นแต่ละบรรทัดด้วยหมายเลขให้ตรงกับต้นฉบับ หนึ่งข่าวต่อหนึ่งบรรทัด "
        "ห้ามใส่คำอธิบายอื่นนอกจากสรุป:\n\n" + combined
    )

    result = call_gemini(prompt)
    if not result:
        for item in items:
            item["summary"] = None
        return items

    lines = [l.strip() for l in result.split("\n") if l.strip()]
    for i, item in enumerate(items):
        if i < len(lines):
            cleaned = lines[i]
            for sep in [". ", ") "]:
                if sep in cleaned[:5]:
                    cleaned = cleaned.split(sep, 1)[1]
                    break
            item["summary"] = cleaned
        else:
            item["summary"] = None

    return items


def build_discord_message(items):
    today = time.strftime("%d/%m/%Y")
    lines = [f"📰 **สรุปข่าวเทคโนโลยี/AI ประจำวันที่ {today}**\n"]
    for i, it in enumerate(items, 1):
        summary = it.get("summary")
        text = summary if summary else it["title"]
        # ถ้ามีหลายสำนักข่าวรายงานเรื่องเดียวกัน แปลว่าเป็นข่าวใหญ่ ใส่ 🔥 กำกับไว้
        tag = " 🔥" if it.get("source_count", 1) >= 3 else ""
        lines.append(f"**{i}.**{tag} {text} — {it['link']}")
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
