import httpx
import pytest

from app.main import app


@pytest.mark.asyncio
async def test_hotels() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/hotels", params={"city": "Сочи", "max_price": 50000})
        assert r.status_code == 200 and [h["name"] for h in r.json()] == ["Морской бриз (FAKE)"]
        assert (await client.get("/hotels", params={"max_price": -1})).status_code == 422
        assert (await client.get("/", follow_redirects=False)).status_code == 307
