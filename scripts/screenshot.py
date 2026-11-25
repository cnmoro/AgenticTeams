#!/usr/bin/env python3
"""Screenshot da UI com Playwright (verificação manual).

    python3 scripts/screenshot.py http://127.0.0.1:8798/#loja docs/ui.png
"""
import asyncio
import sys

from playwright.async_api import async_playwright


async def main(url, out):
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 860})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        await page.goto(url)
        await page.wait_for_selector(".msg")
        await page.wait_for_timeout(800)
        await page.check("#task-show-done")
        await page.wait_for_timeout(300)
        await page.fill("#input", "@fo")
        await page.wait_for_timeout(300)
        await page.screenshot(path=out)
        checks = {
            "mensagem do humano destacada": await page.locator(".msg.human .tag.human").count() > 0,
            "tarefa no painel": await page.locator("#tasks .task").count() > 0,
            "autocomplete de @": await page.is_visible("#mention-menu"),
            "SSE conectado": (await page.text_content("#conn")) == "conectado",
        }
        await browser.close()
    for k, v in checks.items():
        print(f"{'ok ' if v else 'FALHOU'} {k}")
    if errors:
        print("erros no console:", *errors, sep="\n  ")
    return 0 if all(checks.values()) and not errors else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1], sys.argv[2])))
