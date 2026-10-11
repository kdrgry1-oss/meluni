import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import email_brevo


class Response:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data or {}
        self.content = json.dumps(self._data).encode() if data is not None else b""
        self.text = json.dumps(self._data) if data is not None else ""

    def json(self):
        return self._data


class Client:
    calls = []
    responses = []

    def __init__(self, **_):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def fake_client(monkeypatch):
    Client.calls = []
    Client.responses = []
    monkeypatch.setattr(email_brevo.httpx, "AsyncClient", Client)


def test_requires_marketing_list():
    assert not email_brevo.is_configured({"enabled": True, "api_key": "x", "from_email": "a@b.co"})
    assert email_brevo.is_configured({"enabled": True, "api_key": "x", "from_email": "a@b.co", "list_id": "12"})


@pytest.mark.asyncio
async def test_test_email_uses_brevo_https_api():
    Client.responses = [Response(201, {"messageId": "m-1"})]
    result = await email_brevo.send_test_email(
        {"api_key": "secret", "from_email": "news@example.com", "from_name": "Brand"},
        "user@example.com", "Konu", "<p>Merhaba</p>", "reply@example.com",
    )
    assert result["success"] is True
    assert result["message_id"] == "m-1"
    method, url, kwargs = Client.calls[0]
    assert (method, url) == ("POST", "https://api.brevo.com/v3/smtp/email")
    assert kwargs["headers"]["api-key"] == "secret"
    assert kwargs["json"]["replyTo"]["email"] == "reply@example.com"


@pytest.mark.asyncio
async def test_campaign_is_created_then_sent():
    Client.responses = [Response(201, {"id": 91}), Response(204)]
    result = await email_brevo.create_and_send_campaign(
        {"api_key": "secret", "list_id": 12, "from_email": "news@example.com"},
        name="Kampanya", subject="Konu", html="<p>İçerik</p>",
    )
    assert result == {"success": True, "campaign_id": 91}
    assert Client.calls[0][1].endswith("/emailCampaigns")
    assert Client.calls[0][2]["json"]["recipients"] == {"listIds": [12]}
    assert Client.calls[1][1].endswith("/emailCampaigns/91/sendNow")


@pytest.mark.asyncio
async def test_sync_removes_stale_and_does_not_force_unblacklist():
    Client.responses = [
        Response(200, {"contacts": [{"email": "old@example.com"}]}),
        Response(204),
        Response(201, {"id": 1}),
    ]
    result = await email_brevo.sync_contacts(
        {"api_key": "secret", "list_id": 12},
        [{"email": "new@example.com", "name": "Ada Lovelace"}],
    )
    assert result == {"success": True, "synced": 1, "removed": 1}
    remove_payload = Client.calls[1][2]["json"]
    upsert_payload = Client.calls[2][2]["json"]
    assert remove_payload == {"emails": ["old@example.com"]}
    assert upsert_payload["attributes"] == {"FIRSTNAME": "Ada"}
    assert "emailBlacklisted" not in upsert_payload
