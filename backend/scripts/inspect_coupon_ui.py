"""Inspect only safe, visible coupon UI facts from the authenticated browser."""

import asyncio
import json

from playwright.async_api import async_playwright

from backend.app.config import get_settings


async def main() -> None:
    settings = get_settings()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(settings.browser_cdp_url)
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(settings.meli_coupons_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(2_000)
        generated_tab = page.get_by_role("tab", name="Códigos gerados")
        if await generated_tab.count():
            await generated_tab.click()
            await page.wait_for_timeout(1_000)
        cards = page.locator(".generated-coupon-item")
        output: dict = {
            "url": page.url,
            "title": await page.title(),
            "tabs": await page.get_by_role("tab").all_inner_texts(),
            "count": await cards.count(),
            "cards": [],
        }
        for index in range(min(await cards.count(), 5)):
            card = cards.nth(index)
            link = card.locator(".generated-coupon-item__link").first
            output["cards"].append(
                {
                    "code": (await card.locator("b").first.inner_text()).strip(),
                    "discount": (
                        await card.locator(
                            ".generated-coupon-item__inner-details-title"
                        ).inner_text()
                    ).strip(),
                    "category": (
                        await card.locator(".generated-coupon-item__category").inner_text()
                    ).strip(),
                    "expiry": (
                        await card.locator(".generated-coupon-item__expiration").inner_text()
                    ).strip(),
                    "budget": (
                        await card.locator(".generated-coupon-item__budget").inner_text()
                    ).strip(),
                    "link_attributes": await link.evaluate(
                        "el => Object.fromEntries([...el.attributes].map(a => [a.name, a.value]))"
                    ),
                }
            )
        if await cards.count():
            card = cards.first
            conditions = card.locator(".generated-coupon-item__modal-trigger")
            await conditions.click()
            await page.wait_for_timeout(500)
            dialogs = page.locator("[role='dialog'], .andes-modal")
            output["conditions_dialog"] = (
                (await dialogs.last.inner_text()).strip()[:2_000] if await dialogs.count() else None
            )
            close = page.get_by_role("button", name="Fechar")
            if await close.count():
                await close.last.click()
            else:
                await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
            before_pages = len(context.pages)
            await card.locator(".generated-coupon-item__link").click()
            await page.wait_for_timeout(1_500)
            target = context.pages[-1]
            visible_dialogs = target.locator("[role='dialog']:visible, .andes-modal:visible")
            product_links = target.locator("a[href*='/p/MLB'], a[href*='item_id=MLB']")
            output["products_flow"] = {
                "new_page": len(context.pages) > before_pages,
                "url": target.url,
                "title": await target.title(),
                "product_cards": await target.locator("li.poly-card, .poly-card").count(),
                "product_ids": list(
                    dict.fromkeys(
                        await target.locator("a[href*='MLB']").evaluate_all(
                            "els => els.map(el => (el.href.match(/MLB\\d+/) || [null])[0]).filter(Boolean)"
                        )
                    )
                )[:10],
                "dialogs": await visible_dialogs.count(),
                "dialog_text": (
                    (await visible_dialogs.last.inner_text()).strip()[:3_000]
                    if await visible_dialogs.count()
                    else None
                ),
                "product_link_count": await product_links.count(),
                "product_urls": list(
                    dict.fromkeys(await product_links.evaluate_all("els => els.map(el => el.href)"))
                )[:10],
            }
        print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
