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
    # 這個網址帶有 !9m1!1b1 參數，理論上會直接開啟「所有評論」面板
TARGET_URL = "https://www.google.com/maps/place/%E5%9C%8B%E7%AB%8B%E5%85%AC%E5%85%B1%E8%B3%87%E8%A8%8A%E5%9C%96%E6%9B%B8%E9%A4%A8/@24.1272771,120.6708688,17z/data=!4m8!3m7!1s0x34693d0146d61257:0x7a16000e8eb3abce!8m2!3d24.1272771!4d120.6708688!9m1!1b1"
)


from playwright.async_api import async_playwright  # noqa: E402

# ── 找出系統 Chromium 路徑 ────────────────────────────────────────────────────
def find_chromium() -> str | None:
    """依序尋找系統上可用的 Chromium 執行檔路徑"""
    candidates = [
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ]
    for path in candidates:
        if subprocess.run(["test", "-f", path], capture_output=True).returncode == 0:
            return path
    return None


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
    chromium_path = find_chromium()
    async with async_playwright() as p:
        launch_kwargs = dict(
            headless=True,
            args=[
                "--lang=zh-TW", 
                "--no-sandbox", 
                "--disable-dev-shm-usage",
                "--disable-gpu", 
                "--single-process",
                "--disable-blink-features=AutomationControlled" 
            ],
        )
        if chromium_path:
            launch_kwargs["executable_path"] = chromium_path
            
        browser = await p.chromium.launch(**launch_kwargs)
        context = await browser.new_context(
            locale="zh-TW", 
            viewport={"width": 1280, "height": 1000},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        status_cb(-1) 
        
        # 1. 直接前往目標 Place 頁面
        try:
            # 使用 commit 確保只要開始接收資料就繼續，不乾等
            await page.goto(TARGET_URL, wait_until="commit", timeout=60000)
            await page.wait_for_timeout(7000) # 給予充足的渲染時間
            await close_popups(page)
        except Exception:
            pass

        # 2. 【強力 JS 點擊】尋找「評論」按鈕
        # 不管 Class 是什麼，直接找文字包含 "評論" 或 "Reviews" 的按鈕
        await page.evaluate('''() => {
            const btns = Array.from(document.querySelectorAll('button, div, span'));
            const target = btns.find(b => 
                (b.innerText && (b.innerText.includes('評論') || b.innerText.includes('Reviews'))) ||
                (b.getAttribute('aria-label') && b.getAttribute('aria-label').includes('評論'))
            );
            if (target) {
                target.click();
            }
        }''')
        await page.wait_for_timeout(3000)

        # 3. 備援邏輯：如果還是沒出現側欄，嘗試按一次搜尋鍵（對應網址參數）
        if await page.locator('div[role="feed"]').count() == 0:
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(5000)

        # 4. 截圖檢查（現在應該要有側欄了）
        await page.screenshot(path="debug_screenshot.png")

        # 5. 等待並滾動
        try:
            # 這是評論清單的特徵
            await page.wait_for_selector('div[data-review-id]', timeout=15000)
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

    # 執行爬蟲
    with st.spinner("爬蟲執行中，請稍候..."):
        reviews = asyncio.run(run_crawler(max_reviews, status_cb))

    progress_bar.progress(1.0)

    # --- 💡 顯示除錯截圖 ---
    import os
    if os.path.exists("debug_screenshot.png"):
        with st.expander("🛠️ 查看爬蟲當下看到的畫面 (除錯用)"):
            st.image("debug_screenshot.png")
    # --------------------

    if reviews:
        status_box.success(f"完成！共爬取 {len(reviews)} 則評論。")
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
        status_box.error("完成，但未爬取到任何評論。")
        st.error("未爬取到任何評論，請查看上方的「除錯用」畫面確認原因。")
