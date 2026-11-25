#!/usr/bin/env python3
"""Screenshot the web UI with Playwright (manual check).

    set -a; . "$AT_HOME/demo-login.env"; set +a      # login created by scripts/demo.sh
    python3 scripts/screenshot.py http://127.0.0.1:8798/#store docs/ui.png
"""
import asyncio
import os
import sys
from urllib.parse import urlsplit

from playwright.async_api import async_playwright


async def main(url, out):
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 860})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        parts = urlsplit(url)
        await page.goto(f"{parts.scheme}://{parts.netloc}/login" + (f"?{parts.query}" if parts.query else "")
                        + (f"#{parts.fragment}" if parts.fragment else ""))
        await page.fill("input[name=email]", os.environ["AT_DEMO_EMAIL"])
        await page.fill("input[name=password]", os.environ["AT_DEMO_PASSWORD"])
        await page.click("button[type=submit]")
        await page.wait_for_selector(".msg")
        await page.wait_for_timeout(800)
        await page.check("#task-show-done")
        await page.wait_for_timeout(300)
        await page.fill("#input", "@inv")
        await page.wait_for_timeout(300)
        await page.screenshot(path=out)
        checks = {
            "human message highlighted": await page.locator(".msg.human .tag.human").count() > 0,
            "task in the panel": await page.locator("#tasks .task").count() > 0,
            "@ autocomplete": await page.is_visible("#mention-menu"),
            "SSE connected": await page.locator("#conn.on").count() > 0,
            "Markdown rendered": await page.locator(".msg .body ol li").count() > 0
                                    and await page.locator(".msg .body pre code").count() > 0,
            "only the messages scroll": await page.evaluate(
                "document.scrollingElement.scrollHeight <= innerHeight + 1"),
        }
        await browser.close()
    for k, v in checks.items():
        print(f"{'ok    ' if v else 'FAILED'} {k}")
    if errors:
        print("console errors:", *errors, sep="\n  ")
    return 0 if all(checks.values()) and not errors else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1], sys.argv[2])))
