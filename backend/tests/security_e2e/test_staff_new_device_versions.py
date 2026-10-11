"""Yönetici girişi 'yeni cihaz' uyarısı: tarayıcı SÜRÜM güncellemesi yeni cihaz sayılmaz."""
from conftest import kv_run
from kv_harness import kv_findall

UA_OLD = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
UA_NEW = UA_OLD.replace("Chrome/140.0.0.0", "Chrome/141.0.7390.55")
UA_FF = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:131.0) Gecko/20100101 Firefox/131.0"


def _alerts():
    return [a for a in kv_findall("alerts", {}) if "yeni IP" in (a.get("title") or "")]


def test_browser_version_update_is_not_a_new_device_but_other_browser_and_ip_are(kv_users, kv_db):
    from security import events
    staff = kv_users.staff(perms=["products.view"])
    # mevcut kayıt ESKİ (sürüm-duyarlı) özetle → geçişte yeniden uyarı üretmemeli
    kv_run(kv_db.staff_known_devices.insert_one({
        "user_id": staff["id"], "net": events.ip_prefix("203.0.113.10"),
        "dev": events._legacy_device_hash(UA_OLD), "last_seen": "2999-01-01T00:00:00+00:00"}))
    kv_run(events.note_staff_login(staff, "203.0.113.10", UA_OLD, "password"))
    kv_run(events.note_staff_login(staff, "203.0.113.11", UA_NEW, "password"))   # Chrome güncellendi
    assert not _alerts()
    kv_run(events.note_staff_login(staff, "203.0.113.12", UA_FF, "password"))    # başka tarayıcı
    assert len(_alerts()) == 1
    kv_run(events.note_staff_login(staff, "198.51.100.7", UA_NEW, "password"))   # yeni IP bloğu
    assert len(_alerts()) == 2
