import { test, expect, type Page } from "@playwright/test";

async function login(page: Page) {
  await expect.poll(async () => (await page.request.get("/health")).status()).toBe(200);
  await page.goto("/settings");
  await page.getByLabel("Usuário", { exact: true }).fill("verification_operator");
  await page.getByLabel("Senha", { exact: true }).fill("verification-local-only");
  await page.getByRole("button", {name: "Entrar", exact: true}).click();
  await expect(page.getByRole("heading", {name: "Configurações", exact: true})).toBeVisible({timeout: 15000});
}

async function noHorizontalOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth+1)).toBeTruthy();
}

test("configurações, validação e simulação não alteram produção", async ({page}) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await login(page);
  await page.getByRole("button", {name: "Banco de dados", exact: true}).click();
  await expect(page.getByLabel("Manter detalhe por (dias)")).toBeVisible();
  await page.getByLabel("Manter detalhe por (dias)").fill("89");
  await page.getByRole("button", {name: "Salvar alterações"}).click();
  expect(await page.getByLabel("Manter detalhe por (dias)").evaluate((input: HTMLInputElement) => input.validity.rangeUnderflow)).toBeTruthy();
  await page.getByLabel("Manter detalhe por (dias)").fill("90");
  await page.getByRole("button", {name: "Consultar próximo lote (sem excluir)"}).click();
  await expect(page.getByText(/Nenhum dado foi removido/)).toBeVisible();
  await noHorizontalOverflow(page);
  await page.screenshot({path: `../artifacts/e2e/settings-${page.viewportSize()?.width}.png`});
  expect(errors).toEqual([]);
});

test("calendário paginado e filtros persistidos na URL", async ({page}) => {
  await login(page);
  await page.goto("/operations");
  await expect(page.getByRole("heading", {name: "Calendário operacional"})).toBeVisible();
  await page.getByLabel("Tipo de publicação", {exact:true}).selectOption("BOT_PROMO");
  await page.getByLabel("Estado da tentativa", {exact:true}).selectOption("PENDING");
  await expect(page).toHaveURL(/calendar.kind=BOT_PROMO/);
  await expect(page).toHaveURL(/calendar.state=PENDING/);
  await expect(page.getByRole("heading", {name: "Resumo da semana anterior"})).toBeVisible();
  await noHorizontalOverflow(page);
  await page.screenshot({path: `../artifacts/e2e/calendar-${page.viewportSize()?.width}.png`});
});

test("Produto 360 mantém foto e título, fecha por Escape e devolve foco", async ({page}) => {
  await login(page);
  await page.goto("/opportunities");
  const open = page.getByRole("button", {name: "Produto 360 · ver histórico e decisões"}).first();
  await open.click();
  const dialog = page.getByRole("dialog", {name: "Produto 360"});
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("heading", {name: "Por que esta oferta?"})).toBeVisible();
  const box = await dialog.boundingBox();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
  await page.screenshot({path: `../artifacts/e2e/product360-${page.viewportSize()?.width}.png`});
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await expect(open).toBeFocused();
});
