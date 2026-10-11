"""IAM-01 — TÜM uygulama için üretilmiş (generated), eksiksiz yetkilendirme matrisi.

Gerçek uygulamanın (`server.app`) her APIRoute'u (~1100) bağımlılık ağacından (route.dependant, özyinelemeli)
sınıflandırılır:

  * super      → require_super_admin (yalnız etkin izin '*')
  * perm       → require_permission("<izin>") (izin adı kapanıştan/closure okunur)
  * staff      → require_admin (herhangi bir geçerli personel rolü)
  * customer   → require_auth (giriş yapmış müşteri)
  * optional   → yalnız get_current_user (kimlik OPSİYONEL; asıl kontrol gövdede)
  * public     → hiçbir yetki bağımlılığı yok

Matris (ASGI TestClient + mongomock; tohumlanmış kullanıcılar, gerçek JWT'ler) her uç için GERÇEKTEN çalıştırılır:

  1. Token yok                     → 401/403, uç gövdesi (handler) HİÇ çalışmaz, iş verisi değişmez.
  2. Müşteri token'ı (personel uçları) → 401/403, handler çalışmaz, veri değişmez.
  3. Rolsüz/izinsiz personel (personel uçları) → 403, handler çalışmaz, veri değişmez.
  4. Gereken izin HARİÇ tüm izinlere sahip personel (perm uçları) / '*' olmadan tüm izinler (super uçları)
     → 403, handler çalışmaz, veri değişmez.
  5. En az yetkili DOĞRU arayan (yalnız o izne sahip personel / süper admin / müşteri) → yetki katmanı
     geçilir (handler'a ulaşılır; bu adımda handler bloklanır → yan etki yok) ya da yalnız parametre
     doğrulaması (422) döner — 422, FastAPI'de yetki bağımlılıkları başarıyla çözüldükten SONRA üretilir.

"Handler çalışmadı" kanıtı: test süresince her rotanın `dependant.call`'u bir kayıt sarmalayıcısıyla
değiştirilir (modül sonunda geri yüklenir). Ret adımlarında sarmalayıcı gerçek handler'ı ÇAĞIRIR (gövde
düzeyinde korunan uçlar gerçekten çalışsın ve yan etkisi ölçülsün); yine de bağımlılıkla korunan uçlarda
handler'a ulaşılmış olması testi KIRMIZI yapar.

Herkese açık yüzey (public + misafire açık optional uçlar) `public_routes_allowlist.txt` dosyasındaki
gözden geçirilmiş listeyle BİREBİR eşleşmek zorundadır: listede olmayan yeni bir herkese açık uç
(ya da listede kalmış silinmiş uç) testi KIRMIZI yapar — regresyon kapısı.

IAM-02 (en az yetki): "her personel" sınıfı (yalnız require_admin) `staff_shared_routes_allowlist.txt`
dosyasındaki gözden geçirilmiş ORTAK uçlarla BİREBİR eşleşmek zorundadır; diğer tüm panel uçları
sayfalarının iznini (require_permission) ister ve 4. adımda "gereken izin HARİÇ tüm izinler → 403"
ile tek tek doğrulanır.

Alan düzeyi: vitrin ürün/katalog/kategori/kombin/sayfa-blok uçları, iç alanları (alış fiyatı, maliyet,
üye fiyatları, tedarikçi, iç notlar, stok hareketleri…) dolu tohumlanmış ürün için ANAHTAR ya da DEĞER
olarak asla döndürmez (anonim ve müşteri).
"""
from __future__ import annotations

import functools
import inspect
import json
import re
from collections import Counter
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.security.http import HTTPBearer
from starlette.responses import JSONResponse
from starlette.routing import Match

from id_harness import id_auth, id_run, id_seed_role, id_seed_user, id_token
from kv_harness import kv_mongomock_convert  # noqa: F401  (fixture: mongomock'ta olmayan $convert)
import server  # noqa: F401  (id_harness zaten içe aktarır; tüm router'lar bağlı)
from routes import deps

pytestmark = pytest.mark.usefixtures("id_env")

APP = server.app
PROBE = "iam01-probe"
PROBE_UUID = "00000000-0000-4000-8000-000000000001"
HANDLER_STATUS = 299
HANDLER_HDR = "x-iam01-handler"
ALLOWLIST_FILE = Path(__file__).with_name("public_routes_allowlist.txt")
STAFF_SHARED_FILE = Path(__file__).with_name("staff_shared_routes_allowlist.txt")
_BODY_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Yalnız güvenlik/iz kayıtları (ret sırasında yazılması beklenen günlükler) — iş verisi DEĞİL.
_LOG_COLLS = {"auth_audit_logs", "audit_logs", "alerts", "staff_sessions", "staff_known_devices",
              "login_ip_failures", "pii_reveal_counters", "error_logs", "audit_outbox", "integration_logs",
              "security_events", "policy_denials", "rate_limits"}

# Kimliği OPSİYONEL okuyan (get_current_user) ama gövdede oturumu ZORUNLU kılan uçlar: token yoksa 401.
# (Bunlar allowlist'te DEĞİL; yeni bir optional uç ya burada ya da allowlist'te olmak zorunda.)
BODY_GUARDED_LOGIN = {
    "GET /api/auth/me",
    "POST /api/auth/change-password",
    "POST /api/auth/logout-all",
    "GET /api/me/measurements",
    "POST /api/me/measurements",
    "GET /api/products/{product_id}/size-recommendation",
    "POST /api/iys/consent/update",
}

# Yetki bağımlılığından ÖNCE çalışmasına izin verilen (salt-okur, önbellekli, istisna yutan) bağımlılıklar.
# Yeni bir "yetkiden önce çalışan" bağımlılık eklenirse test KIRMIZI olur (yetkisiz istek kod çalıştırmasın).
_PRE_AUTH_OK = {HTTPBearer}


# ───────────────────────────── sınıflandırma ─────────────────────────────
def _walk(dependant, out):
    for d in dependant.dependencies:
        out.append(d.call)
        _walk(d, out)
    return out


def _is_perm_checker(call) -> bool:
    return getattr(call, "__qualname__", "") == "require_permission.<locals>._checker"


def _perm_cells(call) -> dict:
    return dict(zip(call.__code__.co_freevars, (c.cell_contents for c in call.__closure__ or ())))


def _perm_key(call) -> str:
    return _perm_cells(call)["perm_key"]


def _any_in_area(call) -> bool:
    return bool(_perm_cells(call).get("any_in_area"))


def _is_auth_call(call) -> bool:
    return call in (deps.require_admin, deps.require_super_admin, deps.require_auth, deps.get_current_user) \
        or _is_perm_checker(call)


def classify(route: APIRoute):
    calls = _walk(route.dependant, [])
    perms = sorted({_perm_key(c) for c in calls if _is_perm_checker(c)})
    if deps.require_super_admin in calls:
        return "super", perms
    if perms:
        return "perm", perms
    if deps.require_admin in calls:
        return "staff", perms
    if deps.require_auth in calls:
        return "customer", perms
    if deps.get_current_user in calls:
        return "optional", perms
    return "public", perms


def _pre_auth_deps(route: APIRoute) -> list:
    pre = []
    for d in route.dependant.dependencies:
        if _is_auth_call(d.call):
            break
        if type(d.call) in _PRE_AUTH_OK:
            continue
        pre.append(getattr(d.call, "__qualname__", repr(d.call)))
    return pre


_CONV_VALUES = {"StringConvertor": PROBE, "PathConvertor": PROBE, "IntegerConvertor": "1",
                "FloatConvertor": "1.0", "UUIDConvertor": PROBE_UUID}


def probe_url(route: APIRoute) -> str:
    def _sub(m):
        name = m.group(1)
        conv = route.param_convertors.get(name)
        return _CONV_VALUES.get(type(conv).__name__, PROBE)
    return re.sub(r"\{(\w+)(?::\w+)?\}", _sub, route.path)


def _effective_route(method: str, url: str):
    scope = {"type": "http", "path": url, "root_path": "", "method": method, "headers": [],
             "query_string": b""}
    for r in APP.router.routes:
        m, _ = r.matches(scope)
        if m == Match.FULL:
            return r
    return None


def _build_entries():
    entries, seen = [], set()
    for r in APP.routes:
        if not isinstance(r, APIRoute):
            continue
        for method in sorted(r.methods):
            key = f"{method} {r.path}"
            if key in seen:  # aynı yöntem+yol iki kez kayıtlı → yalnız ilki erişilebilir
                continue
            seen.add(key)
            url = probe_url(r)
            eff = _effective_route(method, url)
            if not isinstance(eff, APIRoute):
                eff = r
            cls, perms = classify(eff)
            entries.append({"key": key, "method": method, "path": r.path, "url": url, "route": eff,
                            "declared": r, "shadowed": eff is not r, "cls": cls, "perms": perms,
                            "area_read": any(_any_in_area(c) for c in _walk(r.dependant, []) if _is_perm_checker(c))})
    return entries


ENTRIES = _build_entries()
BY_CLS = Counter(e["cls"] for e in ENTRIES)
GUARDED = [e for e in ENTRIES if e["cls"] in ("staff", "perm", "super", "customer")]
STAFF_ANY = [e for e in ENTRIES if e["cls"] in ("staff", "perm", "super")]
PERM_OR_SUPER = [e for e in ENTRIES if e["cls"] in ("perm", "super")]
BODY_GUARDED = [e for e in ENTRIES if e["key"] in BODY_GUARDED_LOGIN]
ROUTE_PERMS = sorted({p for e in ENTRIES for p in e["perms"]})


def _ids(rows):
    return [e["key"] for e in rows]


# ───────────────────────────── handler kaydedici ─────────────────────────────
HITS: list = []
MODE = {"block": False}


def _reached():
    return JSONResponse({"iam01": "handler-reached"}, status_code=HANDLER_STATUS, headers={HANDLER_HDR: "1"})


def _make_wrapper(route: APIRoute, orig):
    tag = id(route)
    if route.dependant.is_coroutine_callable:
        @functools.wraps(orig)
        async def _aw(*a, **kw):
            HITS.append(tag)
            if MODE["block"]:
                return _reached()
            return await orig(*a, **kw)
        return _aw

    @functools.wraps(orig)
    def _sw(*a, **kw):
        HITS.append(tag)
        if MODE["block"]:
            return _reached()
        return orig(*a, **kw)
    return _sw


@pytest.fixture(scope="module", autouse=True)
def _handler_recorder():
    saved = []
    for r in APP.routes:
        if isinstance(r, APIRoute) and not (r.dependant.is_gen_callable or r.dependant.is_async_gen_callable):
            saved.append((r, r.dependant.call))
            r.dependant.call = _make_wrapper(r, r.dependant.call)
    yield
    for r, orig in saved:
        r.dependant.call = orig


# ───────────────────────────── dünya (tohum) ─────────────────────────────
INTERNAL_PRODUCT_FIELDS = {
    "cost_price": 4321.987, "purchase_price": 4322.987, "alis_fiyati": 4323.987, "buy_price": 4324.987,
    "supplier": "iam01-secret-supplier", "tedarikci": "iam01-secret-tedarikci",
    "supplier_code": "iam01-secret-supcode", "member_price": 4325.987, "member_price_1": 4326.987,
    "member_price_2": 4327.987, "member_price_3": 4328.987, "margin": 4329.987, "profit": 4330.987,
    "profit_margin": 4331.987, "kar": 4332.987, "kar_marji": 4333.987,
    "ticimax_fields": {"iam01-secret-ticimax": 1}, "markup_rate": 4334.987,
    "admin_notes": "iam01-secret-admin-notes", "internal_notes": "iam01-secret-internal-notes",
    "vendor": "iam01-secret-vendor", "vendor_id": "iam01-secret-vendor-id",
}
_SECRET_NUMBERS = [str(v) for v in INTERNAL_PRODUCT_FIELDS.values() if isinstance(v, float)]
_SECRET_STRINGS = ["iam01-secret"]


def _seed_probe_docs():
    """Yol parametresi PROBE ile eşleşen iş kayıtları: yanlışlıkla çalışan bir handler GERÇEK bir kaydı
    değiştirir/siler → anlık görüntü karşılaştırması bunu yakalar."""
    async def _s():
        db = deps.db
        prod = {"id": PROBE, "name": "IAM01 Urun", "slug": PROBE, "price": 100.0, "stock": 5,
                "is_active": True, "category_id": PROBE, "category_ids": [PROBE], "images": ["https://cdn.example.test/a.jpg"],
                "brand": "IAM", "barcode": "8690000000001", "stock_code": "IAM01",
                "variants": [{"id": PROBE + "-v", "size": "M", "color": "Siyah", "stock": 5, "price": 100.0,
                              "barcode": "8690000000002", **INTERNAL_PRODUCT_FIELDS}],
                "csv_card_id": "IAM01CARD", "color": "Siyah", **INTERNAL_PRODUCT_FIELDS}
        sib = dict(prod, id=PROBE + "-2", slug=PROBE + "-2", name="IAM01 Urun 2", color="Beyaz",
                   variants=[dict(prod["variants"][0], id=PROBE + "-v2")])
        await db.products.insert_many([prod, sib])
        await db.categories.insert_one({"id": PROBE, "name": "IAM01 Kategori", "slug": PROBE, "is_active": True})
        await db.orders.insert_one({"id": PROBE, "order_number": PROBE, "status": "awaiting_payment",
                                    "payment_status": "pending", "payment_method": "credit_card",
                                    "total": 100.0, "user_id": "u-nobody", "email": "probe@example.test",
                                    "items": [{"product_id": PROBE, "quantity": 1, "price": 100.0}]})
        await db.users.insert_one({"id": PROBE, "email": "probe-user@example.test", "is_admin": False,
                                   "is_active": True, "first_name": "Probe", "token_version": 0})
        await db.roles.insert_one({"id": PROBE, "name": "probe role", "permissions": ["orders.view"]})
        await db.page_blocks.insert_one({"id": PROBE, "type": "product_slider", "page": "home",
                                         "is_active": True, "sort_order": 0, "title": "IAM01",
                                         "settings": {"product_ids": [PROBE]}, "images": [], "links": []})
        await db.full_look_pages.insert_one({"_id": "main", "title": "Full Look", "revision": 1, "looks": [
            {"id": PROBE, "title": "IAM01 Look", "is_active": True, "image": "https://cdn.example.test/a.jpg",
             "source_product_id": PROBE, "product_ids": [PROBE, PROBE + "-2"]}]})
        for coll, extra in (("coupons", {"code": "IAM01PROBE", "is_active": True}),
                            ("banners", {"is_active": True, "title": "IAM01"}),
                            ("pages", {"slug": PROBE, "title": "IAM01", "is_active": True}),
                            ("customer_returns", {"order_id": PROBE, "status": "created"}),
                            ("campaigns", {"name": "IAM01", "is_active": True}),
                            ("gift_cards", {"code": "IAM01PROBE", "balance": 50.0}),
                            ("bank_accounts", {"bank_name": "IAM Bank", "iban": "TR000000000000000000000001"}),
                            ("product_reviews", {"product_id": PROBE, "status": "approved", "rating": 5}),
                            ("tickets", {"subject": "IAM01", "status": "open"})):
            await db[coll].insert_one({"id": PROBE, **extra})
    id_run(_s())


def _snapshot() -> dict:
    async def _s():
        out = {}
        for name in sorted(await deps.db.list_collection_names()):
            if name in _LOG_COLLS:
                continue
            docs = await deps.db[name].find({}, {"_id": 0}).to_list(100000)
            if docs:
                out[name] = sorted(json.dumps(d, sort_keys=True, default=str) for d in docs)
        return out
    return id_run(_s())


def _diff(before: dict, after: dict) -> list:
    return [f"{n}: {len(before.get(n, []))}→{len(after.get(n, []))}"
            + ("" if len(before.get(n, [])) != len(after.get(n, [])) else " (içerik değişti)")
            for n in sorted(set(before) | set(after)) if before.get(n) != after.get(n)]


_ALL_KEYS = None


def _all_perm_keys() -> list:
    global _ALL_KEYS
    if _ALL_KEYS is None:
        from permissions import ALL_PERMISSION_KEYS
        _ALL_KEYS = sorted(set(ALL_PERMISSION_KEYS) | set(ROUTE_PERMS))
    return _ALL_KEYS


class World:
    """Tohum dünyası. Kullanıcılar TEMBEL (ilk erişimde) oluşturulur — matris ~2800 test, gereksiz tohum yok."""

    def __init__(self, probe_docs: bool = True):
        if probe_docs:
            _seed_probe_docs()
        self._n = 0
        self._cache = {}

    def _once(self, name, make):
        if name not in self._cache:
            self._cache[name] = make()
        return self._cache[name]

    @property
    def cust(self) -> dict:
        return self._once("cust", lambda: id_seed_user(kind="customer", email="iam01-cust@example.test"))

    @property
    def staff_empty(self) -> dict:
        """Rolü VAR ama rolün hiç izni yok."""
        def _mk():
            id_seed_role("r_iam_empty", [], "IAM bos rol")
            return id_seed_user(kind="staff", role_id="r_iam_empty", email="iam01-empty@example.test")
        return self._once("staff_empty", _mk)

    @property
    def staff_norole(self) -> dict:
        """is_admin=True ama atanmış rol veritabanında YOK."""
        return self._once("staff_norole", lambda: id_seed_user(kind="staff", role_id="r_iam_missing",
                                                               email="iam01-norole@example.test"))

    def staff_with(self, perms) -> dict:
        self._n += 1
        rid = f"r_iam_{self._n}"
        id_seed_role(rid, list(perms), rid)
        return id_seed_user(kind="staff", role_id=rid, email=f"iam01-s{self._n}@example.test")

    def super_admin(self) -> dict:
        return self._once("super", lambda: id_seed_user(kind="super", email="iam01-super@example.test"))

    @staticmethod
    def hdr(user) -> dict:
        return id_auth(id_token(user)) if user else {}


@pytest.fixture
def world():
    return World()


@pytest.fixture
def world_lite():
    return World(probe_docs=False)


def _call(client, e, headers=None):
    HITS.clear()
    kw = {"headers": dict(headers or {})}
    if e["method"] in _BODY_METHODS:
        kw["json"] = {}
    res = client.request(e["method"], e["url"], **kw)
    reached = id(e["route"]) in HITS
    return res, reached


def _assert_denied(client, e, callers):
    """callers: [(etiket, başlıklar, izinli durum kodları)]. Hepsi ret almalı; bağımlılıkla korunan uçta
    handler'a hiç ulaşılmamalı; tüm çağrılar boyunca iş verisi (günlükler hariç) DEĞİŞMEMELİ."""
    MODE["block"] = False
    before = _snapshot()
    for label, headers, allowed in callers:
        res, reached = _call(client, e, headers)
        assert res.status_code in allowed, \
            f"[{label}] {e['key']} → {res.status_code} (beklenen {allowed}): {res.text[:200]}"
        assert res.headers.get(HANDLER_HDR) is None
        if e["cls"] != "optional":
            assert not reached, f"[{label}] {e['key']}: yetkisiz çağrıda handler ÇALIŞTI"
    diff = _diff(before, _snapshot())
    assert not diff, f"{e['key']}: yetkisiz çağrılar iş verisini değiştirdi: {diff}"


# ───────────────────────────── 0) kapsam / sınıflandırma ─────────────────────────────
def _read_allowlist(path: Path = ALLOWLIST_FILE) -> dict:
    out = {}
    for ln, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        assert "|" in line, f"allowlist satır {ln}: 'METHOD PATH | gerekçe' biçiminde olmalı"
        route, reason = (x.strip() for x in line.split("|", 1))
        assert reason, f"allowlist satır {ln}: gerekçe boş"
        assert re.fullmatch(r"(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) /\S*", route), f"satır {ln}: {route!r}"
        assert route not in out, f"allowlist satır {ln}: tekrar eden kayıt {route}"
        out[route] = reason
    return out


def test_route_inventory_is_complete():
    """Envanter gerçekten tüm uygulamayı kapsıyor; her rota tam olarak bir sınıfa düşüyor."""
    assert len(ENTRIES) >= 1000, f"beklenmedik derecede az rota: {len(ENTRIES)}"
    assert sum(BY_CLS.values()) == len(ENTRIES)
    assert BY_CLS["staff"] + BY_CLS["perm"] + BY_CLS["super"] > 800
    print(f"\nIAM-01 rota envanteri: {len(ENTRIES)} yöntem+yol; sınıflar: {dict(BY_CLS)}; "
          f"gölgelenen: {sum(e['shadowed'] for e in ENTRIES)}")


def test_public_surface_matches_reviewed_allowlist():
    """Herkese açık yüzey = gözden geçirilmiş allowlist (fazlası da eksiği de KIRMIZI)."""
    allow = _read_allowlist()
    public = {e["key"] for e in ENTRIES if e["cls"] == "public"}
    guest_optional = {e["key"] for e in ENTRIES if e["cls"] == "optional" and e["key"] not in BODY_GUARDED_LOGIN}
    surface = public | guest_optional
    new = sorted(surface - set(allow))
    stale = sorted(set(allow) - surface)
    assert not new, ("Gözden geçirilmemiş YENİ herkese açık uç(lar) — yetki bağımlılığı ekleyin ya da "
                     "public_routes_allowlist.txt'ye gerekçesiyle ekleyin:\n  " + "\n  ".join(new))
    assert not stale, "allowlist'te olup artık herkese açık OLMAYAN/silinmiş uç(lar):\n  " + "\n  ".join(stale)
    missing_bg = sorted(BODY_GUARDED_LOGIN - {e["key"] for e in ENTRIES if e["cls"] == "optional"})
    assert not missing_bg, f"BODY_GUARDED_LOGIN'de optional olmayan/olmayan uç: {missing_bg}"


def test_staff_any_surface_matches_reviewed_list():
    """IAM-02: yalnız require_admin ile korunan ("herhangi bir personel") uçlar = gözden geçirilmiş ortak
    liste. Yeni bir panel ucu sayfasının iznini almadan eklenirse (ya da listedeki uç izne bağlanırsa)
    test KIRMIZI olur. Önceden 735 uç bu sınıftaydı."""
    shared = _read_allowlist(STAFF_SHARED_FILE)
    staff = {e["key"] for e in ENTRIES if e["cls"] == "staff"}
    new = sorted(staff - set(shared))
    stale = sorted(set(shared) - staff)
    assert not new, ("Sayfa/özellik izni OLMAYAN yeni panel ucu — require_permission(<sayfa izni>) kullanın ya da "
                     "staff_shared_routes_allowlist.txt'ye gerekçesiyle ekleyin:\n  " + "\n  ".join(new))
    assert not stale, "listede olup artık 'her personel' OLMAYAN uç(lar):\n  " + "\n  ".join(stale)
    assert len(staff) <= 25, f"'her personel' yüzeyi beklenmedik derecede büyüdü: {len(staff)}"


def test_admin_paths_are_never_public():
    """/admin altındaki uçlar yalnız gerekçeli istisnalarla herkese açık olabilir (allowlist'te)."""
    allow = _read_allowlist()
    leaked = [e["key"] for e in ENTRIES if "/admin" in e["path"] and e["cls"] in ("public", "optional")
              and e["key"] not in allow]
    assert not leaked, leaked


def test_no_code_runs_before_auth_dependency():
    """Korunan uçlarda yetki bağımlılığından ÖNCE başka bir bağımlılık (DB okuma/önbellek ısıtma vb.)
    çalışmaz — yetkisiz istek uç koduna hiç dokunmaz."""
    bad = {e["key"]: _pre_auth_deps(e["route"]) for e in GUARDED}
    bad = {k: v for k, v in bad.items() if v}
    assert not bad, bad


def test_matrix_detects_a_missing_guard(id_client, world):
    """Kendi kendini doğrulama (mutasyon): bir ucun yetki kapısı devre dışı kalırsa matris KIRMIZI olur —
    hem durum kodu hem 'handler çalıştı' hem de iş verisi değişikliği yakalanır."""
    e = next(x for x in ENTRIES if x["key"] == "DELETE /api/admin/roles/{role_id}")
    assert e["cls"] == "super"
    APP.dependency_overrides[deps.require_super_admin] = lambda: {"id": "x", "is_super_admin": True}
    try:
        with pytest.raises(AssertionError, match="beklenen"):
            _assert_denied(id_client, e, [("token-yok", {}, (401, 403))])
        assert id(e["route"]) in HITS
    finally:
        APP.dependency_overrides.clear()


def test_every_required_permission_is_grantable():
    """require_permission ile istenen her izin rol kataloğunda var (yoksa yalnız süper admin erişebilir)."""
    from permissions import ALL_PERMISSION_KEYS
    extra_ok = {"customers.export", "customers.credentials"}  # katalog dışı, belgeli ince izinler
    unknown = sorted(set(ROUTE_PERMS) - set(ALL_PERMISSION_KEYS) - extra_ok)
    assert not unknown, unknown


# ───────────────────────────── 1) token yok ─────────────────────────────
@pytest.mark.parametrize("e", GUARDED + BODY_GUARDED, ids=_ids(GUARDED + BODY_GUARDED))
def test_unauthenticated_rejected(id_client, world, e):
    _assert_denied(id_client, e, [("token-yok", {}, (401, 403))])


# ──────────── 2-4) personel uçları: müşteri / boş rol / olmayan rol / yanlış izin → ret ────────────
@pytest.mark.parametrize("e", STAFF_ANY, ids=_ids(STAFF_ANY))
def test_unprivileged_callers_rejected_on_staff_routes(id_client, world, e):
    callers = [
        ("müşteri", World.hdr(world.cust), (401, 403)),
        ("boş-rol", World.hdr(world.staff_empty), (403,)),
        ("rol-yok", World.hdr(world.staff_norole), (403,)),
    ]
    if e["cls"] == "super":
        # Katalogdaki (ve rotaların istediği) HER izin — ama '*' yok.
        callers.append(("tüm-izinler-süper-değil", World.hdr(world.staff_with(_all_perm_keys())), (403,)))
    elif e["cls"] == "perm":
        # Gereken izin HARİÇ her izin (alan-okuma uçlarında: alan DIŞI her izin).
        if e.get("area_read"):
            areas = {p.split(".", 1)[0] + "." for p in e["perms"]}
            wrong = [p for p in _all_perm_keys() if not any(p.startswith(a) for a in areas)]
        else:
            wrong = [p for p in _all_perm_keys() if p not in e["perms"]]
        callers.append(("yanlış-izin", World.hdr(world.staff_with(wrong)), (403,)))
    _assert_denied(id_client, e, callers)


# ───────────────────────────── 5) en az yetkili doğru arayan kabul edilir ─────────────────────────────
@pytest.mark.parametrize("e", GUARDED, ids=_ids(GUARDED))
def test_least_privileged_caller_passes_auth(id_client, world_lite, e):
    """Pozitif kontrol: sınıflandırma doğru — doğru arayan yetki katmanını geçer (handler'a ulaşır ya da
    yalnız parametre doğrulaması 422 alır). Handler bu adımda BLOKLANIR (yan etki yok)."""
    world = world_lite
    if e["cls"] == "customer":
        user = world.cust
    elif e["cls"] == "super":
        user = world.super_admin()
    elif e["cls"] == "perm":
        user = world.staff_with(e["perms"])  # YALNIZ gereken izin
    else:
        user = world.staff_with(["dashboard.view"])  # herhangi tek bir izin
    MODE["block"] = True
    try:
        res, reached = _call(id_client, e, World.hdr(user))
    finally:
        MODE["block"] = False
    ok = res.status_code == HANDLER_STATUS and reached or res.status_code == 422
    assert ok, f"{e['key']} ({e['cls']} {e['perms']}) doğru arayanla {res.status_code}: {res.text[:200]}"


# ───────────────────────────── 6) alan düzeyi: iç alanlar vitrine sızmaz ─────────────────────────────
FIELD_PROBES = [
    ("GET", "/api/products", None),
    ("GET", "/api/products?search=IAM01", None),
    ("GET", f"/api/products/{PROBE}", None),
    ("GET", f"/api/products/{PROBE}/similar", None),
    ("GET", f"/api/products/{PROBE}/color-siblings", None),
    ("GET", f"/api/products/{PROBE}/combine-products", None),
    ("GET", "/api/products/slider-feed", None),
    ("GET", "/api/products/search/popular", None),
    ("POST", "/api/products/cart-pricing", {"product_ids": [PROBE, PROBE + "-2"], "variant_ids": [PROBE + "-v"]}),
    ("POST", "/api/products/cart-suggestions", {"product_ids": [PROBE], "items": [{"product_id": PROBE}]}),
    ("POST", "/api/products/checkout-deals", {"product_ids": [PROBE], "items": [{"product_id": PROBE}]}),
    ("GET", "/api/categories", None),
    ("GET", f"/api/categories/{PROBE}", None),
    ("GET", "/api/full-look", None),
    ("GET", "/api/page-blocks", None),
    ("GET", f"/api/page-blocks/{PROBE}", None),
    ("GET", "/api/sale-menu", None),
    ("GET", f"/api/reviews/product/{PROBE}", None),
    ("GET", f"/api/size-tables-public/{PROBE}", None),
    ("GET", "/api/variants", None),
]


def _leaks(obj, path="$"):
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in INTERNAL_PRODUCT_FIELDS:
                out.append(f"{path}.{k}")
            out.extend(_leaks(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(_leaks(v, f"{path}[{i}]"))
    return out


@pytest.mark.parametrize("who", ["anonim", "müşteri"])
def test_public_catalog_never_returns_internal_fields(id_client, world, kv_mongomock_convert, who):
    hdr = World.hdr(world.cust) if who == "müşteri" else {}
    checked = 0
    problems = []
    for method, url, body in FIELD_PROBES:
        res = id_client.request(method, url, headers=hdr, **({"json": body} if body is not None else {}))
        assert res.status_code < 500, f"{method} {url} → {res.status_code}"
        text = res.text
        for s in _SECRET_STRINGS + _SECRET_NUMBERS:
            if s in text:
                problems.append(f"{method} {url}: değer sızıntısı {s!r}")
        try:
            data = res.json()
        except Exception:
            data = None
        if data is not None:
            problems.extend(f"{method} {url}: anahtar {p}" for p in _leaks(data))
        if res.status_code == 200 and PROBE in text:
            checked += 1
    assert not problems, "\n".join(problems)
    # Kendi kendini doğrulama: tohum ürün vitrin yanıtlarının en az bir kısmında gerçekten görünüyor.
    assert checked >= 5, f"tohum ürün yalnız {checked} yanıtta görüldü — test kurgusu boşa çalışıyor olabilir"


@pytest.mark.parametrize("url", ["/api/products/google-merchant-feed.xml", "/api/feeds/google-merchant.xml"])
def test_public_feeds_never_contain_internal_values(id_client, world, url):
    res = id_client.get(url)
    assert res.status_code < 500
    for s in _SECRET_STRINGS + _SECRET_NUMBERS:
        assert s not in res.text, f"{url}: iç değer {s!r} beslemede"


# ───────────────────────────── 7) bulunan ve düzeltilen açıkların hedefli regresyonları ─────────────────────────────
@pytest.mark.parametrize("url", ["/api/integrations/gib/status", "/api/integrations/payment/status",
                                 "/api/integrations/trendyol/status"])
def test_integration_status_endpoints_require_staff(id_client, world, url):
    """Önceden kimliksiz dönüyordu (firma VKN'si/ünvanı, Trendyol satıcı kimliği, iyzico modu)."""
    assert id_client.get(url).status_code == 401
    assert id_client.get(url, headers=World.hdr(world.cust)).status_code == 403
    # IAM-02: artık her personel değil, Entegrasyonlar sayfa izni gerekir.
    assert id_client.get(url, headers=World.hdr(world.staff_with(["dashboard.view"]))).status_code == 403
    assert id_client.get(url, headers=World.hdr(world.staff_with(["integrations.view"]))).status_code == 200


def test_reports_routers_reject_before_cache_warmers(id_client, world, monkeypatch):
    """Önceden /admin/reports*, /analytics-extra/* uçlarında load_dup_dep (DB taraması) yetkiden ÖNCE
    çalışıyordu. Artık kimliksiz istekte hiç çağrılmaz; herkese açık Merchant beslemesi de çağırmaz."""
    from routes import report_dedup
    calls = []
    real = report_dedup.load_dup_order_numbers

    async def _spy():
        calls.append(1)
        return await real()
    monkeypatch.setattr(report_dedup, "load_dup_order_numbers", _spy)
    for url in ("/api/admin/reports/sales-summary", "/api/admin/reports2/stock-valuation",
                "/api/analytics-extra/rfm", "/api/analytics-extra/marketplace-profit"):
        r = id_client.get(url)
        assert r.status_code == 401, (url, r.status_code)
    id_client.get("/api/feeds/google-merchant.xml")
    assert calls == []
    # Kontrol: yetkili (rapor izni olan) personel isteğinde ısıtıcı yine çalışır (rapor davranışı değişmedi).
    r = id_client.get("/api/admin/reports/sales-summary", headers=World.hdr(world.staff_with(["reports.sales"])))
    assert r.status_code < 500 and r.status_code != 403, r.status_code
    assert calls
    # IAM-02: rapor izni OLMAYAN personel 403 alır.
    assert id_client.get("/api/admin/reports/sales-summary",
                         headers=World.hdr(world.staff_with(["dashboard.view"]))).status_code == 403


def _amazon_cfg(**extra):
    from security.crypto import encrypt
    doc = {"key": "amazon_spapi", "client_id": "amzn-client", "client_secret_enc": encrypt("amzn-secret"),
           "refresh_token_enc": encrypt("store-original-refresh"), "selling_partner_id": "STORE"}
    doc.update(extra)
    id_run(deps.db.integration_settings.insert_one(doc))


def _amazon_row():
    return id_run(deps.db.integration_settings.find_one({"key": "amazon_spapi"}, {"_id": 0}))


@pytest.mark.parametrize("stored_state,sent_state", [(None, "attacker"), (None, ""), ("panel-state-1", ""),
                                                     ("panel-state-1", "attacker")])
def test_amazon_oauth_callback_fails_closed_without_matching_state(id_client, world, monkeypatch,
                                                                   stored_state, sent_state):
    """Önceden kayıtlı state YOKSA kontrol atlanıyordu: kimliksiz biri kendi Amazon kodunu göndererek
    mağazanın refresh_token'ını kendi satıcı hesabıyla değiştirebiliyordu."""
    from routes import amazon_spapi
    _amazon_cfg(**({"oauth_state": stored_state} if stored_state else {}))
    exchanged = []

    async def _no_exchange(*a, **k):
        exchanged.append(a)
        return {"refresh_token": "attacker-refresh"}
    monkeypatch.setattr(amazon_spapi, "_exchange_code_for_refresh", _no_exchange)
    before = _amazon_row()
    r = id_client.get("/api/amazon/spapi/oauth/callback",
                      params={"spapi_oauth_code": "attacker-code", "state": sent_state,
                              "selling_partner_id": "ATTACKER"}, follow_redirects=False)
    assert r.status_code in (302, 307) and "status=state_mismatch" in r.headers["location"]
    assert exchanged == []
    assert _amazon_row() == before


def test_amazon_oauth_state_is_single_use(id_client, world, monkeypatch):
    from routes import amazon_spapi
    _amazon_cfg(oauth_state="panel-state-2")

    async def _exchange(*a, **k):
        return {"refresh_token": "new-refresh"}
    monkeypatch.setattr(amazon_spapi, "_exchange_code_for_refresh", _exchange)
    q = {"spapi_oauth_code": "c1", "state": "panel-state-2", "selling_partner_id": "STORE"}
    r1 = id_client.get("/api/amazon/spapi/oauth/callback", params=q, follow_redirects=False)
    assert "status=connected" in r1.headers["location"]
    assert "oauth_state" not in _amazon_row()
    r2 = id_client.get("/api/amazon/spapi/oauth/callback", params=q, follow_redirects=False)
    assert "status=state_mismatch" in r2.headers["location"]
