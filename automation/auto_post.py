import os
import re
import time
import random
import datetime
import feedparser
import requests
import openai
from bs4 import BeautifulSoup

BASE_DIR = r"C:\hugo\myblog"
AUTOMATION_DIR = os.path.join(BASE_DIR, "automation")
POST_DIR = os.path.join(BASE_DIR, "content", "posts")

RSS_FILE = os.path.join(AUTOMATION_DIR, "rss_list.txt")
PROCESSED_FILE = os.path.join(AUTOMATION_DIR, "processed_links.txt")

LIMIT_PER_RSS = 3
MIN_DELAY = 5
MAX_DELAY = 10

openai.api_key = os.getenv("OPENAI_API_KEY")



def clean_url(url):
    return url.split("?")[0]

def load_rss_list():
    with open(RSS_FILE, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def load_processed_links():
    if not os.path.exists(PROCESSED_FILE):
        return set()

    with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())


def save_processed_link(link):
    os.makedirs(AUTOMATION_DIR, exist_ok=True)
    with open(PROCESSED_FILE, "a", encoding="utf-8", newline="\n") as f:
        f.write(link + "\n")


def random_delay():
    delay = random.randint(MIN_DELAY, MAX_DELAY)
    print(f"  ⏳ {delay}초 대기 중...")
    time.sleep(delay)


def clean_text(text):
    lines = text.splitlines()
    cleaned = []

    skip_words = [
        "공감", "댓글", "블로그", "카페", "이웃추가",
        "본문 기타 기능", "공유하기", "URL 복사"
    ]

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if any(word in line for word in skip_words):
            continue
        cleaned.append(line)

    return "\n".join(cleaned)


def fix_response_encoding(res):
    """
    네이버/블로그 응답이 깨지는 경우 방지.
    requests가 ISO-8859-1로 잘못 잡는 경우가 있어서 apparent_encoding 우선 적용.
    """
    if not res.encoding or res.encoding.lower() in ["iso-8859-1", "ascii"]:
        res.encoding = res.apparent_encoding or "utf-8"
    return res.text


def get_naver_blog_content(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
    }

    try:
        res = requests.get(url, headers=headers, timeout=15)
        res.raise_for_status()
        html = fix_response_encoding(res)
    except Exception as e:
        print(f"  ❌ 접속 실패: {e}")
        return None

    soup = BeautifulSoup(html, "html.parser")

    iframe = soup.find("iframe", id="mainFrame")
    if iframe:
        src = iframe.get("src")
        if src:
            iframe_url = "https://blog.naver.com" + src
            try:
                res = requests.get(iframe_url, headers=headers, timeout=15)
                res.raise_for_status()
                html = fix_response_encoding(res)
                soup = BeautifulSoup(html, "html.parser")
            except Exception as e:
                print(f"  ❌ iframe 접속 실패: {e}")
                return None

    selectors = [
        "div.se-main-container",
        "div#postViewArea",
        "div.post-view",
        "div.post_ct"
    ]

    for selector in selectors:
        content = soup.select_one(selector)
        if content:
            text = clean_text(content.get_text("\n", strip=True))
            if len(text) > 100:
                return text

    return None


def make_slug(title):
    slug = re.sub(r"[^가-힣a-zA-Z0-9\s-]", "", title)
    slug = re.sub(r"\s+", "-", slug.strip())
    slug = slug.strip("-")
    return slug[:60] if slug else "post"


def escape_yaml(text):
    if not text:
        return ""
    text = str(text)
    text = text.replace("\\", "\\\\")
    text = text.replace('"', '\\"')
    text = text.replace("\n", " ")
    return text.strip()


def make_description(text, fallback_title):
    text = re.sub(r"[#>*`\-\[\]\(\)]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    if len(text) >= 60:
        return text[:155]

    return f"{fallback_title} 관련 핵심 내용을 쉽게 정리한 요약 정보입니다."


def summarize_with_gpt(title, content):
    prompt = f"""
다음 네이버 블로그 글을 기반으로 구글 검색 노출용 아카이브 콘텐츠를 만들어줘.

조건:
- 원문 제목을 그대로 쓰지 말 것
- SEO용 제목을 새로 만들 것
- 제목은 35~55자 정도
- 제목은 자연스러운 검색형 문장으로 작성
- 본문은 500~800자 정도
- 원문을 그대로 복사하지 말 것
- 정보 요약 중심
- 과장 표현 금지
- 마지막에 "핵심 포인트" 제목과 bullet 3개 포함
- Markdown 형식
- 원문 링크 문장은 만들지 말 것

출력 형식은 반드시 아래처럼 해줘:

제목: 여기에 새 제목

본문:
여기에 본문

원문 제목:
{title}

원문 내용:
{content[:4000]}
"""

    response = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": "너는 SEO 요약 아카이브 글을 작성하는 한국어 블로그 에디터다."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0.5,
        max_tokens=1200
    )

    result = response["choices"][0]["message"]["content"].strip()

    new_title = title
    summary = result

    if "본문:" in result:
        parts = result.split("본문:", 1)
        title_part = parts[0].replace("제목:", "").strip()
        body_part = parts[1].strip()

        if title_part:
            new_title = title_part

        summary = body_part

    new_title = new_title.replace("\n", " ").strip()
    summary = summary.strip()

    return new_title, summary


def save_markdown(title, summary, original_link):
    os.makedirs(POST_DIR, exist_ok=True)

    now = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=9))
    )
    date_str = now.isoformat(timespec="seconds")

    slug = make_slug(title)
    filename = f"{now.strftime('%Y%m%d-%H%M%S')}-{slug}.md"
    filepath = os.path.join(POST_DIR, filename)

    description = make_description(summary, title)

    md = f"""---
title: "{escape_yaml(title)}"
date: {date_str}
draft: false
description: "{escape_yaml(description)}"
categories: ["쇼핑정보"]
tags: ["쇼핑정보", "상품추천"]
---

{summary}

---

👉 [원문 보러가기]({original_link})
"""

    # 핵심: UTF-8로 저장
    with open(filepath, "w", encoding="utf-8", newline="\n") as f:
        f.write(md)

    return filepath


def main():
    if not openai.api_key:
        print("❌ OPENAI_API_KEY 환경변수가 없습니다.")
        print("CMD에서 먼저 실행:")
        print('set OPENAI_API_KEY=여기에_API키')
        return

    os.makedirs(AUTOMATION_DIR, exist_ok=True)
    os.makedirs(POST_DIR, exist_ok=True)

    rss_list = load_rss_list()
    processed_links = load_processed_links()

    new_count = 0
    skip_count = 0
    fail_count = 0

    print("RSS → GPT 요약 → Hugo Markdown 생성 시작")
    print("=" * 60)

    for rss_url in rss_list:
        print(f"\nRSS 확인 중: {rss_url}")
        feed = feedparser.parse(rss_url)

        if not feed.entries:
            print("  가져온 글 없음")
            continue

        for entry in feed.entries[:LIMIT_PER_RSS]:
            title = entry.title
            link = entry.link

            if link in processed_links:
                print(f"  중복 건너뜀: {title}")
                skip_count += 1
                continue

            print(f"\n  새 글 발견: {title}")
            print(f"  링크: {link}")

            content = get_naver_blog_content(link)

            if not content:
                print("  ❌ 본문 가져오기 실패")
                fail_count += 1
                random_delay()
                continue

            print(f"  ✅ 본문 성공 / 글자 수: {len(content)}")

            try:
                new_title, summary = summarize_with_gpt(title, content)
                print(f"  ✅ GPT 제목 생성: {new_title}")
                print("  ✅ GPT 요약 완료")

                cleaned_link = clean_url(link)
                filepath = save_markdown(new_title, summary, cleaned_link)
                print(f"  ✅ Markdown 저장 완료: {filepath}")

                save_processed_link(link)
                processed_links.add(link)
                new_count += 1

            except Exception as e:
                print(f"  ❌ GPT 또는 저장 실패: {e}")
                fail_count += 1

            print("-" * 60)
            random_delay()

    print("\n작업 완료")
    print(f"새 글 생성: {new_count}개")
    print(f"중복 건너뜀: {skip_count}개")
    print(f"실패: {fail_count}개")


if __name__ == "__main__":
    main()