import asyncio

from activity_audit import audit_actor, record_admin_audit, redacted_diff, redact_value


class Collection:
    def __init__(self): self.rows = []
    async def insert_one(self, row): self.rows.append(row)


class Db:
    audit_logs = Collection()


def test_redaction_blocks_secrets_cards_and_pii():
    data = {
        "api_key": "live-secret", "password": "pw", "contact_email": "user@example.com",
        "phone": "+90 555 111 22 33", "card_number": "4111 1111 1111 1111",
        "sender_tax_no": "1234567890", "sender_name": "Gerçek Kişi",
        "logo_url": "https://cdn.example/a.png?signature=secret", "content": "private body",
    }
    safe = redact_value(data)
    assert "live-secret" not in str(safe) and "4111" not in str(safe)
    assert safe["contact_email"] == "[REDACTED_PII]"
    assert safe["sender_tax_no"] == "[REDACTED_PII]" and safe["sender_name"] == "[REDACTED_PII]"
    assert safe["logo_url"] == "https://cdn.example/a.png"  # signed query is never retained
    assert safe["content"] == "[text 12 chars]"


def test_diff_only_contains_changed_redacted_fields():
    diff = redacted_diff({"title": "A", "token": "old", "same": 1},
                         {"title": "B", "token": "new", "same": 1})
    assert set(diff) == {"title", "token"}
    assert diff["token"] == {"old": "[REDACTED]", "new": "[REDACTED]"}


def test_actor_context_never_keeps_bearer_token():
    class Request:
        headers = {"authorization": "Bearer top-secret", "user-agent": "pytest", "x-forwarded-for": "1.2.3.4"}
        client = None
    actor, context = audit_actor({"id": "u1", "email": "admin@example.com", "auth_provider": "google"}, Request())
    assert actor["login_method"] == "google"
    assert "top-secret" not in str(context)
    assert len(context["session_hash"]) == 12


def test_canonical_audit_record_is_redacted():
    Db.audit_logs.rows.clear()
    ok = asyncio.run(record_admin_audit(
        Db, action="settings.update", entity_type="settings", entity_id="main",
        before={"site_name": "A", "smtp_password": "old"},
        after={"site_name": "B", "smtp_password": "new"},
        current_user={"id": "u1", "email": "admin@example.com"},
    ))
    assert ok is True
    row = Db.audit_logs.rows[0]
    assert row["schema_version"] == 2 and row["entity"] == {"type": "settings", "id": "main"}
    assert row["diff"]["smtp_password"]["new"] == "[REDACTED]"
    assert "admin@example.com" not in str(row) and row["actor"]["email_hash"]
