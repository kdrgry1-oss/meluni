"""Denetim #3: geçiş yeni HASSAS izinleri düşük yetkili rollere otomatik vermez; geçmişte verilenler
süper adminin incelemesine sunulur (sessizce silinmez), kaldır/koru kararı kalıcıdır."""
from permissions import upgrade_grants_for_existing_role, SENSITIVE_NO_AUTO_GRANT, UPGRADE_GRANTS_FLAG, DEFAULT_ROLES
from routes.admin_rbac import grant_least_privilege_upgrade_permissions
from conftest import kv_run


def test_migration_never_grants_sensitive_or_out_of_area():
    add = upgrade_grants_for_existing_role(["reports.sales"])
    assert not set(add) & SENSITIVE_NO_AUTO_GRANT
    assert all(p.startswith("reports.") for p in add)
    fin = next(r for r in DEFAULT_ROLES if r["id"] == "finance")
    assert "integrations.hepsiburada" not in fin["permissions"]
    assert "integrations.temu" not in fin["permissions"]


def test_fresh_migration_least_privilege(kv_db):
    kv_run(kv_db.roles.insert_one({"id": "r1", "name": "Rapor", "permissions": ["reports.sales"]}))
    kv_run(grant_least_privilege_upgrade_permissions())
    r = kv_run(kv_db.roles.find_one({"id": "r1"}))
    assert not set(r["permissions"]) & SENSITIVE_NO_AUTO_GRANT


def test_review_lists_revokes_and_keeps(kv_client, kv_users, kv_db):
    kv_run(kv_db.roles.insert_one({"id": "r2", "name": "Muhasebe",
                                   "permissions": ["reports.sales", "integrations.temu", "integrations.hepsiburada"]}))
    kv_run(kv_db.settings.insert_one({"id": UPGRADE_GRANTS_FLAG, "done": True, "roles": [
        {"role_id": "r2", "added": ["integrations.temu", "integrations.hepsiburada", "reports.stock"]}]}))
    admin = kv_users.staff(super_admin=True)
    low = kv_users.staff(perms=["settings.view"])
    assert kv_client.get("/api/admin/roles/autogrant-review", headers=kv_users.hdr(low)).status_code == 403
    r = kv_client.get("/api/admin/roles/autogrant-review", headers=kv_users.hdr(admin))
    assert r.status_code == 200, r.text
    assert r.json()["items"] == [{"role_id": "r2", "role_name": "Muhasebe",
                                  "permissions": ["integrations.hepsiburada", "integrations.temu"]}]
    r = kv_client.post("/api/admin/roles/autogrant-review", headers=kv_users.hdr(admin),
                       json={"role_id": "r2", "action": "keep", "permissions": ["integrations.hepsiburada"]})
    assert r.status_code == 200, r.text
    r = kv_client.post("/api/admin/roles/autogrant-review", headers=kv_users.hdr(admin),
                       json={"role_id": "r2", "action": "revoke",
                             "permissions": ["integrations.temu", "reports.sales"]})   # reports.sales listede yok
    assert r.status_code == 200 and r.json()["revoked"] == ["integrations.temu"]
    role = kv_run(kv_db.roles.find_one({"id": "r2"}))
    assert set(role["permissions"]) == {"reports.sales", "integrations.hepsiburada"}
    assert kv_client.get("/api/admin/roles/autogrant-review", headers=kv_users.hdr(admin)).json()["items"] == []
