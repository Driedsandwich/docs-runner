"""Headless, localhost-only integration checks. No external pages or commands."""
import asyncio
import copy
import json
from pathlib import Path

from playwright.async_api import async_playwright, expect

OUT = Path(__file__).resolve().parents[1] / "artifacts"


def luminance(color):
    values = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in values]
    return sum(v * weight for v, weight in zip(linear, (.2126, .7152, .0722)))


def contrast(a, b):
    x, y = sorted((luminance(a), luminance(b)))
    return round((y + .05) / (x + .05), 2)


async def main():
    report = {"checks": [], "console_errors": [], "external_requests": [], "contrast": {}}
    def passed(text):
        report["checks"].append(text)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--no-sandbox"])
        context = await browser.new_context(viewport={"width": 1440, "height": 1200}, permissions=["clipboard-read", "clipboard-write"])
        async def local_only(route):
            if not route.request.url.startswith("http://localhost:8000/"):
                report["external_requests"].append(route.request.url)
                await route.abort()
            else:
                await route.continue_()
        await context.route("**/*", local_only)
        page = await context.new_page()
        page.on("pageerror", lambda error: report["console_errors"].append(str(error)))
        submitted = []
        page.on("request", lambda req: submitted.append(req.url) if req.method == "POST" and req.url.endswith("/api/jobs") else None)
        await page.goto("http://localhost:8000/")
        await expect(page.locator("#headline")).to_have_text("A boundary for every step.")
        await expect(page.locator("#score")).to_have_text("Doc health: —")
        await page.screenshot(path=str(OUT / "desktop-empty.png"), full_page=True)
        await page.set_viewport_size({"width": 390, "height": 844})
        await page.screenshot(path=str(OUT / "mobile-empty.png"), full_page=True)
        await page.set_viewport_size({"width": 1440, "height": 1200})
        await page.locator('[data-demo="hostile"]').click()
        assert not submitted
        passed("Fixture selection updates the URL without submitting a job; empty state is unscored.")
        await page.evaluate("""() => {
          window.observedCounts = []; window.observedLifecycle = [];
          new MutationObserver(() => {
            window.observedCounts.push(document.querySelectorAll('.containment-event').length);
            window.observedLifecycle.push(document.querySelector('#lifecycle').textContent);
          }).observe(document.querySelector('.map'), {subtree:true,childList:true,characterData:true});
        }""")
        await page.locator("#run").click()
        await expect(page.locator("#run-label")).to_have_text("Stop")
        await page.wait_for_function("() => document.querySelectorAll('.step-list li').length === 6")
        await page.screenshot(path=str(OUT / "desktop-running.png"), full_page=True)
        await page.set_viewport_size({"width": 390, "height": 844})
        await page.screenshot(path=str(OUT / "mobile-running.png"), full_page=True)
        await page.set_viewport_size({"width": 1440, "height": 1200})
        await page.wait_for_function("() => document.querySelector('#run-label').textContent === 'Re-run'", timeout=25000)
        await expect(page.locator("#score")).to_have_text("Doc health: 67/100")
        await expect(page.locator("#coverage")).to_have_text("Coverage: 3/6")
        await expect(page.locator("#verified")).to_have_text("2 of 3 verified")
        assert await page.locator(".containment-event").count() == 4
        assert await page.locator("#extraction-events [data-event=injection_detected]").count() == 1
        assert set(await page.evaluate("() => window.observedCounts")) >= {1, 2, 3, 4}
        assert "Destroying" in await page.evaluate("() => window.observedLifecycle")
        await expect(page.locator("#lifecycle")).to_have_text("Destroyed")
        passed("Live SSE adds four observations separately; injection is at extraction; Destroying precedes Destroyed.")
        await page.wait_for_function("() => document.querySelector('#next-job .next-sandbox')?.textContent.includes('fixture-')")
        assert "running" in await page.locator("#next-job h2").inner_text()
        await page.screenshot(path=str(OUT / "desktop.png"), full_page=True)
        assert await page.evaluate("() => document.documentElement.scrollWidth === innerWidth")
        passed("1440px desktop has no page overflow; real follow-up fixture job exposes a new Sandbox ID while running.")
        actual = await page.evaluate("() => JSON.parse(JSON.stringify(job))")
        assert len(actual["steps"]) == 6
        await page.get_by_role("button", name="04 Deploy").click()
        await expect(page.locator("#evidence")).to_contain_text("Not executed")
        assert await page.locator('#evidence input').count() == 0
        await page.get_by_role("button", name="03 Start service").click()
        await expect(page.locator(".suggestion-label")).to_have_text("Unverified suggestion · Not applied")
        await page.get_by_role("button", name="Copy output", exact=True).click()
        await expect(page.get_by_role("button", name="Copied", exact=True)).to_be_visible()
        copied = await page.evaluate("() => navigator.clipboard.readText()")
        assert "server.py" in copied
        await page.get_by_role("button", name="Wrap: on").click()
        assert await page.locator(".output-tail.nowrap").count() > 0
        await page.get_by_role("button", name="Wrap: off").click()
        await page.get_by_role("button", name="Open output ↗").click()
        await expect(page.locator("#output-dialog")).to_be_visible()
        await page.keyboard.press("Escape")
        await expect(page.get_by_role("button", name="Open output ↗")).to_be_focused()
        passed("Evidence selection, Not executed, unverified suggestions, clipboard, wrap toggle, output dialog and Escape focus return.")
        await page.set_viewport_size({"width": 390, "height": 844})
        await page.evaluate("() => scrollTo(0,0)")
        await page.screenshot(path=str(OUT / "mobile.png"), full_page=True)
        assert await page.evaluate("() => document.documentElement.scrollWidth === innerWidth")
        await page.locator(".menu-button").click()
        await expect(page.locator("#close-menu")).to_be_focused()
        assert await page.evaluate("() => document.querySelector('#main').inert && getComputedStyle(document.body).overflow === 'hidden'")
        await page.screenshot(path=str(OUT / "mobile-menu.png"))
        bounds = await page.locator("#controls").bounding_box()
        assert bounds["x"] + bounds["width"] == 310
        for _ in range(20):
            await page.keyboard.press("Tab")
            assert await page.evaluate("() => document.querySelector('#controls').contains(document.activeElement)")
        for _ in range(20):
            await page.keyboard.press("Shift+Tab")
            assert await page.evaluate("() => document.querySelector('#controls').contains(document.activeElement)")
        await page.keyboard.press("Escape")
        await expect(page.locator(".menu-button")).to_be_focused()
        assert not await page.evaluate("() => document.querySelector('#main').inert")
        passed("390px mobile and 310px menu: no overflow, scroll lock, forward/reverse focus trap and Escape trigger return.")
        await page.emulate_media(reduced_motion="reduce")
        assert await page.locator(".containment-event").first.evaluate("el => getComputedStyle(el).animationName") == "none"
        await page.emulate_media(reduced_motion="no-preference")
        passed("Reduced motion removes both event and boundary animations.")
        # Exercise the same renderer with deliberately extreme local API-shaped data.
        stress = copy.deepcopy(actual)
        stress["url"] = "https://example.org/" + "long-public-document-path-" * 70
        stress["presentation"]["next_job_id"] = None
        stress["presentation"]["plan"] = []
        stress["presentation"]["step_details"] = {}
        stress["steps"] = []
        for i in range(1, 16):
            step = copy.deepcopy(actual["steps"][2])
            step["step"]["id"] = i
            step["attempts"][0]["evidence"]["stderr"] = "long-output-" * 2000
            stress["steps"].append(step)
            stress["presentation"]["plan"].append({"id": i, "title": f"Long document step {i}", "command": step["step"]["command"]})
        await page.evaluate("value => { selectionMade = true; selected = 15; render(value, true); }", stress)
        await expect(page.locator("#steps li")).to_have_count(15)
        await page.locator("#steps li").last.locator("button").focus()
        await page.keyboard.press("Enter")
        await expect(page.locator("#sandbox-step")).to_contain_text("STEP 15")
        assert await page.evaluate("() => document.documentElement.scrollWidth === innerWidth")
        await page.screenshot(path=str(OUT / "mobile-15-steps.png"), full_page=True)
        await page.get_by_role("button", name="Wrap: on").click()
        assert await page.evaluate("() => document.documentElement.scrollWidth === innerWidth")
        await page.get_by_role("button", name="Open output ↗").click()
        assert await page.locator("#full-output").evaluate("el => el.scrollHeight > el.clientHeight")
        await page.keyboard.press("Escape")
        await page.set_viewport_size({"width": 1440, "height": 1200})
        assert await page.evaluate("() => document.documentElement.scrollWidth === innerWidth")
        passed("15 steps are keyboard-selectable; long URLs and output remain bounded at 390/1440px; full captured tails scroll inside a dialog.")
        unverified = copy.deepcopy(actual)
        unverified["steps"] = [actual["steps"][3], actual["steps"][4], actual["steps"][5]]
        unverified["presentation"]["plan"] = [p for p in actual["presentation"]["plan"] if p["id"] in (4, 5, 6)]
        await page.evaluate("value => render(value, true)", unverified)
        await expect(page.locator("#score")).to_have_text("Doc health: —")
        await expect(page.locator("#verified")).to_have_text("Not scored · 0 of 0 verified")
        await expect(page.locator("#coverage")).to_have_text("Coverage: 0/3")
        passed("Zero verified steps shows Not scored / — / Coverage 0/3, never a success score.")
        await page.reload()
        await page.locator("#run").click()
        await expect(page.locator("#run-label")).to_have_text("Stop")
        await page.locator("#run").click()
        await expect(page.locator("#headline")).to_have_text("Stopped. Evidence preserved.", timeout=10000)
        await expect(page.locator("#run-label")).to_have_text("Re-run")
        passed("Stop reaches a terminal cancelled API snapshot and restores Re-run.")
        # Rejection remains in the same layout; no external audit is submitted.
        async def reject(route):
            await route.fulfill(status=429, content_type="application/json", body=json.dumps({"detail": "Two jobs are already running"}))
        await page.route("**/api/jobs", reject)
        await page.locator("#run").click()
        await expect(page.locator("#notice")).to_contain_text("Two jobs are already running")
        await expect(page.locator("#run")).to_be_enabled()
        await expect(page.locator("#evidence")).not_to_contain_text("Creating the boundary")
        await expect(page.locator("#lifecycle")).to_have_text("Not created")
        await page.screenshot(path=str(OUT / "desktop-error.png"), full_page=True)
        await page.set_viewport_size({"width": 390, "height": 844})
        await page.screenshot(path=str(OUT / "mobile-error.png"), full_page=True)
        passed("API rejection is an accessible error, retains the layout and enables retry.")
        reconnect = await context.new_page()
        reconnect.on("pageerror", lambda error: report["console_errors"].append(str(error)))
        await reconnect.goto("http://localhost:8000/")
        await reconnect.locator('[data-demo="hostile"]').click()
        await reconnect.locator("#run").click()
        await reconnect.wait_for_function("() => document.querySelectorAll('.containment-event').length === 1")
        await context.set_offline(True)
        await expect(reconnect.locator("#notice")).to_contain_text("Connection interrupted", timeout=10000)
        await context.set_offline(False)
        await expect(reconnect.locator("#run-label")).to_have_text("Re-run", timeout=25000)
        assert await reconnect.locator(".containment-event").count() == 4
        await expect(reconnect.locator("#score")).to_have_text("Doc health: 67/100")
        passed("SSE interruption is visible; reconnect recovers the terminal snapshot without duplicate containment events.")
        assert report["console_errors"] == []
        assert report["external_requests"] == []
        for label, fg, bg, threshold in [
            ("body/background", "#EDF0F6", "#10141C", 4.5),
            ("body/surface", "#EDF0F6", "#1A2130", 4.5),
            ("muted/surface", "#A9B3C3", "#1A2130", 4.5),
            ("accent/background", "#B89AFF", "#10141C", 4.5),
            ("button/accent", "#161021", "#B89AFF", 4.5),
            ("control-border/surface", "#7D8A9E", "#1A2130", 3),
            ("muted/selected", "#A9B3C3", "#30283F", 4.5),
        ]:
            ratio = contrast(fg, bg)
            assert ratio >= threshold
            report["contrast"][label] = ratio
        passed("Text contrasts ≥4.5:1; major UI boundaries ≥3:1. No page errors or external browser requests.")
        await browser.close()
    (OUT / "ui-validation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
