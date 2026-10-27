import re
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from redis.asyncio import Redis

from backend.app.config import get_settings
from backend.app.domain.types import AffiliateLinkData, CouponData, NormalizedProduct
from backend.app.services.coupon_parsing import parse_generated_coupon, parse_marketplace_coupon
from backend.app.services.parsing import MercadoLivreAffiliateHubParser
from backend.app.services.redis_lock import DistributedLock
from backend.app.services.urls import is_eligible_product_url, is_official_short_link


class MercadoLivreBrowserService:
    CATEGORIES = (
        "Bebês",
        "Ingressos",
        "Eletrodomésticos",
        "Informática",
        "Arte/Papelaria",
        "Construção",
        "Agro",
        "Pet Shop",
        "Festas",
        "Games",
        "Casa/Móveis/Decoração",
        "Ferramentas",
        "Indústria e Comércio",
        "Brinquedos",
        "Câmeras",
        "Joias",
        "Alimentos",
        "Antiguidades",
        "Calçados/Roupas/Bolsas",
        "Beleza",
        "Saúde",
        "Celulares",
        "Instrumentos",
        "Acessórios para Veículos",
        "Esportes",
        "Eletrônicos",
    )

    def __init__(self, redis: Redis):
        self.settings = get_settings()
        self.redis = redis
        self.parser = MercadoLivreAffiliateHubParser()
        self.discovery_metrics: dict = {}
        self.coupon_products: list[NormalizedProduct] = []
        self.coupon_metrics: dict = {}

    @asynccontextmanager
    async def _context(self):
        async with DistributedLock(self.redis, "lock:meli_browser", ttl=300, wait_timeout=90):
            async with async_playwright() as playwright:
                browser = await playwright.chromium.connect_over_cdp(self.settings.browser_cdp_url)
                if not browser.contexts:
                    raise RuntimeError("Persistent browser context is unavailable")
                yield browser.contexts[0]

    async def authentication_state(self) -> str:
        async with self._context() as context:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(self.settings.meli_hub_url, wait_until="domcontentloaded")
            return await self._detect_auth(page)

    async def discover_offers(self, strategy: str | None = None) -> list[NormalizedProduct]:
        async with self._context() as context:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(self.settings.meli_hub_url, wait_until="domcontentloaded")
            state = await self._detect_auth(page)
            if state != "AUTHENTICATED":
                raise RuntimeError(state)
            try:
                await page.wait_for_load_state("networkidle", timeout=30_000)
            except PlaywrightTimeoutError:
                pass
            selected = await self._next_strategy() if strategy == "auto" else strategy or "relevant"
            applied = await self._apply_strategy(page, selected)
            if not applied:
                self.discovery_metrics = {"requested_strategy": strategy, "applied_strategy": None,
                    "stop_reason": "strategy_unavailable", "auth_state": state}
                raise RuntimeError("STRATEGY_UNAVAILABLE: selection was not applied; no products queued")
            max_items = (
                self.settings.discovery_max_items
                if selected == "relevant"
                else self.settings.discovery_specialized_max_items
            )
            products, metrics = await self._collect_virtualized_products(page, selected, max_items)
            metrics.update(requested_strategy=strategy or "relevant", applied_strategy=selected)
            self.discovery_metrics = metrics
            return products

    async def _collect_virtualized_products(
        self, page: Page, strategy: str, max_items: int
    ) -> tuple[list[NormalizedProduct], dict]:
        started = time.monotonic()
        by_item: dict[str, NormalizedProduct] = {}
        cards_seen = 0
        stable_rounds = 0
        scrolls = 0
        stop_reason = "scroll_limit"
        for round_index in range(self.settings.discovery_max_scrolls + 1):
            state = await self._detect_auth(page)
            if state != "AUTHENTICATED":
                raise RuntimeError(state)
            batch = self.parser.parse(await page.content())
            cards_seen += len(batch)
            before = len(by_item)
            for product in batch:
                product.raw_metadata = {
                    **product.raw_metadata,
                    "discovery_strategy": strategy,
                    "discovery_round": round_index,
                }
                by_item[product.item_id] = product
            stable_rounds = stable_rounds + 1 if len(by_item) == before else 0
            elapsed = time.monotonic() - started
            if len(by_item) >= max_items:
                stop_reason = "item_limit"
                break
            if stable_rounds >= self.settings.discovery_stable_rounds:
                stop_reason = "stable"
                break
            if elapsed >= self.settings.discovery_timeout_seconds:
                stop_reason = "timeout"
                break
            if round_index >= self.settings.discovery_max_scrolls:
                break
            cards = page.locator("li.poly-card, .poly-card")
            if not await cards.count():
                stop_reason = "dom_changed"
                break
            await cards.last.scroll_into_view_if_needed()
            await page.evaluate("window.scrollBy(0, Math.max(window.innerHeight * 0.9, 700))")
            scrolls += 1
            await page.wait_for_timeout(1_200)
            try:
                await page.wait_for_load_state("networkidle", timeout=3_000)
            except PlaywrightTimeoutError:
                pass
        products = list(by_item.values())[:max_items]
        return products, {
            "strategy": strategy,
            "cards_seen": cards_seen,
            "unique_products": len(products),
            "duplicates_discarded": max(cards_seen - len(products), 0),
            "scrolls": scrolls,
            "stable_rounds": stable_rounds,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "stop_reason": stop_reason,
            "auth_state": "AUTHENTICATED",
        }

    async def _next_strategy(self) -> str:
        position = await self.redis.incr("meli:discovery:strategy_cursor")
        cycle = ("relevant", "best_sellers", "extra_commission") + tuple(
            f"category:{name}" for name in self.CATEGORIES
        )
        return cycle[(position - 1) % len(cycle)]

    async def _apply_strategy(self, page: Page, strategy: str) -> bool:
        recommendations = page.locator("#recommendations_card")
        label = {
            "relevant": "Mais relevantes",
            "best_sellers": "Mais vendidos",
            "extra_commission": "Ganhos extras",
        }.get(strategy)
        try:
            if strategy == "relevant":
                # The Hub exposes both a mobile control and a desktop dropdown with
                # this accessible name. The dropdown label already confirms the
                # default sort; clicking it only opens a menu, it does not select it.
                current_sort = recommendations.locator("button.choice-selector__button").filter(
                    has_text=re.compile(r"^Mais relevantes$"))
                if await current_sort.count() == 1:
                    return True
            if strategy.startswith("category:"):
                await recommendations.get_by_role("button", name="Categorias", exact=True).click(
                    timeout=5_000
                )
                matches = page.get_by_text(strategy.removeprefix("category:"), exact=True)
                clicked = False
                for index in range(await matches.count()):
                    option = matches.nth(index)
                    if await option.is_visible():
                        await option.click()
                        clicked = True
                        break
                if not clicked:
                    await page.keyboard.press("Escape")
                    return False
            else:
                option = recommendations.get_by_role("button", name=label, exact=True)
                await option.click(timeout=5_000)
            await page.wait_for_timeout(1_500)
            return True
        except (PlaywrightError, ValueError):
            await page.keyboard.press("Escape")
            return False

    async def revalidate_product(self, product: NormalizedProduct) -> NormalizedProduct | None:
        async with self._context() as context:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(product.product_url, wait_until="domcontentloaded")
            body = (await page.locator("body").inner_text()).lower()
            if any(
                value in body
                for value in ("publicação pausada", "anúncio finalizado", "produto indisponível")
            ):
                return None
            if "captcha" in body or "não sou um robô" in body:
                raise RuntimeError("AUTH_CHALLENGE_REQUIRED")
            facts = await page.evaluate("""() => {
              const scripts = [...document.querySelectorAll('script[type="application/ld+json"]')];
              let product = null;
              for (const node of scripts) {
                try {
                  const value = JSON.parse(node.textContent || '{}');
                  const items = Array.isArray(value) ? value : [value];
                  product = items.find(item => item && (item['@type'] === 'Product' || item.offers));
                  if (product) break;
                } catch (_) {}
              }
              const offer = Array.isArray(product?.offers) ? product.offers[0] : product?.offers;
              const meta = name => document.querySelector(`meta[property="${name}"],meta[name="${name}"],meta[itemprop="${name}"]`)?.content || null;
              return {
                title: product?.name || meta('og:title') || document.querySelector('h1')?.textContent?.trim(),
                image: Array.isArray(product?.image) ? product.image[0] : product?.image || meta('og:image'),
                price: offer?.price || meta('price'),
                currency: offer?.priceCurrency || meta('priceCurrency'),
                availability: offer?.availability || null
              };
            }""")
            availability = str(facts.get("availability") or "").lower()
            if "outofstock" in availability or "discontinued" in availability:
                return None
            price = _decimal(str(facts["price"])) if facts.get("price") else product.current_price
            if price is None:
                return None
            return replace(
                product,
                title=facts.get("title") or product.title,
                image_url=facts.get("image") or product.image_url,
                current_price=price,
                currency=facts.get("currency") or product.currency,
                observed_at=datetime.now(UTC),
                raw_metadata={
                    **product.raw_metadata,
                    "product_page": {
                        "availability": facts.get("availability"),
                        "revalidated_at": datetime.now(UTC).isoformat(),
                    },
                },
            )

    async def list_coupons(self) -> list[CouponData]:
        async with self._context() as context:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(self.settings.meli_coupons_url, wait_until="domcontentloaded")
            state = await self._detect_auth(page)
            if state != "AUTHENTICATED":
                raise RuntimeError(state)
            generated_tab = page.get_by_role("tab", name="Códigos gerados")
            if await generated_tab.count():
                await generated_tab.click()
            await page.locator(".generated-coupon-item").first.wait_for(
                state="visible", timeout=20_000
            )
            cards = page.locator(".generated-coupon-item")
            safe_facts: list[dict] = []
            for index in range(min(await cards.count(), 100)):
                card = cards.nth(index)
                safe_facts.append(
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
                    }
                )

            coupons: list[CouponData] = []
            all_products: dict[str, NormalizedProduct] = {}
            for facts in safe_facts:
                card = page.locator(".generated-coupon-item").filter(has_text=facts["code"]).first
                conditions = await self._coupon_conditions(page, card)
                products, products_url = await self._coupon_eligible_products(
                    context, page, card, facts["code"]
                )
                product_ids = {
                    value.upper()
                    for product in products
                    for value in (product.item_id, product.product_id)
                    if value
                }
                all_products.update({product.item_id: product for product in products})
                coupons.append(
                    parse_generated_coupon(
                        code_text=facts["code"],
                        discount_text=facts["discount"],
                        category_text=facts["category"],
                        expiry_text=facts["expiry"],
                        conditions_text=conditions,
                        budget_text=facts["budget"],
                        eligible_product_ids=sorted(product_ids),
                        eligible_products_count=len(products),
                        products_view_url=products_url,
                    )
                )
            marketplace_coupons = await self._marketplace_coupon_catalog(page)
            coupons.extend(marketplace_coupons)
            self.coupon_products = list(all_products.values())
            self.coupon_metrics = {
                "coupons_found": len(coupons),
                "affiliate_generated_found": len(safe_facts),
                "marketplace_catalog_found": len(marketplace_coupons),
                "eligible_products_unique": len(all_products),
                "eligible_products_total": sum(
                    int(item.raw_metadata.get("eligible_products_count") or 0) for item in coupons
                ),
            }
            return coupons

    async def _marketplace_coupon_catalog(self, page: Page) -> list[CouponData]:
        await page.goto(self.settings.meli_marketplace_coupons_url, wait_until="domcontentloaded")
        try:
            await page.locator(".coupon-card").first.wait_for(state="visible", timeout=20_000)
        except PlaywrightTimeoutError:
            return []
        by_id: dict[str, CouponData] = {}
        category_controls = page.locator("[role='tab'], button").filter(
            has_text=re.compile(
                r"Recomendados|Eletrônicos|Áudio|Casa|Moda|Beleza|Supermercado|Esportes|Veículos",
                re.I,
            )
        )
        passes = max(1, min(await category_controls.count(), 20))
        for category_index in range(passes):
            if category_index:
                control = category_controls.nth(category_index)
                if await control.is_visible():
                    try:
                        await control.click(timeout=3_000)
                        await page.wait_for_timeout(700)
                    except PlaywrightTimeoutError:
                        pass
            cards = page.locator(".coupon-card")
            for index in range(min(await cards.count(), 150)):
                try:
                    card = cards.nth(index)
                    card_text = await self._optional_text(card, wait_ms=2_000)
                    if not card_text:
                        continue
                    title_locator = card.locator(".title").first
                    title = await self._optional_text(title_locator)
                    title = title.strip()
                    if not title:
                        continue
                    subtitles = await card.locator(".subtitle").all_inner_texts()
                    scope = subtitles[0].strip() if subtitles else ""
                    minimum = next(
                        (value for value in subtitles if "compra mínima" in value.casefold()),
                        None,
                    )
                    maximum = next(
                        (value for value in subtitles if "limite" in value.casefold()), None
                    )
                    expiry = await self._optional_text(card.locator(".expiration-text").first)
                    if not expiry:
                        expiry_match = re.search(
                            r"(?:vence|válido)\s+(?:em|até)?\s*\d{1,2}\s+de\s+[a-zç]+",
                            card_text,
                            re.I,
                        )
                        expiry = expiry_match.group(0) if expiry_match else ""
                    action = ""
                    action_button = card.locator("button.andes-button").last
                    if await action_button.count():
                        action = await self._optional_text(action_button)
                    conditions = await self._marketplace_coupon_conditions(page, card)
                    coupon = parse_marketplace_coupon(
                        title_text=title,
                        scope_text=scope,
                        minimum_text=minimum,
                        maximum_text=maximum,
                        expiry_text=expiry,
                        action_text=action,
                        conditions_text=conditions,
                    )
                    by_id[coupon.marketplace_coupon_id] = coupon
                except PlaywrightError:
                    await page.keyboard.press("Escape")
                    continue
        return list(by_id.values())

    @staticmethod
    async def _optional_text(locator, wait_ms: int = 1_500) -> str:
        try:
            if not await locator.count():
                return ""
            return (await locator.inner_text(timeout=wait_ms)).strip()
        except PlaywrightTimeoutError:
            return ""

    async def _marketplace_coupon_conditions(self, page: Page, card) -> str:
        trigger = card.locator(
            '[data-testid="info-container"] [role="button"], [aria-label^="Termos e condições"]'
        ).first
        if not await trigger.count():
            return ""
        try:
            await trigger.click()
            dialog = page.locator(".andes-modal:visible, [role='dialog']:visible").last
            await dialog.wait_for(state="visible", timeout=3_000)
            text = (await dialog.inner_text()).strip()[:4_000]
            close = dialog.get_by_role("button", name=re.compile(r"Fechar", re.I))
            if await close.count():
                await close.first.click()
            else:
                await page.keyboard.press("Escape")
            return text
        except PlaywrightTimeoutError:
            await page.keyboard.press("Escape")
            return ""

    async def _coupon_conditions(self, page: Page, card) -> str:
        try:
            await card.locator(".generated-coupon-item__modal-trigger").click()
            dialog = page.locator(".andes-modal:visible, [role='dialog']:visible").last
            await dialog.wait_for(state="visible", timeout=5_000)
            text = (await dialog.inner_text()).strip()
            close = dialog.get_by_role("button", name=re.compile(r"Fechar", re.I))
            if await close.count():
                await close.first.click()
            else:
                await page.keyboard.press("Escape")
            return text[:4_000]
        except PlaywrightTimeoutError:
            await page.keyboard.press("Escape")
            return ""

    async def _coupon_eligible_products(
        self, context, coupons_page: Page, card, code: str
    ) -> tuple[list[NormalizedProduct], str | None]:
        target: Page | None = None
        try:
            async with context.expect_page(timeout=7_000) as page_info:
                await card.locator(".generated-coupon-item__link").click()
            target = await page_info.value
        except PlaywrightTimeoutError:
            if coupons_page.url != self.settings.meli_coupons_url:
                target = coupons_page
        if target is None:
            return [], None
        try:
            await target.wait_for_load_state("domcontentloaded", timeout=20_000)
            products = await self._collect_coupon_product_pages(target, code)
            return products, target.url
        finally:
            if target is not coupons_page:
                await target.close()

    async def _collect_coupon_product_pages(self, page: Page, code: str) -> list[NormalizedProduct]:
        started = time.monotonic()
        by_item: dict[str, NormalizedProduct] = {}
        pages = 0
        while len(by_item) < self.settings.discovery_max_items and pages < 20:
            try:
                await page.locator("li.poly-card, .poly-card").first.wait_for(
                    state="visible", timeout=15_000
                )
            except PlaywrightTimeoutError:
                break
            cards = page.locator("li.poly-card, .poly-card")
            if await cards.count():
                await cards.last.scroll_into_view_if_needed()
                await page.wait_for_timeout(800)
            for product in self.parser.parse(await page.content()):
                product.source = "official_coupon_products"
                product.raw_metadata = {
                    **product.raw_metadata,
                    "discovery_strategy": f"coupon:{code.lstrip('#')}",
                    "coupon_applicability": "official_products_view",
                }
                by_item[product.item_id] = product
            pages += 1
            if (
                len(by_item) >= self.settings.discovery_max_items
                or time.monotonic() - started >= self.settings.discovery_timeout_seconds
            ):
                break
            next_button = page.locator(
                "li.andes-pagination__button--next:not(.andes-pagination__button--disabled) a, "
                "a[title='Seguinte']:not([data-andes-state='disabled'])"
            ).first
            if not await next_button.count():
                break
            previous_url = page.url
            await next_button.click()
            try:
                await page.wait_for_url(
                    lambda url, old=previous_url: str(url) != old, timeout=10_000
                )
            except PlaywrightTimeoutError:
                await page.wait_for_timeout(1_500)
        return list(by_item.values())[: self.settings.discovery_max_items]

    async def generate_coupon(self, coupon: CouponData) -> CouponData:
        """Generate a coupon only after an operator-selected, confirmed card is found."""
        if not coupon.eligibility_confirmed:
            raise ValueError("Coupon eligibility must be confirmed before generating a code")
        async with self._context() as context:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(self.settings.meli_coupons_url, wait_until="domcontentloaded")
            state = await self._detect_auth(page)
            if state != "AUTHENTICATED":
                raise RuntimeError(state)
            card = page.get_by_text(coupon.title, exact=False).first.locator(
                "xpath=ancestor::*[self::article or contains(@class,'andes-card')][1]"
            )
            if not await card.count():
                raise RuntimeError("Confirmed coupon card was not found")
            await card.get_by_role("button", name=re.compile(r"Gerar|Ativar", re.I)).click()
            code_locator = page.locator(
                "input[readonly][value], [data-testid*='coupon-code'], code"
            ).first
            await code_locator.wait_for(state="visible", timeout=20_000)
            code = (
                await code_locator.get_attribute("value") or await code_locator.inner_text()
            ).strip()
            if not code:
                raise RuntimeError("Coupon generation did not return a code")
            coupon.code = code
            return coupon

    async def generate_affiliate_link(self, product_url: str, tag: str) -> AffiliateLinkData:
        if not is_eligible_product_url(product_url):
            raise ValueError("Only eligible individual product URLs are accepted")
        last_error: Exception | None = None
        for _ in range(3):
            try:
                return await self._generate_with_link_builder(product_url, tag)
            except (RuntimeError, PlaywrightTimeoutError) as exc:
                if str(exc) in {"AUTH_REQUIRED", "AUTH_CHALLENGE_REQUIRED", "SESSION_EXPIRED"}:
                    raise
                last_error = exc
        raise RuntimeError(
            f"Official Link Builder failed after three attempts: {type(last_error).__name__}"
        ) from last_error

    async def _generate_with_link_builder(self, product_url: str, tag: str) -> AffiliateLinkData:
        async with self._context() as context:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(self.settings.meli_link_builder_url, wait_until="domcontentloaded")
            state = await self._detect_auth(page)
            if state != "AUTHENTICATED":
                raise RuntimeError(state)
            textarea = page.locator("textarea#url-0, textarea[id^='url-']").first
            await textarea.wait_for(state="visible", timeout=30_000)
            await textarea.fill(product_url)
            tracking_tag = await self._select_tracking_tag(page, tag)
            await page.get_by_role("button", name=re.compile(r"^Gerar$", re.I)).click()
            result = page.locator("textarea[id^='textfield-copyLink-']").first
            await result.wait_for(state="visible", timeout=30_000)
            value = (await result.input_value()).strip()
            if not is_official_short_link(value):
                raise RuntimeError("Link Builder did not return an official short link")
            short_radio = page.get_by_role("radio", name=re.compile(r"Link curto", re.I))
            if await short_radio.count() and not await short_radio.first.is_checked():
                raise RuntimeError("Link Builder result is not marked as a short link")
            return AffiliateLinkData(
                short_url=value,
                tracking_tag=tracking_tag,
                evidence={
                    "version": "linkbuilder-v2",
                    "page": "/afiliados/linkbuilder",
                    "input_selector": "textarea#url-0",
                    "result_selector": "textarea[id^='textfield-copyLink-']",
                    "link_type": "short",
                    "requested_tag": tag,
                    "selected_tag": tracking_tag,
                    "generated_at": datetime.now(UTC).isoformat(),
                    "short_link_opened": False,
                },
            )

    async def _select_tracking_tag(self, page: Page, requested_tag: str) -> str:
        selector = page.get_by_role(
            "combobox", name=re.compile(r"Selecione sua etiqueta", re.I)
        ).first
        if not await selector.count():
            raise RuntimeError("Link Builder tracking-tag selector was not found")
        current = " ".join((await selector.inner_text()).split())
        if requested_tag and requested_tag.casefold() not in current.casefold():
            await selector.click()
            option = page.get_by_text(requested_tag, exact=True)
            if await option.count():
                await option.first.click()
                current = " ".join((await selector.inner_text()).split())
            else:
                await page.keyboard.press("Escape")
        selected = _tracking_tag_from_text(current)
        if not selected:
            body = await page.locator("body").inner_text()
            match = re.search(r"Etiqueta em uso:\s*([^\n]+)", body, re.I)
            selected = match.group(1).strip() if match else ""
        if not selected:
            raise RuntimeError("Link Builder has no active tracking tag")
        return selected

    async def _detect_auth(self, page: Page) -> str:
        url = page.url.lower()
        content = (await page.locator("body").inner_text()).lower()
        if "captcha" in content or "não sou um robô" in content or "challenge" in url:
            return "AUTH_CHALLENGE_REQUIRED"
        if "login" in url or "entre na sua conta" in content or "iniciar sessão" in content:
            return "AUTH_REQUIRED"
        if "afiliados" not in url:
            return "SESSION_EXPIRED"
        return "AUTHENTICATED"


def _decimal(value: str) -> Decimal:
    clean = value.strip()
    if "," in clean:
        clean = clean.replace(".", "").replace(",", ".")
    return Decimal(clean)


def _tracking_tag_from_text(value: str) -> str:
    clean = " ".join(value.split())
    clean = re.sub(r"^Etiqueta em uso:\s*", "", clean, flags=re.I)
    clean = re.sub(r"^Selecione sua etiqueta\s*", "", clean, flags=re.I)
    return clean.strip(" :-")
