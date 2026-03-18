"""
國立公共資訊圖書館 Google 評論爬蟲
使用 Playwright 處理動態 JavaScript 載入
輸出：Nlpi_reviews.md
"""

import asyncio
import re
from datetime import datetime
from playwright.async_api import async_playwright

# 直接使用「直達評論區」的網址
TARGET_URL = "https://www.google.com/maps/place/%E5%9C%8B%E7%AB%8B%E5%85%AC%E5%85%B1%E8%B3%87%E8%A8%8A%E5%9C%96%E6%9B%B8%E9%A4%A8/@24.1272771,120.6708688,17z/data=!4m7!3m6!1s0x34693d0146d61257:0x7a16000e8eb3abce!8m2!3d24.1272771!4d120.6708688!9m1!1b1"
MAX_REVIEWS = 50
OUTPUT_FILE = "Nlpi_reviews.md"

def stars_from_aria(aria_label: str) -> str:
    match = re.search(r"(\d+(?:\.\d+)?)", aria_label or "")
    return match.group(1) if match else "N/A"

async def scroll_to_load_reviews(page, target_count: int):
    print(f"  [開始] 目標載入 {target_count} 則評論...")
    last_count = 0
    stale_rounds = 0
    while True:
        reviews = page.locator('div[data-review-id]')
        current_count = await reviews.count()
        print(f"  [滾動] 目前已載入 {current_count} 則評論...")
        
        if current_count >= target_count or stale_rounds >= 10: break
        if current_count == last_count: stale_rounds += 1
        else: stale_rounds = 0
        last_count = current_count
        
        try:
            # 確保對準評論區捲動
            await page.mouse.move(300, 500)
            await page.mouse.wheel(0, 3000)
            await page.wait_for_timeout(2000)
            # 強制對齊最後一則評論
            if current_count > 0:
                await reviews.last.scroll_into_view_if_needed()
        except: break

async def parse_reviews(page, max_count: int) -> list[dict]:
    # 點擊「更多」展開長評論
    mores = page.locator('button[aria-label*="查看更多"], button:has-text("更多")')
    for i in range(await mores.count()):
        try:
            m = mores.nth(i)
            if await m.is_visible(): await m.click()
        except: pass
    
    elements = page.locator('div[data-review-id]')
    count = min(await elements.count(), max_count)
    reviews = []
    for i in range(count):
        el = elements.nth(i)
        try:
            name = await el.locator('div[class*="d4r55"]').first.inner_text()
            stars_raw = await el.locator('span[role="img"][aria-label*="星"]').first.get_attribute("aria-label")
            stars = stars_from_aria(stars_raw)
            time = await el.locator('span[class*="rsqaWe"]').first.inner_text()
            content_el = el.locator('span[class*="wiI7pd"]').first
            content = await content_el.inner_text() if await content_el.count() > 0 else "(無文字評論)"
            reviews.append({"name": name.strip(), "stars": stars, "time": time.strip(), "content": content.strip()})
        except: continue
    return reviews

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            locale="zh-TW", 
            viewport={"width": 1280, "height": 1000},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        print("\n[1/3] 直達評論頁面...")
        await page.goto(TARGET_URL, wait_until="domcontentloaded")
        
        # 處理可能的 Cookie 彈窗
        try:
            btn = page.locator('button[aria-label*="接受全部"], button[aria-label*="同意"]').first
            if await btn.is_visible(timeout=3000): await btn.click()
        except: pass

        # 等待評論區塊加載 (data-review-id 是評論的特徵)
        print("[2/3] 等待評論列表加載...")
        try:
            await page.wait_for_selector('div[data-review-id]', timeout=15000)
        except:
            print("  [錯誤] 頁面載入異常。請確認瀏覽器畫面是否為評論列表。")
            await browser.close()
            return

        print(f"[3/3] 執行載入與解析...")
        await scroll_to_load_reviews(page, MAX_REVIEWS)
        reviews = await parse_reviews(page, MAX_REVIEWS)
        
        if reviews:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                f.write(f"# 國立公共資訊圖書館評論\n> 爬取時間: {now}\n> 數量: {len(reviews)}\n\n---\n")
                for i, r in enumerate(reviews, 1):
                    f.write(f"### {i}. {r['name']} ({r['stars']}星)\n{r['content']}\n\n---\n")
            print(f"\n成功！存檔至 {OUTPUT_FILE}")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
