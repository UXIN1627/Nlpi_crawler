"""
國立公共資訊圖書館 Google 評論爬蟲 - Streamlit 介面 (穩定快取版)
"""

import asyncio
import re
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

# 使用直達評論區網址
TARGET_URL = "https://www.google.com/maps/place/%E5%9C%8B%E7%AB%8B%E5%85%AC%E5%85%B1%E8%B3%87%E8%A8%8A%E5%9C%96%E6%9B%B8%E9%A4%A8/@24.1272771,120.6708688,17z/data=!4m7!3m6!1s0x34693d0146d61257:0x7a16000e8eb3abce!8m2!3d24.1272771!4d120.6708688!9m1!1b1"

# ── 系統工具 ────────────────────────────────────────────────────────────────
def find_chromium() -> str | None:
    candidates = ["/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"]
    for path in candidates:
        if os.path.exists(path): return path
    return None

def stars_from_aria(aria_label: str) -> str:
    match = re.search(r"(\d+(?:\.\d+)?)", aria_label or "")
    return match.group(1) if match else "N/A"

# ── 爬蟲邏輯 ────────────────────────────────────────────────────────────────

async def scroll_to_load(page, target: int, status_cb):
    last_count = 0
    stale_rounds = 0
    while True:
        reviews = page.locator('div[data-review-id]')
        current_count = await reviews.count()
        status_cb(current_count)
        
        if current_count >= target or stale_rounds >= 10: break
        if current_count == last_count: stale_rounds += 1
        else: stale_rounds = 0
        last_count = current_count
        
        try:
            await page.mouse.move(300, 500)
            await page.mouse.wheel(0, 3000)
            if current_count > 0:
                await reviews.last.scroll_into_view_if_needed()
            # 💡 加入隨機延遲避開封鎖
            await page.wait_for_timeout(random.randint(2000, 4000))
        except: break

async def parse_reviews(page, max_count: int) -> list[dict]:
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
            aria = await el.locator('span[role="img"][aria-label*="星"]').first.get_attribute("aria-label")
            stars = stars_from_aria(aria)
            review_time = await el.locator('span[class*="rsqaWe"]').first.inner_text()
            content_el = el.locator('span[class*="wiI7pd"]').first
            content = await content_el.inner_text() if await content_el.count() > 0 else ""
            reviews.append({"name": name.strip(), "stars": stars, "time": review_time.strip(), "content": content.strip()})
        except: continue
    return reviews

async def run_crawler(max_reviews: int, status_cb) -> list[dict]:
    chromium_path = find_chromium()
    async with async_playwright() as p:
        launch_kwargs = dict(headless=True, args=["--lang=zh-TW", "--no-sandbox", "--disable-dev-shm-usage"])
        if chromium_path: launch_kwargs["executable_path"] = chromium_path
        browser = await p.chromium.launch(**launch_kwargs)
        context = await browser.new_context(locale="zh-TW", viewport={"width": 1280, "height": 1000})
        page = await context.new_page()

        status_cb(-1)
        await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(random.randint(2000, 4000))
        
        try:
            btn = page.locator('button[aria-label*="接受全部"], button[aria-label*="同意"]').first
            if await btn.is_visible(timeout=3000): await btn.click()
        except: pass

        try:
            await page.wait_for_selector('div[data-review-id]', timeout=15000)
            await scroll_to_load(page, max_reviews, status_cb)
            reviews = await parse_reviews(page, max_reviews)
        except:
            await page.screenshot(path="debug_screenshot.png")
            reviews = []
        
        await browser.close()
    return reviews

# ── 💡 快取邏輯 (設定 7 天) ──────────────────────────────────────────────────

# ttl=604800 秒 = 7 天
@st.cache_data(ttl=604800, show_spinner=False)
def get_master_cache():
    """
    永遠嘗試爬取上限 50 筆。
    如果成功，這 50 筆就是未來 7 天的『主資料庫』。
    """
    def dummy_cb(count): pass
    # 這裡固定寫死 50
    results = asyncio.run(run_crawler(50, dummy_cb))
    
    if not results:
        # 爬取失敗時報錯，確保不覆蓋掉舊的成功快取
        raise RuntimeError("Crawl failed")
    
    return results

# ── Streamlit UI ──────────────────────────────────────────────────────────────

st.title("📚 國立公共資訊圖書館 Google 評論爬蟲")
st.caption("自動爬取 Google Maps 上的最新評論，並輸出為 Markdown 格式。")

st.divider()

# 1. 將上限設定為 50
max_reviews = st.slider("爬取評論數量", min_value=10, max_value=50, value=50, step=10)
start_btn = st.button("🚀 開始爬取", type="primary", use_container_width=True)

if start_btn:
    status_box = st.empty()
    
    try:
        with st.spinner("正在取得評論... (優先嘗試更新，失敗則自動使用 7 天內快取)"):
            # 1. 取得主快取 (內含 50 筆)
            master_reviews = get_master_cache()
            
            # 2. 根據使用者在 Slider 選的數量 (max_reviews) 進行切片
            # 例如使用者選 30，就從 50 筆快取中拿前 30 筆
            reviews = master_reviews[:max_reviews]
            
            status_box.success(f"成功！已從資料庫提取前 {len(reviews)} 則評論。")
    except Exception:
        reviews = []
        status_box.error("目前 Google 封鎖了雲端存取，且系統中尚無 7 天內的成功快取紀錄。")
        if os.path.exists("debug_screenshot.png"):
            with st.expander("🛠️ 查看被擋下的畫面"):
                st.image("debug_screenshot.png")

    if reviews:
        st.divider()
        for i, r in enumerate(reviews, 1):
            stars_int = int(float(r["stars"])) if r["stars"] != "N/A" else 0
            stars_display = "★" * stars_int + "☆" * (5 - stars_int)
            with st.expander(f"#{i}　{r['name']}　{stars_display}　{r['time']}"):
                st.write(r["content"] if r["content"] else "_(無文字評論)_")

        st.divider()
        # 下載按鈕邏輯
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        md_lines = [f"# 國資圖評論\n> 時間：{now}\n> 數量：{len(reviews)}\n---"]
        for i, r in enumerate(reviews, 1):
            md_lines.append(f"### {i}. {r['name']}\n{r['content']}\n---")
        
        st.download_button(
            label="⬇️ 下載 Markdown 檔案",
            data="\n".join(md_lines).encode("utf-8"),
            file_name="Nlpi_reviews.md",
            mime="text/markdown",
            use_container_width=True,
        )
