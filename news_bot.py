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
# หัวข้อข่าวหลักที่จะดึง (แก้ query ตรงนี้ได้ตามต้องการ)
NEWS_QUERY = (
    '"AI agent" OR "AI agents" OR "MCP" OR "Model Context Protocol" OR '
    '"new AI model" OR "releases" AI OR Anthropic OR Claude OR OpenAI OR Gemini OR '
    '"agentic AI" OR "AI coding" OR Cursor OR "GitHub Copilot" OR Codex OR '
    'Windsurf OR "coding assistant" OR "coding agent"'
)
# หัวข้อข่าวเกี่ยวกับ Claude Skills โดยเฉพาะ (แยกหมวดต่างหาก)
SKILL_NEWS_QUERY = (
    '"Claude Skills" OR "Claude Skill" OR "Claude Code skill" OR '
    '"SKILL.md" OR "Agent Skills" OR "Anthropic skill"'
)
NEWS_LANG = "en-US"          # ภาษาแหล่งข่าว (en-US ให้ผลลัพธ์เยอะและครอบคลุมที่สุด)
NEWS_COUNTRY = "US"
FETCH_POOL_SIZE = 20         # ดึงข่าวมาเยอะๆ ก่อน แล้วค่อยกรองเอาที่ดีที่สุด
MAX_ITEMS = 8                # จำนวนข่าวหลักสุดท้ายที่จะส่งเข้า Discord ต่อวัน
SKILL_MAX_ITEMS = 6          # จำนวน skill สุดท้ายที่จะส่งเข้า Discord ต่อวัน (เอาที่ดังๆ เป็นหลัก)
TITLE_SIMILARITY_THRESHOLD = 0.55  # ถ้าหัวข้อคล้ายกันเกินนี้ ถือว่าเป็นข่าวซ้ำ (0-1)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
)

# ใช้ติดตามว่าตอนนี้เจอปัญหาที่ต้องแจ้งเตือนผู้ใช้หรือไม่ (โมเดลถูกปิด / โควตาหมด)
alert_messages = []


def fetch_news_by_query(query_text, max_items):
    """ดึงข่าวจาก Google News RSS ตาม query ที่กำหนด แล้วจัดกลุ่มข่าวที่พูดเรื่องเดียวกัน
    (หัวข้อคล้ายกันมาก) เรื่องที่มีหลายแหล่งพูดถึงพร้อมกัน = ดัง/สำคัญกว่า จะถูกจัดให้ขึ้นก่อน"""
    query = urllib.parse.quote(query_text)
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

    # จัดกลุ่มข่าวที่หัวข้อคล้ายกันมาก (เรื่องเดียวกันจากหลายแหล่ง) เข้าด้วยกัน
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

    # เรื่องที่มีหลายแหล่งพูดถึง (คลัสเตอร์ใหญ่กว่า) ถือว่าดัง/สำคัญกว่า -> เรียงจากมากไปน้อย
    clusters.sort(key=lambda c: len(c["items"]), reverse=True)

    result = []
    for cluster in clusters[:max_items]:
        representative = cluster["items"][0]
        representative["source_count"] = len(cluster["items"])
        result.append(representative)

    return result


def fetch_news():
    return fetch_news_by_query(NEWS_QUERY, MAX_ITEMS)


def fetch_skill_news():
    return fetch_news_by_query(SKILL_NEWS_QUERY, SKILL_MAX_ITEMS)


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

            if resp.status_code == 404:
                print(f"Gemini ตอบกลับ error 404: {resp.text[:500]}")
                # โมเดลถูกปิด/เปลี่ยนชื่อ -> ดึงชื่อโมเดลใหม่ที่ Google แนะนำจากข้อความ error มาแจ้งด้วย ถ้ามี
                suggested = re.search(r"use models/([\w.\-]+)", resp.text)
                suggestion = f" (Google แนะนำให้ใช้ `{suggested.group(1)}` แทน)" if suggested else ""
                alert_messages.append(
                    f"⚠️ **โมเดล Gemini `{GEMINI_MODEL}` ใช้งานไม่ได้แล้ว (ถูกปิด/เปลี่ยนชื่อ)**{suggestion}\n"
                    f"กรุณาไปเปลี่ยนค่า `GEMINI_MODEL` ในไฟล์ `news_bot.py` เป็นโมเดลที่ยังใช้งานได้ แล้วอัปโหลดทับใน repo ครับ"
                )
                return None

            if resp.status_code in (429, 503) and attempt < max_retries:
                # พยายามอ่านเวลาที่ Google บอกให้รอจริงๆ จากข้อความ error เช่น "retry in 45.29s"
                match = re.search(r"retry in ([\d.]+)s", resp.text)
                wait = float(match.group(1)) + 2 if match else attempt * 20
                print(f"Gemini โหลดสูง/limit (status {resp.status_code}) รอ {wait:.0f} วิ แล้วลองใหม่")
                time.sleep(wait)
                continue

            if resp.status_code == 429 and attempt >= max_retries:
                print(f"Gemini ตอบกลับ error {resp.status_code}: {resp.text[:500]}")
                alert_messages.append(
                    f"⚠️ **Gemini (`{GEMINI_MODEL}`) โควตาฟรีวันนี้เต็มแล้ว** ข่าววันนี้เลยไม่มีสรุปให้ (ส่งแค่หัวข้อ+ลิงก์แทน) "
                    f"ลองใหม่พรุ่งนี้ หรือเปลี่ยนไปใช้โมเดลอื่นที่ยังมี quota เหลือได้ครับ"
                )
                return None

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


def summarize_with_gemini(news_items, skill_items):
    """สรุปข่าวหลักและ Claude Skills เป็นภาษาไทยด้วย Gemini เรียกครั้งเดียวรวมทุกอย่าง (ประหยัด quota มาก)
    โดยดึงเนื้อหาจริงจากแต่ละลิงก์มาก่อน (ไม่เสีย quota เพราะเป็นแค่การโหลดหน้าเว็บธรรมดา)"""
    if not GEMINI_API_KEY:
        print("ไม่พบ GEMINI_API_KEY จะข้ามขั้นตอนสรุป และส่งแค่หัวข้อ+ลิงก์แทน")
        for item in news_items + skill_items:
            item["summary"] = None
        return news_items, skill_items

    def build_blocks(items):
        blocks = []
        for i, item in enumerate(items, 1):
            article_text = fetch_article_text(item["link"])
            if article_text:
                blocks.append(f"{i}. หัวข้อ: {item['title']}\nเนื้อหาบางส่วน: {article_text}")
            else:
                blocks.append(f"{i}. หัวข้อ: {item['title']}\n(ไม่มีเนื้อหาเพิ่มเติม)")
        return "\n\n".join(blocks)

    news_block = build_blocks(news_items) if news_items else ""
    skill_block = build_blocks(skill_items) if skill_items else ""

    prompt_parts = [
        "ต่อไปนี้คือข่าวเทคโนโลยี/AI สำหรับผู้อ่านที่เป็นนักพัฒนา (dev) ที่สนใจ AI agent, "
        "MCP (Model Context Protocol), เครื่องมือ AI coding (เช่น Cursor, GitHub Copilot, Codex), "
        "โมเดล AI ใหม่ๆ จากทุกค่าย (OpenAI, Anthropic, Google ฯลฯ) และ Claude Skills โดยเฉพาะ "
        "งานแบ่งเป็น 2 ส่วน กรุณาทำตามรูปแบบการตอบที่กำหนดให้เป๊ะๆ\n"
    ]

    if news_block:
        prompt_parts.append(
            "=== ส่วนที่ 1: ข่าวเทคโนโลยี/AI ทั่วไป ===\n"
            "สรุปแต่ละข่าวเป็นภาษาไทย ข่าวละ 1-2 ประโยคสั้นๆ กระชับ (ไม่เกิน 40 คำต่อข่าว) "
            "อิงจากเนื้อหาจริงที่ให้มา ระบุรายละเอียดเจาะจง เช่นถ้าพูดถึง 'รายการ N ข้อ' ให้บอกว่ามีอะไรบ้าง "
            "ถ้าเป็นข่าวเปิดตัว/อัปเดตเวอร์ชัน ให้ระบุชื่อเวอร์ชัน/ฟีเจอร์ใหม่ที่ชัดเจน และเน้นว่ามันทำอะไรได้ใหม่ที่ dev สนใจ "
            "ถ้าข่าวไหนไม่มีเนื้อหาเพิ่มเติม ให้สรุปเท่าที่ตีความได้จากหัวข้อเท่านั้น ห้ามเดาหรือแต่งเติม\n\n"
            + news_block
            + "\n\nตอบกลับส่วนนี้โดยขึ้นต้นด้วยบรรทัด 'NEWS:' แล้วตามด้วยรายการหมายเลขตรงกับต้นฉบับ หนึ่งข่าวต่อหนึ่งบรรทัด ห้ามใส่คำอธิบายอื่น\n"
        )

    if skill_block:
        prompt_parts.append(
            "\n=== ส่วนที่ 2: Claude Skills ที่น่าสนใจ ===\n"
            "สรุปแต่ละ skill เป็นภาษาไทย skill ละ 1-2 ประโยค (ไม่เกิน 40 คำ) โดยเน้นบอกว่า "
            "'มันทำอะไรได้' และ 'ทำไมถึงเจ๋ง/น่าลองใช้' ให้คนอ่านรู้สึกอยากลองทันที ใช้โทนกระตือรือร้นแต่ยังสุภาพ "
            "อิงจากเนื้อหาจริงที่ให้มาเท่านั้น ถ้าข้อมูลไม่พอให้สรุปเท่าที่ตีความได้จากหัวข้อ ห้ามเดาหรือแต่งเติม\n\n"
            + skill_block
            + "\n\nตอบกลับส่วนนี้โดยขึ้นต้นด้วยบรรทัด 'SKILL:' แล้วตามด้วยรายการหมายเลขตรงกับต้นฉบับ หนึ่ง skill ต่อหนึ่งบรรทัด ห้ามใส่คำอธิบายอื่น\n"
        )

    prompt = "\n".join(prompt_parts)
    result = call_gemini(prompt)

    if not result:
        for item in news_items + skill_items:
            item["summary"] = None
        return news_items, skill_items

    # แยกส่วน NEWS: และ SKILL: ออกจากกัน
    news_text = ""
    skill_text = ""
    news_match = re.search(r"NEWS:(.*?)(?=SKILL:|$)", result, re.DOTALL)
    skill_match = re.search(r"SKILL:(.*)", result, re.DOTALL)
    if news_match:
        news_text = news_match.group(1).strip()
    if skill_match:
        skill_text = skill_match.group(1).strip()

    def apply_summaries(items, text):
        lines = [l.strip() for l in text.split("\n") if l.strip()]
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

    apply_summaries(news_items, news_text)
    apply_summaries(skill_items, skill_text)

    return news_items, skill_items


def build_discord_message(news_items, skill_items):
    today = time.strftime("%d/%m/%Y")
    lines = [f"📰 **สรุปข่าวเทคโนโลยี/AI ประจำวันที่ {today}**\n"]

    for i, it in enumerate(news_items, 1):
        summary = it.get("summary")
        text = summary if summary else it["title"]
        # ถ้ามีหลายแหล่งพูดถึงเรื่องเดียวกัน แปลว่าเป็นข่าวใหญ่ ใส่ 🔥 กำกับไว้
        tag = " 🔥" if it.get("source_count", 1) >= 3 else ""
        lines.append(f"**{i}.**{tag} {text} — {it['link']}")

    lines.append("\n🛠️ **Claude Skills น่าสนใจวันนี้**\n")
    if skill_items:
        for i, it in enumerate(skill_items, 1):
            summary = it.get("summary")
            text = summary if summary else it["title"]
            tag = " 🔥" if it.get("source_count", 1) >= 3 else ""
            lines.append(f"**{i}.**{tag} {text} — {it['link']}")
    else:
        lines.append("วันนี้ไม่มี skill ใหม่")

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
    news_items = fetch_news()
    skill_items = fetch_skill_news()

    if not news_items and not skill_items:
        print("ไม่พบข่าวในตอนนี้")
        return

    news_items, skill_items = summarize_with_gemini(news_items, skill_items)
    message = build_discord_message(news_items, skill_items)
    send_to_discord(message)

    # ถ้ามีปัญหาที่ต้องแจ้งเตือน (เช่นโมเดล Gemini ถูกปิด หรือโควตาหมด) ส่งแจ้งเตือนแยกต่างหาก
    for alert in alert_messages:
        send_to_discord(alert)

    print("ส่งข่าวเข้า Discord เรียบร้อยแล้ว")


if __name__ == "__main__":
    main()
