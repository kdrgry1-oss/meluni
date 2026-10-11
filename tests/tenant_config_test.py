import asyncio

from tenant_config import (
    TenantConfig, get_tenant_config, invalidate, legacy_aliases,
    legacy_write_through, migrate_tenant_config, migration_seed,
)


class _Collection:
    def __init__(self, docs=None):
        self.docs = {d["id"]: dict(d) for d in (docs or [])}

    async def find_one(self, query, projection=None):
        doc = self.docs.get(query.get("id"))
        return dict(doc) if doc else None

    async def update_one(self, query, update, upsert=False):
        key, inserted = query["id"], query["id"] not in self.docs
        if inserted and upsert:
            self.docs[key] = {"id": key}
        if "$set" in update:
            self.docs[key].update(update["$set"])
        if inserted and "$setOnInsert" in update:
            self.docs[key].update(update["$setOnInsert"])


class _Db:
    name = "tenant-test"

    def __init__(self, docs=None):
        self.settings = _Collection(docs)


def test_alias_and_canonical_precedence():
    aliases = legacy_aliases({
        "site_name": "Legacy", "contact_email": "old@example.test",
        "free_shipping_limit": 500, "company_info": {"email": "new@example.test"},
    })
    assert aliases["contact"]["email"] == "new@example.test"
    assert aliases["shipping"]["free_shipping_threshold"] == 500
    db = _Db([{"id": "main", "site_name": "Legacy"},
              {"id": "tenant_config", "brand": {"store_name": "Canonical"}}])
    invalidate()
    assert asyncio.run(get_tenant_config(db))["brand"]["store_name"] == "Canonical"


def test_neutral_defaults_seed_and_compatibility_write():
    assert "MELUNI" not in str(TenantConfig().model_dump())
    assert migration_seed({}, {}, {})["brand"]["store_name"] == "MELUNI"
    cfg = TenantConfig.model_validate({
        "brand": {"store_name": "New Brand"},
        "domains": {"storefront_url": "https://example.test/"},
        "shipping": {"free_shipping_threshold": 1000},
    }).model_dump()
    patch = legacy_write_through(cfg)
    assert patch["site_url"] == "https://example.test"
    assert patch["free_shipping_limit"] == patch["free_shipping_threshold"] == 1000


def test_migration_is_dry_run_and_insert_only():
    db = _Db([{"id": "main", "site_name": "Existing Brand"}])
    assert asyncio.run(migrate_tenant_config(db))["action"] == "would_insert"
    assert "tenant_config" not in db.settings.docs
    assert asyncio.run(migrate_tenant_config(db, apply=True))["action"] == "insert"
    assert db.settings.docs["tenant_config"]["brand"]["store_name"] == "Existing Brand"
    assert asyncio.run(migrate_tenant_config(db, apply=True))["action"] == "none"


def test_only_opaque_secret_refs_survive_schema():
    cfg = TenantConfig.model_validate({
        "secret_refs": {"smtp": "vault:smtp.primary", "bad": "raw-looking value"},
        "smtp_password": "must-not-survive",
    }).model_dump()
    assert cfg["secret_refs"] == {"smtp": "vault:smtp.primary"}
    assert "smtp_password" not in cfg


def test_legacy_bare_domains_are_normalized_to_absolute_urls():
    cfg = TenantConfig.model_validate({
        "domains": {
            "storefront_url": "meluni.com.tr/",
            "api_url": "//api.meluni.com.tr/",
            "cdn_url": "localhost:3000/",
        }
    })
    assert cfg.domains.storefront_url == "https://meluni.com.tr"
    assert cfg.domains.api_url == "https://api.meluni.com.tr"
    assert cfg.domains.cdn_url == "http://localhost:3000"
