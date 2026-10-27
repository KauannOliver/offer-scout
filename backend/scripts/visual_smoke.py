import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

from backend.app.config import get_settings


async def main() -> None:
    settings = get_settings()
    errors: list[str] = []
    output = Path("/data/artifacts")
    output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1440, "height": 1000})
        page = await context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto("http://affiliate-frontend", wait_until="networkidle")
        await page.locator('input[autocomplete="username"]').fill(settings.admin_username)
        await page.locator('input[autocomplete="current-password"]').fill(settings.admin_password)
        await page.get_by_role("button", name="Entrar").click()
        await page.get_by_role("heading", name="Visão geral").wait_for()
        page.on(
            "console",
            lambda message: errors.append(message.text) if message.type == "error" else None,
        )
        await page.screenshot(path=output / "dashboard-overview.png", full_page=True)
        await page.get_by_role("button", name="Operações").click()
        await page.get_by_role("heading", name="Central de operações").wait_for()
        await page.get_by_text("database", exact=True).wait_for()
        await page.screenshot(path=output / "dashboard-operations.png", full_page=True)
        await page.set_viewport_size({"width": 390, "height": 844})
        await page.get_by_role("button", name="Oportunidades").click()
        await page.get_by_role("heading", name="Oportunidades").wait_for()
        await page.screenshot(path=output / "dashboard-opportunities-mobile.png", full_page=True)
        layout = await page.evaluate("""() => ({
          horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
          cardOverlap: [...document.querySelectorAll('article.grid')].some(card => {
            const children = card.children;
            if (children.length < 2) return false;
            return children[0].getBoundingClientRect().bottom > children[1].getBoundingClientRect().top + 1;
          }),
          textLength: document.body.innerText.trim().length,
          opportunityCards: document.querySelectorAll('article.grid').length
        })""")
        opportunity_text = await page.locator("body").inner_text()
        await page.set_viewport_size({"width": 1440, "height": 1000})
        await page.get_by_role("button", name="Fila operacional").click()
        await page.get_by_role("heading", name="Fila operacional").wait_for()
        await page.wait_for_timeout(500)
        queue_layout = await page.evaluate("""() => ({
          uuidAsTitle: [...document.querySelectorAll('article h3')].some(node => /^[0-9a-f]{8}-[0-9a-f-]{27}$/i.test(node.textContent.trim())),
          brokenImages: [...document.querySelectorAll('article img')].filter(image => image.complete && image.naturalWidth === 0).length,
          cards: document.querySelectorAll('article').length
        })""")
        await page.screenshot(path=output / "dashboard-queue.png", full_page=True)
        await browser.close()
    result = {
        "errors": errors,
        "layout": layout,
        "queue": queue_layout,
        "totalVisible": "monitoradas" in opportunity_text,
    }
    print(json.dumps(result, ensure_ascii=False))
    if (
        errors
        or layout["horizontalOverflow"]
        or layout["cardOverlap"]
        or layout["textLength"] < 100
        or queue_layout["uuidAsTitle"]
        or queue_layout["brokenImages"]
        or not result["totalVisible"]
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
