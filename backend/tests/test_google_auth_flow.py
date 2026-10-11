import importlib.util
from pathlib import Path
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


# Load auth.py without importing routes/__init__.py, which registers every
# product/report/integration route and makes these focused tests require the
# whole production dependency graph.
routes_dir = Path(__file__).parents[1] / "routes"
routes_pkg = types.ModuleType("google_auth_test_routes")
routes_pkg.__path__ = [str(routes_dir)]
sys.modules[routes_pkg.__name__] = routes_pkg

class _StubDeps(types.ModuleType):
    """auth.py deps'ten yeni bir yardımcı içe aktardığında test kırılmasın: tanımsız adlar
    zararsız bir çağrılabilir döner (testlerin ilgilendiği adlar aşağıda açıkça tanımlı)."""
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *_a, **_k: None


deps = _StubDeps(f"{routes_pkg.__name__}.deps")
deps.db = SimpleNamespace()
deps.logger = SimpleNamespace(info=lambda *_a, **_k: None, warning=lambda *_a, **_k: None)
deps.hash_password = lambda value: value
deps.verify_password = lambda *_a: False
deps.create_token = lambda *_a, **_k: "token"
deps.get_current_user = lambda: None
deps.generate_id = lambda: "test-id"
deps.safe_str = lambda value, limit=1000: str(value or "")[:limit]
deps.is_safe_email = lambda *_a: True
deps.write_audit_log = lambda *_a, **_k: None
deps.is_account_locked = lambda *_a, **_k: False
deps.register_failed_login = lambda *_a, **_k: None
deps.reset_failed_login = lambda *_a, **_k: None
deps.is_ip_blocked = lambda *_a, **_k: False
deps.register_failed_login_ip = lambda *_a, **_k: None
deps.client_ip_from_request = lambda *_a, **_k: "127.0.0.1"
deps.limiter = None
deps.validate_strong_password = lambda *_a, **_k: None
sys.modules[deps.__name__] = deps

spec = importlib.util.spec_from_file_location(
    f"{routes_pkg.__name__}.auth", routes_dir / "auth.py"
)
auth = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = auth
spec.loader.exec_module(auth)


def test_google_return_path_rejects_external_urls():
    assert auth._safe_google_return_to("/hesabim?s=1") == "/hesabim?s=1"
    assert auth._safe_google_return_to("https://evil.example") == "/hesabim"
    assert auth._safe_google_return_to("//evil.example") == "/hesabim"


def test_google_redirect_uri_prefers_explicit_public_uri(monkeypatch):
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://api.example.test/api/auth/google/callback")
    request = SimpleNamespace(base_url="http://internal:8000/")
    assert auth._google_redirect_uri(request) == "https://api.example.test/api/auth/google/callback"


@pytest.mark.asyncio
async def test_google_audiences_are_runtime_configured_and_deduplicated(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_IDS", "web-client,mobile-client")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "web-client")
    fake_db = SimpleNamespace(settings=SimpleNamespace(
        find_one=AsyncMock(return_value={"google_client_id": "tenant-client"})
    ))
    monkeypatch.setattr(auth, "db", fake_db)
    assert await auth._google_client_ids() == ["web-client", "mobile-client", "tenant-client"]
