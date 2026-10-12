"""Drive Motion Canvas' native image-sequence exporter for the Scene 2 proof."""

from __future__ import annotations

from playwright.sync_api import sync_playwright

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.goto("http://host.docker.internal:9000", wait_until="networkidle")
    render = page.locator("button").filter(has_text="RENDER")
    if render.count() == 0:
        body = page.locator("body").inner_text()
        raise RuntimeError(f"Motion Canvas render control unavailable:\n{body}")
    render.click()
    page.wait_for_function(
        """() => document.body.innerText.includes('Rendering') ||
                   document.body.innerText.includes('Abort')""",
        timeout=30_000,
    )
    page.wait_for_function(
        """() => document.body.innerText.includes('RENDER') &&
                   !document.body.innerText.includes('Abort') &&
                   !document.body.innerText.includes('Rendering frame')""",
        timeout=180_000,
    )
    print("Motion Canvas native export completed")
    browser.close()
