"""Exercise real route function bodies without production Mongo/startup imports."""
import ast
import asyncio
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from fastapi.security import HTTPAuthorizationCredentials

ROOT = Path(__file__).parents[1]


def load_functions(path, names, scope, decorators=False):
    tree = ast.parse((ROOT / path).read_text())
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    assert len(nodes) == len(names)
    if not decorators:
        for n in nodes:
            n.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROOT / path), "exec"), scope)
    return scope


@pytest.fixture
def permissions():
    db = SimpleNamespace(roles=SimpleNamespace(find_one=AsyncMock(return_value=None)))
    scope = {"db": db, "Depends": Depends, "HTTPException": HTTPException,
             "require_admin": lambda: {}}
    return load_functions("routes/deps.py", ["get_effective_permissions", "require_permission"], scope)


@pytest.mark.parametrize("user", [None, {}, {"created_by": "owner"},
    {"email": "admin@meluni.com"}, {"is_super_admin": "true"}, {"role_id": "deleted"}])
def test_no_implicit_permission_grants(permissions, user):
    assert asyncio.run(permissions["get_effective_permissions"](user)) == []


def test_explicit_owner_and_assigned_role(permissions):
    get = permissions["get_effective_permissions"]
    assert asyncio.run(get({"is_super_admin": True})) == ["*"]
    permissions["db"].roles.find_one.return_value = {"permissions": ["orders.view"]}
    assert asyncio.run(get({"role_id": "ops"})) == ["orders.view"]


@pytest.mark.parametrize("operation", ["returns.iyzico_refund", "returns.refund_pay", "settings.emails"])
def test_roleless_sensitive_operations_denied(permissions, operation):
    with pytest.raises(HTTPException) as caught:
        asyncio.run(permissions["require_permission"](operation)({"created_by": "owner"}))
    assert caught.value.status_code == 403


@pytest.fixture
def marketing(permissions):
    settings = SimpleNamespace(update_one=AsyncMock(), find_one=AsyncMock(return_value={}))
    scope = {**permissions, "db": SimpleNamespace(settings=settings),
             "admin_router": APIRouter(), "_SECRET_MASK": "********", "_now": lambda: "now",
             "logger": logging.getLogger("test")}
    load_functions("routes/email_marketing.py", ["save_settings", "get_settings", "validate_provider"], scope, decorators=True)
    return scope


def test_settings_routes_enforce_specific_permission(marketing):
    app = FastAPI()
    app.include_router(marketing["admin_router"])
    with TestClient(app) as client:
        for method, path in [("GET", "/settings"), ("PUT", "/settings"), ("POST", "/validate-provider")]:
            result = client.request(method, path, json={})
            assert result.status_code == 403
    marketing["db"].settings.update_one.assert_not_awaited()


@pytest.mark.parametrize("field", ["brevo_api_key", "brevo_webhook_secret", "ses_secret_key"])
def test_encryption_failure_never_writes_settings(marketing, monkeypatch, field):
    module = types.ModuleType("security.crypto")
    def broken(_):
        raise RuntimeError("test secret must not leak")
    module.encrypt = broken
    monkeypatch.setitem(sys.modules, "security.crypto", module)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(marketing["save_settings"]({field: "test-private-key"}, {}))
    assert caught.value.status_code == 503
    assert "test-private-key" not in str(caught.value.detail)
    marketing["db"].settings.update_one.assert_not_awaited()


def test_settings_encrypt_and_preserve_masked_secrets(marketing, monkeypatch):
    module = types.ModuleType("security.crypto")
    module.encrypt = lambda value: "v1:encrypted-" + value
    monkeypatch.setitem(sys.modules, "security.crypto", module)
    asyncio.run(marketing["save_settings"]({"brevo_api_key": "example", "ses_secret_key": "********"}, {}))
    calls = marketing["db"].settings.update_one.await_args_list
    assert calls[1].args[1]["$set"]["api_key"] == "v1:encrypted-example"
    assert "secret_key" not in calls[2].args[1]["$set"]


def test_suppression_read_failure_stops_instead_of_empty_list():
    class Broken:
        def find(self, *_):
            raise RuntimeError("database unavailable")
    scope = {"db": SimpleNamespace(email_suppressions=Broken()), "HTTPException": HTTPException,
             "logger": logging.getLogger("test")}
    load_functions("routes/email_marketing.py", ["_suppressed_set"], scope)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(scope["_suppressed_set"]())
    assert caught.value.status_code == 503


def test_suppression_list_normalizes_all_rows():
    class Collection:
        def find(self, *_):
            async def rows():
                for row in [{"email": " Person@Example.Test "}, {}, {"email": "other@example.test"}]:
                    yield row
            return rows()
    scope = {"db": SimpleNamespace(email_suppressions=Collection()), "HTTPException": HTTPException,
             "logger": logging.getLogger("test")}
    load_functions("routes/email_marketing.py", ["_suppressed_set"], scope)
    assert asyncio.run(scope["_suppressed_set"]()) == {"person@example.test", "other@example.test"}


@pytest.mark.parametrize("module_name,field", [("email_brevo", "api_key"), ("email_ses", "secret_key")])
def test_unreadable_credentials_disable_provider(monkeypatch, module_name, field):
    import importlib
    provider = importlib.import_module(module_name)
    crypto = types.ModuleType("security.crypto")
    crypto.decrypt = lambda _: (_ for _ in ()).throw(RuntimeError("key unavailable"))
    monkeypatch.setitem(sys.modules, "security.crypto", crypto)
    db = SimpleNamespace(settings=SimpleNamespace(find_one=AsyncMock(return_value={
        "enabled": True, field: "v1:unreadable", "from_email": "sender@example.test", "list_id": 1,
        "access_key": "test-id", "region": "eu-west-1",
    })))
    get = getattr(provider, "get_brevo_config" if module_name == "email_brevo" else "get_ses_config")
    cfg = asyncio.run(get(db))
    assert cfg[field] is None
    assert not provider.is_configured(cfg)


@pytest.mark.parametrize("error,status", [(HTTPException(503, "Suppression unavailable"), "failed"),
                                         (RuntimeError("unknown delivery"), "needs_review")])
def test_campaign_failure_is_visible_without_fallback(error, status):
    campaigns = SimpleNamespace(find_one=AsyncMock(return_value={"provider": "brevo"}), update_one=AsyncMock())
    scope = {"db": SimpleNamespace(email_campaigns=campaigns), "HTTPException": HTTPException,
             "_now": lambda: "now", "logger": logging.getLogger("test"),
             "_run_brevo_campaign": AsyncMock(side_effect=error), "_run_ses_campaign": AsyncMock()}
    load_functions("routes/email_marketing.py", ["_run_campaign"], scope)
    asyncio.run(scope["_run_campaign"]("test-campaign"))
    assert campaigns.update_one.await_args.args[1]["$set"]["status"] == status
    scope["_run_ses_campaign"].assert_not_awaited()


def test_document_endpoints_have_no_query_token_auth():
    targets = {
        "orders.py": {"print_invoice_html", "get_cargo_label"},
        "barcode_cards.py": {"get_product_barcode_card"},
        "members.py": {"member_360_print"},
        "influencers.py": {"influencer_pr_cargo_label", "influencer_pr_irsaliye"},
    }
    for file, names in targets.items():
        nodes = ast.parse((ROOT / "routes" / file).read_text()).body
        functions = [n for n in nodes if isinstance(n, ast.AsyncFunctionDef) and n.name in names]
        assert len(functions) == len(names)
        for node in functions:
            assert "token" not in [arg.arg for arg in node.args.args]
            assert "current_user" in [arg.arg for arg in node.args.args]


def test_invoice_escapes_identifiers_and_requires_invoice_permission(permissions):
    attack = "</title><script>alert('test')</script>"
    orders = SimpleNamespace(find_one=AsyncMock(return_value={
        "invoice_number": attack, "order_number": attack, "invoice_provider": attack,
        "items": [], "total": 100,
    }))
    scope = {**permissions, "db": SimpleNamespace(orders=orders), "router": APIRouter()}
    load_functions("routes/orders.py", ["print_invoice_html"], scope, decorators=True)
    app = FastAPI()
    app.include_router(scope["router"])
    with TestClient(app) as client:
        assert client.get("/test/invoice/print?token=old-session").status_code == 403
    orders.find_one.assert_not_awaited()
    response = asyncio.run(scope["print_invoice_html"]("test", {}))
    html = response.body.decode()
    assert attack not in html
    assert "&lt;script&gt;" in html
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("user,allowed", [
    ({"is_admin": True, "created_by": "owner"}, False),
    ({"is_admin": True, "email": "admin@meluni.com"}, False),
    ({"is_admin": False, "is_super_admin": True}, False),
    ({"is_admin": True, "role_id": "deleted"}, False),
    ({"is_admin": True, "is_super_admin": True}, True),
    ({"is_admin": True, "role_id": "staff"}, True),
])
def test_admin_gate_rechecks_database_role_and_admin_flag(user, allowed):
    db = SimpleNamespace(users=SimpleNamespace(find_one=AsyncMock(return_value=user)),
                         roles=SimpleNamespace(find_one=AsyncMock(side_effect=lambda q, _: {"permissions": ["orders.view"]} if q["id"] == "staff" else None)))
    scope = {"db": db, "Depends": Depends, "security": None, "HTTPException": HTTPException,
             "HTTPAuthorizationCredentials": HTTPAuthorizationCredentials,
             "_decode_jwt_strict": lambda _: {"is_admin": True, "user_id": "test", "typ": "staff"},
             "_token_revoked": lambda *_: False,
             "get_security_epoch": AsyncMock(return_value=(0, 0)),
             "_epoch_revoked": lambda *_: False,
             "_enforce_staff_idle": AsyncMock(return_value=None),
             "_TYP_MISSING_LOG": {"at": 0.0}, "logger": SimpleNamespace(info=lambda *a, **k: None),
             "jwt": __import__("jwt")}
    load_functions("routes/deps.py", ["require_admin", "get_effective_permissions"], scope)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="synthetic-test")
    if allowed:
        assert asyncio.run(scope["require_admin"](credentials)) == user
    else:
        with pytest.raises(HTTPException) as caught:
            asyncio.run(scope["require_admin"](credentials))
        assert caught.value.status_code == 403


def test_corrupt_token_version_is_revoked():
    scope = load_functions("routes/deps.py", ["_token_revoked"], {})
    assert scope["_token_revoked"]({"tv": "invalid"}, {"token_version": 1}) is True
    assert scope["_token_revoked"]({"tv": 1}, {"token_version": 2}) is True
    assert scope["_token_revoked"]({"tv": 2}, {"token_version": 2}) is False
