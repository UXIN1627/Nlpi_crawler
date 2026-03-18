"""
國立公共資訊圖書館 Google 評論爬蟲
使用 Playwright 處理動態 JavaScript 載入
輸出：Nlpi_reviews.md
"""

import asyncio
import re
from datetime import datetime
from playwright.async_api import async_playwright

TARGET_URL = (
    "https://www.google.com/maps/place/%E5%9C%8B%E7%AB%8B%E5%85%AC%E5%85%B1%E8%B3%87"
    "%E8%A8%8A%E5%9C%96%E6%9B%B8%E9%A4%A8/@24.1232134,120.6673792,15z/data=!4m8!3m7"
    "!1s0x34693d0146d61257:0x7a16000e8eb3abce!8m2!3d24.1272771!4d120.6708688!9m1!1b1"
    "!16s%2Fm%2F010hkjk0?authuser=0&entry=ttu&g_ep=EgoyMDI2MDMxNS4wIKXMDSoASAFQAw%3D%3D"
)

MAX_REVIEWS = 50
OUTPUT_FILE = "Nlpi_reviews.md"


def stars_from_aria(aria_label: str) -> str:
    """從 aria-label 解析星等數字，例如 '5 顆星' -> '5'"""
    match = re.search(r"(\d+(?:\.\d+)?)", aria_label or "")
    return match.group(1) if match else "N/A"


async def close_popups(page):
    """嘗試關閉常見彈窗與 Cookie 同意按鈕"""
    popup_selectors = [
        # Google 同意條款 / Cookie 彈窗
        'button[aria-label="接受全部"]',
        'button[aria-label="Accept all"]',
        'button:has-text("接受")',
        'button:has-text("Accept")',
        'button:has-text("同意")',
        'button:has-text("Agree")',
        'button:has-text("我同意")',
        # 關閉按鈕
        'button[aria-label="關閉"]',
        'button[aria-label="Close"]',
    ]
    for selector in popup_selectors:
        try:
            btn = page.locator(selector).first
            if await btn.is_visible(timeout=2000):
                await btn.click()
                await page.wait_for_timeout(800)
                print(f"  [彈窗] 已點擊: {selector}")
        except Exception:
            pass


async def expand_all_reviews(page):
    """點擊所有「更多」按鈕以展開完整評論內容"""
    more_btns = page.locator('button[aria-label="查看更多"], button:has-text("更多"), button[aria-label="See more"]')
    count = await more_btns.count()
    for i in range(count):
        try:
            btn = more_btns.nth(i)
            if await btn.is_visible(timeout=500):
                await btn.click()
                await page.wait_for_timeout(200)
        except Exception:
            pass


async def scroll_to_load_reviews(page, target_count: int):
    """在評論面板內自動向下滾動，直到載入足夠的評論"""
    # 找到評論的捲動容器
    scroll_container = page.locator('div[role="feed"]')

    last_count = 0
    stale_rounds = 0

    while True:
        # 目前已載入的評論數量
        reviews = page.locator('div[data-review-id]')
        current_count = await reviews.count()
        print(f"  [滾動] 目前已載入 {current_count} 則評論...")

        if current_count >= target_count:
            print(f"  [滾動] 已達目標數量 {target_count}，停止滾動。")
            break

        if current_count == last_count:
            stale_rounds += 1
            if stale_rounds >= 5:
                print("  [滾動] 連續 5 次無新評論，判斷已達底部。")
                break
        else:
            stale_rounds = 0

        last_count = current_count

        # 滾動到容器底部
        try:
            await scroll_container.evaluate("el => el.scrollTo(0, el.scrollHeight)")
        except Exception:
            # 備用：用鍵盤 End 鍵滾動
            await page.keyboard.press("End")

        await page.wait_for_timeout(1500)


async def parse_reviews(page, max_count: int) -> list[dict]:
    """解析頁面上的評論資料"""
    await expand_all_reviews(page)

    review_elements = page.locator('div[data-review-id]')
    total = min(await review_elements.count(), max_count)
    print(f"  [解析] 共解析 {total} 則評論")

    reviews = []
    for i in range(total):
        el = review_elements.nth(i)
        try:
            # 評論者姓名
            name_el = el.locator('div[class*="d4r55"] span, .d4r55, [class*="WNxzHc"] span').first
            name = (await name_el.inner_text()).strip() if await name_el.count() > 0 else "N/A"

            # 星等：從 aria-label 取得
            star_el = el.locator('span[role="img"][aria-label]').first
            aria = await star_el.get_attribute("aria-label") if await star_el.count() > 0 else ""
            stars = stars_from_aria(aria)

            # 評論時間
            time_el = el.locator('span[class*="rsqaWe"]').first
            review_time = (await time_el.inner_text()).strip() if await time_el.count() > 0 else "N/A"

            # 評論內容
            content_el = el.locator('span[class*="wiI7pd"]').first
            content = (await content_el.inner_text()).strip() if await content_el.count() > 0 else ""

            reviews.append({
                "name": name,
                "stars": stars,
                "time": review_time,
                "content": content,
            })
        except Exception as e:
            print(f"  [警告] 第 {i+1} 則評論解析失敗: {e}")
            continue

    return reviews


def save_as_markdown(reviews: list[dict], filepath: str):
    """將評論儲存為 Markdown 檔案"""
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
        stars_display = "★" * int(float(r["stars"])) if r["stars"] != "N/A" else "N/A"
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

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"  [輸出] 已儲存至 {filepath}")


async def main():
    print("=" * 55)
    print("  國立公共資訊圖書館 Google 評論爬蟲")
    print("=" * 55)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,  # 設為 True 可無視窗執行
            args=["--lang=zh-TW"],
        )
        context = await browser.new_context(
            locale="zh-TW",
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()

        print(f"\n[1/5] 開啟目標頁面...")
        await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)

        print("[2/5] 處理彈窗...")
        await close_popups(page)

        # 等待評論區載入
        print("[3/5] 等待評論面板載入...")
        try:
            await page.wait_for_selector('div[role="feed"]', timeout=15000)
        except Exception:
            print("  [警告] 未偵測到評論面板，嘗試繼續...")

        print(f"[4/5] 自動滾動以載入 {MAX_REVIEWS} 則評論...")
        await scroll_to_load_reviews(page, MAX_REVIEWS)

        print("[5/5] 解析評論資料...")
        reviews = await parse_reviews(page, MAX_REVIEWS)

        await browser.close()

    if reviews:
        save_as_markdown(reviews, OUTPUT_FILE)
        print(f"\n完成！共爬取 {len(reviews)} 則評論，已儲存至 {OUTPUT_FILE}")
    else:
        print("\n[錯誤] 未爬取到任何評論，請確認頁面結構是否改變。")


if __name__ == "__main__":
    asyncio.run(main())
