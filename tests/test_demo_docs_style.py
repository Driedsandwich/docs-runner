import httpx

from app.fetch import DEMO_DIR, extract_body
from app.main import create_app


def test_demo_pages_use_same_origin_stylesheet_only():
    # The CSP is style-src 'self': inline <style> would be blocked on the public site.
    for name in ("good", "stale", "hostile"):
        html = (DEMO_DIR / f"{name}.html").read_text()
        assert "<style" not in html and " style=" not in html
        assert '<link rel="stylesheet" href="/demo-docs.css">' in html
        assert ".css" not in extract_body(html, "text/html")


async def test_demo_stylesheet_is_served_as_css(settings):
    app = create_app(settings)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/demo-docs.css")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/css")
    assert "max-width:880px" in response.text
