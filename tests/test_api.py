from fastapi.testclient import TestClient

from govee_artnet_lan_bridge.api import create_app
from govee_artnet_lan_bridge.config import Config


def test_reload_endpoint_triggers_callback() -> None:
    calls: list[str] = []

    async def _trigger() -> None:
        calls.append("hit")

    app = create_app(Config(), store=object(), health=None, reload_callback=_trigger)
    client = TestClient(app)
    response = client.post("/reload")
    assert response.status_code == 202
    assert response.json()["status"] == "reload_requested"
    assert calls == ["hit"]


def test_reload_endpoint_without_callback() -> None:
    app = create_app(Config(), store=object(), health=None, reload_callback=None)
    client = TestClient(app)
    response = client.post("/reload")
    assert response.status_code == 503
