"""Denetim #16: personel listesi (/api/admin/users) diğer personelin parola/MFA/kilit meta verisini
döndürmez (tek uç + açık izin listesi)."""
from conftest import kv_run

SECRET_KEYS = ("password", "password_history", "mfa_secret_enc", "mfa_recovery_codes", "mfa_phone_enc",
               "mfa_fail_count", "token_version", "password_reset_channel", "mfa_day_window", "brand_new_secret")


def test_finance_staff_sees_only_directory_fields(kv_client, kv_users, kv_db):
    victim = kv_users.staff(perms=["orders.view"], created_by="owner",
                            **{k: "x" for k in SECRET_KEYS if k not in ("password", "token_version")})
    fin = kv_users.staff(perms=["settings.view", "reports.financial"], created_by="owner")
    r = kv_client.get("/api/admin/users", headers=kv_users.hdr(fin))
    assert r.status_code == 200, r.text
    rows = r.json()["users"]
    v = next(u for u in rows if u["id"] == victim["id"])
    assert v["email"] == victim["email"] and v["role_id"] == victim["role_id"]
    for k in SECRET_KEYS:
        assert k not in v, k
    assert "page" not in r.json()        # eski güvensiz kopya (admin.py) artık yanıt vermiyor
