import httpx

from app.main import VIDEO, create_app


async def test_video_is_served_as_mp4(settings):
    app = create_app(settings)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/video")
    assert VIDEO.is_file()
    assert response.status_code == 200 and response.headers["content-type"] == "video/mp4"
    assert int(response.headers["content-length"]) == VIDEO.stat().st_size
