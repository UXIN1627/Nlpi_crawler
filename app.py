"""
國立公共資訊圖書館 Google 評論爬蟲 - Streamlit 介面
"""

import asyncio
import re
import subprocess
import sys
import os
import random 
from datetime import datetime

import streamlit as st
from playwright.async_api import async_playwright

# ── 頁面設定 ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="國立公共資訊圖書館 Google 評論爬蟲",
    page_icon="📚",
    layout="centered",
)

# 💡 使用你測試成功的「直達評論區」網址
TARGET_URL = "https://www.google.com/maps/place/%E5%9C%8B%E7%AB%8B%E5%85%AC%E5%85%B1%E8%B3%87%E8%A8%8A%E5%9C%96%E6%9B%B8%E9%A4%A8/@24.1272771,120.6708688,17z/data=!4m7!3m6!1s0x34693d0146d61257:0x7a16000e8eb3abce!8m2!3d24.1272771!4d120.6708688!9m1!1b1"

# ── 找出系統 Chromium 路徑 (Streamlit Cloud 專用) ──────────────────────────────
def find_chromium() -> str | None:
    candidates = [
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None

# ── 爬蟲核心邏輯 ─────────────────────────────────────────────────────────────

def stars_from_aria(aria_label: str) -> str:
    match = re.search(r"(\d+(?:\.\d+)?)", aria_label or "")
    return match.group(1) if match else "N/A"

async def scroll_to_load(page, target: int, status_cb):
    """同步本地端成功的捲動邏輯"""
    last_count = 0
    stale_rounds = 0
    while True:
        reviews = page.locator('div[data-review-id]')
        current_count = await reviews.count()
        status_cb(current_count)
        
        if current_count >= target or stale_rounds >= 10:
            break
        if current_count == last_count:
            stale_rounds += 1
        else:
            stale_rounds = 0
        last_count = current_count
        
        try:
            # 在 Headless 模式下，移動滑鼠並捲動
            await page.mouse.move(300, 500)
            await page.mouse.wheel(0, 3000)
            if current_count > 0:
                await reviews.last.scroll_into_view_if_needed()
            await page.wait_for_timeout(2000)
            delay = random.randint(2500, 5500) # 隨機等待 2.5 到 5.5 秒
            await page.wait_for_timeout(delay)
        except:
            break

async def parse_reviews(page, max_count: int) -> list[dict]:
    """同步本地端成功的解析邏輯"""
    # 展開「更多」
    mores = page.locator('button[aria-label*="查看更多"], button:has-text("更多")')
    for i in range(await mores.count()):
        try:
            m = mores.nth(i)
            if await m.is_visible(timeout=500): await m.click()
        except: pass
        
    els = page.locator('div[data-review-id]')
    total = min(await els.count(), max_count)
    reviews = []
    for i in range(total):
        el = els.nth(i)
        try:
            name = await el.locator('div[class*="d4r55"]').first.inner_text()
            star_el = el.locator('span[role="img"][aria-label*="星"]').first
            aria = await star_el.get_attribute("aria-label")
            stars = stars_from_aria(aria)
            review_time = await el.locator('span[class*="rsqaWe"]').first.inner_text()
            content_el = el.locator('span[class*="wiI7pd"]').first
            content = await content_el.inner_text() if await content_el.count() > 0 else ""

            reviews.append({
                "name": name.strip(), 
                "stars": stars, 
                "time": review_time.strip(), 
                "content": content.strip()
            })
        except:
            continue
    return reviews

async def run_crawler(max_reviews: int, status_cb) -> list[dict]:
    chromium_path = find_chromium()
    async with async_playwright() as p:
        launch_kwargs = dict(
            headless=True,  # 雲端必須為 True
            args=["--lang=zh-TW", "--no-sandbox", "--disable-dev-shm-usage"]
        )
        if chromium_path:
            launch_kwargs["executable_path"] = chromium_path
            
        browser = await p.chromium.launch(**launch_kwargs)
        context = await browser.new_context(
            locale="zh-TW", 
            viewport={"width": 1280, "height": 1000},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        status_cb(-1) # 狀態：正在開啟頁面
        await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
        
        # 處理 Cookie 彈窗
        try:
            btn = page.locator('button[aria-label*="接受全部"], button[aria-label*="同意"]').first
            if await btn.is_visible(timeout=3000): await btn.click()
        except: pass

        # 等待評論區塊出現
        try:
            await page.wait_for_selector('div[data-review-id]', timeout=15000)
        except:
            # 除錯截圖
            await page.screenshot(path="debug_screenshot.png")
            await browser.close()
            return []

        await scroll_to_load(page, max_reviews, status_cb)
        reviews = await parse_reviews(page, max_reviews)
        await browser.close()
    return reviews

def reviews_to_markdown(reviews: list[dict]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# 國立公共資訊圖書館 Google 評論",
        f"> 爬取時間：{now}  ",
        f"> 共收錄 {len(reviews)} 則評論",
        "---",
    ]
    for i, r in enumerate(reviews, 1):
        stars_display = "★" * int(float(r["stars"])) if r["stars"] != "N/A" else "N/A"
        lines += [
            f"## {i}. {r['name']} ({stars_display})",
            f"**時間：** {r['time']}  ",
            f"{r['content'] if r['content'] else '_(無文字評論)_'}",
            "---",
        ]
    return "\n".join(lines)

# ── Streamlit UI ──────────────────────────────────────────────────────────────

st.title("📚 國立公共資訊圖書館 Google 評論爬蟲")
st.caption("自動爬取 Google Maps 上的最新評論，並輸出為 Markdown 格式。")

st.divider()

max_reviews = st.slider("爬取評論數量", min_value=10, max_value=100, value=50, step=10)
start_btn = st.button("🚀 開始爬取", type="primary", use_container_width=True)

if start_btn:
    status_box = st.empty()
    progress_bar = st.progress(0)

    def status_cb(count):
        if count == -1:
            status_box.info("正在開啟直達頁面並處理彈窗...")
        else:
            progress = min(count / max_reviews, 1.0)
            progress_bar.progress(progress)
            status_box.info(f"正在載入評論... 已取得 {count} / {max_reviews} 則")

    with st.spinner("爬蟲執行中，請稍候..."):
        reviews = asyncio.run(run_crawler(max_reviews, status_cb))

    progress_bar.progress(1.0)

    # 顯示除錯截圖（如果失敗的話）
    if not reviews and os.path.exists("debug_screenshot.png"):
        with st.expander("🛠️ 查看爬蟲當下看到的畫面 (除錯用)"):
            st.image("debug_screenshot.png")

    if reviews:
        status_box.success(f"完成！共爬取 {len(reviews)} 則評論。")
        st.divider()
        for i, r in enumerate(reviews, 1):
            stars_int = int(float(r["stars"])) if r["stars"] != "N/A" else 0
            stars_display = "★" * stars_int + "☆" * (5 - stars_int)
            with st.expander(f"#{i}　{r['name']}　{stars_display}　{r['time']}"):
                st.write(r["content"] if r["content"] else "_(無文字評論)_")

        st.divider()
        md_content = reviews_to_markdown(reviews)
        st.download_button(
            label="⬇️ 下載 Nlpi_reviews.md",
            data=md_content.encode("utf-8"),
            file_name="Nlpi_reviews.md",
            mime="text/markdown",
            use_container_width=True,
        )
    else:
        status_box.error("未爬取到任何評論。這通常是因為 Google 暫時封鎖了雲端 IP，請稍後再試。")
