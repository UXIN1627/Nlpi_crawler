"""
國立公共資訊圖書館 Google 評論爬蟲 - Streamlit 介面
"""

import asyncio
import re
import subprocess
import sys
from datetime import datetime

import streamlit as st

# ── 頁面設定 ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="國立公共資訊圖書館 Google 評論爬蟲",
    page_icon="📚",
    layout="centered",
)

TARGET_URL = (
    "https://www.google.com/maps/place/%E5%9C%8B%E7%AB%8B%E5%85%AC%E5%85%B1%E8%B3%87"
    "%E8%A8%8A%E5%9C%96%E6%9B%B8%E9%A4%A8/@24.1232134,120.6673792,15z/data=!4m8!3m7"
    "!1s0x34693d0146d61257:0x7a16000e8eb3abce!8m2!3d24.1272771!4d120.6708688!9m1!1b1"
    "!16s%2Fm%2F010hkjk0?authuser=0&entry=ttu&g_ep=EgoyMDI2MDMxNS4wIKXMDSoASAFQAw%3D%3D"
)


# ── 確保 Playwright Chromium 已安裝 ──────────────────────────────────────────
@st.cache_resource(show_spinner="正在初始化瀏覽器環境（首次約需 1 分鐘）...")
def install_playwright():
    # 安裝系統依賴 + Chromium 執行檔
    result = subprocess.run(
        ["playwright", "install", "--with-deps", "chromium"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # 備用：透過 python -m playwright
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"],
            capture_output=True,
            text=True,
        )


install_playwright()

from playwright.async_api import async_playwright  # noqa: E402


# ── 爬蟲核心邏輯 ─────────────────────────────────────────────────────────────

def stars_from_aria(aria_label: str) -> str:
    match = re.search(r"(\d+(?:\.\d+)?)", aria_label or "")
    return match.group(1) if match else "N/A"


async def close_popups(page):
    selectors = [
        'button[aria-label="接受全部"]', 'button[aria-label="Accept all"]',
        'button:has-text("接受")', 'button:has-text("Accept")',
        'button:has-text("同意")', 'button:has-text("Agree")',
        'button[aria-label="關閉"]', 'button[aria-label="Close"]',
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=1500):
                await btn.click()
                await page.wait_for_timeout(600)
        except Exception:
            pass


async def scroll_to_load(page, target: int, status_cb):
    container = page.locator('div[role="feed"]')
    stale = 0
    last = 0
    while True:
        current = await page.locator('div[data-review-id]').count()
        status_cb(current)
        if current >= target:
            break
        if current == last:
            stale += 1
            if stale >= 5:
                break
        else:
            stale = 0
        last = current
        try:
            await container.evaluate("el => el.scrollTo(0, el.scrollHeight)")
        except Exception:
            await page.keyboard.press("End")
        await page.wait_for_timeout(1500)


async def expand_reviews(page):
    btns = page.locator(
        'button[aria-label="查看更多"], button:has-text("更多"), button[aria-label="See more"]'
    )
    count = await btns.count()
    for i in range(count):
        try:
            b = btns.nth(i)
            if await b.is_visible(timeout=400):
                await b.click()
                await page.wait_for_timeout(150)
        except Exception:
            pass


async def parse_reviews(page, max_count: int) -> list[dict]:
    await expand_reviews(page)
    els = page.locator('div[data-review-id]')
    total = min(await els.count(), max_count)
    reviews = []
    for i in range(total):
        el = els.nth(i)
        try:
            name_el = el.locator('div[class*="d4r55"] span, .d4r55, [class*="WNxzHc"] span').first
            name = (await name_el.inner_text()).strip() if await name_el.count() > 0 else "N/A"

            star_el = el.locator('span[role="img"][aria-label]').first
            aria = await star_el.get_attribute("aria-label") if await star_el.count() > 0 else ""
            stars = stars_from_aria(aria)

            time_el = el.locator('span[class*="rsqaWe"]').first
            review_time = (await time_el.inner_text()).strip() if await time_el.count() > 0 else "N/A"

            content_el = el.locator('span[class*="wiI7pd"]').first
            content = (await content_el.inner_text()).strip() if await content_el.count() > 0 else ""

            reviews.append({"name": name, "stars": stars, "time": review_time, "content": content})
        except Exception:
            continue
    return reviews


async def run_crawler(max_reviews: int, status_cb) -> list[dict]:
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--lang=zh-TW", "--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(locale="zh-TW", viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        status_cb(-1)  # 開啟頁面中
        await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)
        await close_popups(page)

        try:
            await page.wait_for_selector('div[role="feed"]', timeout=15000)
        except Exception:
            pass

        await scroll_to_load(page, max_reviews, status_cb)
        reviews = await parse_reviews(page, max_reviews)
        await browser.close()
    return reviews


def reviews_to_markdown(reviews: list[dict]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# 國立公共資訊圖書館 Google 評論",
        "",
        f"> 爬取時間：{now}  ",
        f"> 共收錄 {len(reviews)} 則評論",
        "",
        "---",
        "",
    ]
    for i, r in enumerate(reviews, 1):
        try:
            stars_display = "★" * int(float(r["stars"]))
        except Exception:
            stars_display = "N/A"
        lines += [
            f"## 評論 {i}",
            "",
            f"**評論者：** {r['name']}  ",
            f"**星等：** {stars_display} ({r['stars']} 顆星)  ",
            f"**時間：** {r['time']}  ",
            "",
            r["content"] if r["content"] else "_(無文字評論)_",
            "",
            "---",
            "",
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
            status_box.info("正在開啟頁面並處理彈窗...")
        else:
            progress = min(count / max_reviews, 1.0)
            progress_bar.progress(progress)
            status_box.info(f"正在載入評論... 已取得 {count} / {max_reviews} 則")

    with st.spinner("爬蟲執行中，請稍候..."):
        reviews = asyncio.run(run_crawler(max_reviews, status_cb))

    progress_bar.progress(1.0)
    status_box.success(f"完成！共爬取 {len(reviews)} 則評論。")

    if reviews:
        st.divider()
        st.subheader(f"評論結果（共 {len(reviews)} 則）")

        for i, r in enumerate(reviews, 1):
            try:
                stars_int = int(float(r["stars"]))
                stars_display = "★" * stars_int + "☆" * (5 - stars_int)
            except Exception:
                stars_display = "N/A"

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
        st.error("未爬取到任何評論，請稍後再試或確認網路連線。")
