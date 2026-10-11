"""IAM-02 — "her personel" (yalnız require_admin) panel uçlarının en az yetkiye indirilmesi.

Önceden 735 panel ucu herhangi bir personel rolüyle çağrılabiliyordu. Artık her uç, onu kullanan ekranın
iznini ister (okuma → sayfanın görüntüleme izni; yazma → yeni/uyuyan izinler). Bu modül şunları kanıtlar:

  1. İzin kataloğu + tek seferlik geçiş tablosu tutarlı: geçiş YALNIZ önceden hiçbir uçta zorunlu olmayan
     ("uyuyan") ya da yeni izinleri verir (yetki yükseltme yok), her izin rol ekranında işaretlenebilir.
  2. Geçiş (permissions.PERMISSION_UPGRADE_GRANTS + admin_rbac.grant_least_privilege_upgrade_permissions):
     sayfa iznine sahip MEVCUT her role o sayfanın yazma izinlerini bir kez ekler; yalnız ekler, süper
     admine dokunmaz, idempotenttir, geçişten sonra kaldırılan izni GERİ EKLEMEZ.
  3. "Kimse bugünkü erişimini kaybetmez": frontend/src taranır; her admin sayfasının (adminNav.js →
     PAGE_PERMS) çağırdığı her uç, o sayfanın iznine sahip (geçiş sonrası) rolle yetki katmanını geçer.
  4. GET /api/settings bölünmesi: herkese açık uç yalnız vitrin alanlarını döner ve asla yazmaz; tam belge
     yalnız settings.view ile; ürün formu varsayılanları products.view ile.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from starlette.routing import Match

from id_harness import id_auth, id_find, id_findall, id_run, id_seed_role, id_seed_user, id_token
import server  # noqa: F401
from routes import deps
import permissions as P
import test_iam01_authz_matrix as M

pytestmark = pytest.mark.usefixtures("id_env")

FRONT = Path(__file__).resolve().parents[3] / "frontend" / "src"

# Bu değişiklikten ÖNCE varsayılan rollerin izinleri (permissions.DEFAULT_ROLES geçişle genişletilmeden önce).
ORIGINAL_DEFAULT_ROLES = {
    "operations": ["dashboard.view", "products.view", "orders.view", "orders.update_status", "orders.note",
                   "orders.invoice", "orders.cargo", "orders.trendyol_import", "orders.cancelled_tab",
                   "returns.view", "returns.approve", "returns.reject", "returns.expense_note",
                   "returns.cargo_rebook", "questions.view", "questions.answer", "questions.sync",
                   "customers.view"],
    "finance": ["dashboard.view", "orders.view", "orders.invoice", "returns.view", "returns.iyzico_refund",
                "reports.financial", "reports.sales", "reports.returns", "reports.export", "settings.view",
                "settings.company", "integrations.dogan_edonusum"],
    "manufacturing": ["dashboard.view", "manufacturing.view", "manufacturing.create", "manufacturing.update_stage",
                      "manufacturing.finance", "manufacturing.files", "products.view"],
    "customer_service": ["dashboard.view", "orders.view", "orders.note", "returns.view", "returns.approve",
                         "returns.reject", "questions.view", "questions.answer", "questions.sync", "customers.view"],
}

# IAM-02 ÖNCESİ zaten require_permission ile korunan 93 uç (bu değişiklik bunlara dokunmadı). Sayfadaki ince
# taneli düğmeler (ör. "Durum Değiştir", "Fatura Kes") önceden de ayrı izin istiyordu → kapsama taramasında
# "kayıp" sayılmaz; aşağıdaki test IAM-02'nin hiçbir ucu önceden zorunlu bir YAZMA iznine bağlamadığını doğrular.
PRE_IAM02_PERM_ROUTES = frozenset("""
DELETE /api/admin/email-marketing/templates/{template_id}
DELETE /api/admin/members/{mid}
DELETE /api/categories/{category_id}
DELETE /api/customer-risk/blocked/{bid}
DELETE /api/orders/{order_id}
DELETE /api/products/{product_id}
DELETE /api/products/{product_id}/permanent
DELETE /api/settings/bank-accounts/{acc_id}
GET /api/admin/activity
GET /api/admin/chargebacks
GET /api/admin/chargebacks/order/{order_id}
GET /api/admin/chargebacks/{chargeback_id}
GET /api/admin/consents/export.xlsx
GET /api/admin/email-marketing/audience
GET /api/admin/email-marketing/campaigns
GET /api/admin/email-marketing/campaigns/{campaign_id}/missing
GET /api/admin/email-marketing/campaigns/{campaign_id}/report
GET /api/admin/email-marketing/campaigns/{campaign_id}/report.xlsx
GET /api/admin/email-marketing/diagnose
GET /api/admin/email-marketing/products
GET /api/admin/email-marketing/provider-status
GET /api/admin/email-marketing/settings
GET /api/admin/email-marketing/suppressions
GET /api/admin/email-marketing/templates
GET /api/admin/kvkk/requests
GET /api/admin/members
GET /api/admin/members/export.xlsx
GET /api/admin/members/{mid}
GET /api/admin/members/{mid}/360/print
GET /api/admin/rooftr/return-orders/export
GET /api/admin/sale-menu
GET /api/orders/diagnose/invoice
GET /api/orders/returns/gider-pusulasi/export
GET /api/orders/returns/gider-pusulasi/print-range
GET /api/orders/returns/{return_id}/refund-preview
GET /api/orders/{order_id}/cargo-label
GET /api/orders/{order_id}/invoice/print
GET /api/orders/{order_id}/manual-invoice-file
GET /api/products/{product_id}/barcode-card
PATCH /api/admin/chargebacks/{chargeback_id}
POST /api/admin/chargebacks
POST /api/admin/coupons
POST /api/admin/coupons/exceptions
POST /api/admin/email-marketing/campaigns
POST /api/admin/email-marketing/campaigns/{campaign_id}/send-missing
POST /api/admin/email-marketing/preview
POST /api/admin/email-marketing/templates
POST /api/admin/email-marketing/test
POST /api/admin/email-marketing/validate-provider
POST /api/admin/gift-cards
POST /api/admin/kvkk/requests/{rid}/apply
POST /api/admin/kvkk/requests/{rid}/close
POST /api/admin/mobile/push/send
POST /api/campaigns
POST /api/compliance/pii-retention/config
POST /api/compliance/pii-retention/run
POST /api/customer-risk/block
POST /api/integrations/dogan/settings
POST /api/integrations/iyzico/refund
POST /api/integrations/iyzico/refund/resolve
POST /api/integrations/iyzico/settings
POST /api/integrations/trendyol/settings
POST /api/integrations/{marketplace}/settings
POST /api/orders/bulk-create-invoice
POST /api/orders/bulk-status
POST /api/orders/cargo/mng-settings
POST /api/orders/returns/bulk-mark-refunded-silent
POST /api/orders/returns/gider-pusulasi/clear-numbers
POST /api/orders/returns/vouchers/recompute
POST /api/orders/returns/vouchers/set-number
POST /api/orders/returns/{return_id}/approve
POST /api/orders/returns/{return_id}/gider-pusulasi
POST /api/orders/returns/{return_id}/refund-pay
POST /api/orders/returns/{return_id}/reissue-barcode
POST /api/orders/returns/{return_id}/reject
POST /api/orders/returns/{return_id}/reship
POST /api/orders/returns/{return_id}/update-approval
POST /api/orders/{order_id}/create-invoice
POST /api/orders/{order_id}/reset-invoice
POST /api/orders/{order_id}/set-item-discounts
POST /api/orders/{order_id}/upload-invoice
POST /api/provider-settings/{kind}/config
POST /api/settings
POST /api/settings/bank-accounts
POST /api/settings/bank-accounts/{acc_id}/default
POST /api/settings/email-smtp
PUT /api/admin/coupons/{cid}
PUT /api/admin/email-marketing/settings
PUT /api/admin/gift-cards/{card_id}
PUT /api/admin/sale-menu
PUT /api/orders/{order_id}/mark-paid
PUT /api/orders/{order_id}/status
PUT /api/settings/tenant-config
""".strip().splitlines())

# Sayfa izni olarak kullanılan ve geçişte VERİLMEYEN izinler (rol bunlara zaten sahipse sayfayı görür).
PAGE_VIEW_PERMS = {"dashboard.view", "products.view", "orders.view", "returns.view", "questions.view",
                   "customers.view", "manufacturing.view", "campaigns.view", "integrations.view", "tasarim.view",
                   "settings.view", "admin.logs", "audit.read", "admin.compliance", "tasarim.email",
                   "returns.refund_pay"}


# ───────────────────────────── 1) katalog / tablo değişmezleri ─────────────────────────────
def test_upgrade_grants_never_escalate():
    """Geçiş tablosu önceden zorunlu (PRE_ENFORCED) bir izni ASLA vermez; her anahtar ve çapa katalogda."""
    keys = set(P.ALL_PERMISSION_KEYS)
    assert not set(P.PERMISSION_UPGRADE_GRANTS) & P.PRE_ENFORCED_PERMISSIONS
    for perm, anchors in P.PERMISSION_UPGRADE_GRANTS.items():
        assert perm in keys, perm
        assert anchors and all(a in keys for a in anchors), (perm, anchors)


def test_every_route_permission_is_either_a_page_permission_or_migrated():
    """Rotaların istediği her izin: sayfa görüntüleme izni, önceden zorunlu izin ya da geçişle verilen izin.
    Yeni bir yazma izni eklenip geçiş tablosuna konmazsa mevcut roller sessizce erişim kaybederdi → KIRMIZI."""
    extra_ok = {"customers.export", "customers.credentials"}  # IAM-01 öncesi, katalog dışı ince izinler
    allowed = PAGE_VIEW_PERMS | P.PRE_ENFORCED_PERMISSIONS | set(P.PERMISSION_UPGRADE_GRANTS) | extra_ok
    stray = sorted(set(M.ROUTE_PERMS) - allowed)
    assert not stray, stray


def test_no_iam02_route_uses_a_previously_enforced_write_permission():
    """IAM-02 ile izne bağlanan uçlar önceden zorunlu bir YAZMA iznini (ör. orders.update_status) kullanmaz —
    kullansaydı o izne sahip olmayan ama sayfayı gören roller erişim kaybederdi (geçiş bu izinleri veremez)."""
    fine = P.PRE_ENFORCED_PERMISSIONS - PAGE_VIEW_PERMS
    moved = sorted(e["key"] for e in M.ENTRIES if e["cls"] == "perm" and set(e["perms"]) & fine
                   and e["key"] not in PRE_IAM02_PERM_ROUTES)
    assert not moved, moved


def test_default_roles_are_shipped_migrated():
    """Geçişten SONRA ilk kez oluşturulan sistem rolü de geçirilmiş izinlerle yazılır."""
    by_id = {r["id"]: r for r in P.DEFAULT_ROLES}
    for rid, original in ORIGINAL_DEFAULT_ROLES.items():
        perms = set(by_id[rid]["permissions"])
        assert set(original) <= perms
        assert perms == (set(original) | set(P.upgrade_grants_for(original))
                         | set(P.DEFAULT_ROLE_SENSITIVE_ALLOW.get(rid, ()))), rid
    assert by_id["super_admin"]["permissions"] == ["*"]


# ───────────────────────────── 2) tek seferlik geçiş ─────────────────────────────
def _run_migration():
    from routes.admin_rbac import grant_least_privilege_upgrade_permissions
    return id_run(grant_least_privilege_upgrade_permissions())


def _role(rid):
    return id_find("roles", {"id": rid}) or {}


def test_migration_preserves_todays_access_for_every_existing_role():
    """Geçiş: mevcut role YALNIZ zaten izni olduğu alandaki yeni zorunlu izinler eklenir (o alandaki ekranını
    kaybetmez); hassas izinler (denetim #3) ve önceden zorunlu izinler ASLA otomatik eklenmez;
    boş rol ve süper admin değişmez."""
    for rid, perms in ORIGINAL_DEFAULT_ROLES.items():
        id_seed_role(rid, perms)
    id_seed_role("r_invoice_only", ["orders.invoice", "returns.approve"])
    id_seed_role("r_audit_only", ["audit.read"])
    id_seed_role("super_admin", ["*"])
    id_seed_role("r_empty", [])
    before = {r: set(p) for r, p in list(ORIGINAL_DEFAULT_ROLES.items()) +
              [("r_invoice_only", ["orders.invoice", "returns.approve"]), ("r_audit_only", ["audit.read"])]}

    res = _run_migration()
    assert res["skipped"] is False
    for rid, had in before.items():
        if "*" in had or not had:
            continue
        now = set(_role(rid)["permissions"])
        assert had <= now, rid                                   # hiçbir izin silinmez
        assert set(P.upgrade_grants_for(had)) <= now, rid        # alanındaki ekran erişimi korunur
        assert not ((now - had) & P.PRE_ENFORCED_PERMISSIONS), rid   # yükseltme yok
        assert not ((now - had) & P.SENSITIVE_NO_AUTO_GRANT), rid    # hassas izin otomatik verilmez
    assert _role("super_admin")["permissions"] == ["*"]
    assert _role("r_empty")["permissions"] == []


def test_migration_runs_once_and_respects_later_admin_edits():
    """İkinci çalıştırma hiçbir şey yapmaz: geçişten sonra süper adminin rol ekranından KALDIRDIĞI izin geri
    eklenmez ("kullanıcının yaptığı değişiklik kalıcıdır")."""
    id_seed_role("r_ops", ["orders.view"])
    _run_migration()
    assert "orders.ship" in _role("r_ops")["permissions"]
    id_run(deps.db.roles.update_one({"id": "r_ops"}, {"$pull": {"permissions": "orders.ship"}}))
    assert _run_migration() == {"skipped": True}
    assert "orders.ship" not in _role("r_ops")["permissions"]


def test_migration_is_idempotent_without_flag():
    """Bayrak yazılamadan yarıda kesilse bile tekrar çalışması güvenli: çift kayıt yok, silme yok."""
    id_seed_role("r_ops", ["orders.view", "orders.edit"])
    _run_migration()
    first = list(_role("r_ops")["permissions"])
    id_run(deps.db.settings.delete_one({"id": P.UPGRADE_GRANTS_FLAG}))
    _run_migration()
    second = list(_role("r_ops")["permissions"])
    assert sorted(first) == sorted(second) and len(second) == len(set(second))


def test_migrated_role_keeps_using_newly_permissioned_endpoints(id_client):
    """Uçtan uca: geçişten geçen mevcut rol yeni izinli yazma ucunda yetki katmanını geçer; geçişten SONRA
    ince taneli kurulan (yalnız İmalat) rol 403 alır — en az yetki yeni rollerde geçerli."""
    id_seed_role("r_ops", ["orders.view"])
    _run_migration()
    id_seed_role("r_mfg", ["manufacturing.view"])
    ops = id_seed_user(kind="staff", role_id="r_ops", email="iam02-ops@example.test")
    mfg = id_seed_user(kind="staff", role_id="r_mfg", email="iam02-mfg@example.test")
    url = "/api/orders/iam02-none/note"
    r_ops = id_client.post(url, json={"note": "x"}, headers=id_auth(id_token(ops)))
    r_mfg = id_client.post(url, json={"note": "x"}, headers=id_auth(id_token(mfg)))
    assert r_ops.status_code != 403, r_ops.text
    assert r_mfg.status_code == 403
    assert "orders.note" in r_mfg.json()["detail"]


# ───────────────────────────── 3) sayfa → uç kapsaması (frontend taraması) ─────────────────────────────
_EXPORT = re.compile(r"^export\s+(default\s+)?(?:async\s+)?(?:function\s*(\w*)|const\s+(\w+))", re.M)
_IMPORT = re.compile(r"""import\s+(?:(\w+)\s*,?\s*)?(?:\{([^}]*)\})?\s*from\s*["'](\.[^"']+)["']""")
_BASE = re.compile(r"""const\s+(\w+)\s*=\s*(?:`\$\{process\.env\.REACT_APP_BACKEND_URL\}([^`$]*)`|"""
                   r"""process\.env\.REACT_APP_BACKEND_URL\s*(?:\+\s*["']([^"']*)["'])?\s*;)""")
_CALL = re.compile(r"""\b(?:axios|api)\.(get|post|put|patch|delete)\(\s*`\$\{(\w+(?:\.\w+)*)\}([^`]*)`"""
                   r"""|\bfetch\(\s*`\$\{(\w+(?:\.\w+)*)\}([^`]*)`""")
ENV = "process.env.REACT_APP_BACKEND_URL"

# İnceleme sonucu kabul edilenler: (yöntem yol, sayfa) → gerekçe.
REVIEWED_PAGE_EXCEPTIONS = {
    ("PUT /api/manufacturing/{record_id}", "/admin/urunler"):
        "İmalattan açılan ürünün kaydını 'ürün açıldı' işaretler; akış İmalat sayfasından başlar (o rol "
        "manufacturing.view → geçişle manufacturing.update_stage alır); çağrı hata yutar.",
    ("GET /api/attributes", "/admin/amazon"):
        "Amazon→Eşleştirme sekmesindeki CategoryMapping gelişmiş eşleştirme modali; sekmenin kendisi önceden de "
        "süper-admin uçlarına (category-mapping) dayanıyor",
    ("POST /api/attributes/sync-from-products", "/admin/amazon"):
        "Aynı süper-admin eşleştirme modali (yukarı bkz.)",
}
# AdminLayout içinde yalnız belirli sayfalarda görünen bileşenler.
FILE_PAGE_OVERRIDES = {"components/admin/ReportAssistant.jsx": ["/admin/raporlar/satis"]}


def _read(rel):
    return (FRONT / rel).read_text(encoding="utf-8")


def _resolve(src_rel, spec):
    base = str((FRONT / src_rel).parent / spec)
    for cand in (base, base + ".js", base + ".jsx", base + "/index.js", base + "/index.jsx"):
        c = Path(cand)
        if c.is_file():
            return str(c.resolve().relative_to(FRONT.resolve()))
    return None


def _page_perms():
    txt = _read("lib/adminNav.js")
    rep = re.search(r"const REPORT_PERMS = \[([^\]]*)\]", txt)
    report = re.findall(r'"([^"]+)"', rep.group(1)) if rep else []
    out = {}
    for path, body in re.findall(r'^\s*"(/admin[^"]*)":\s*(\[[^\]]*\]|REPORT_PERMS)', txt, re.M):
        out[path] = report if body == "REPORT_PERMS" else re.findall(r'"([^"]+)"', body)
    sup = re.search(r"SUPER_ONLY_PATHS = \[([^\]]*)\]", txt, re.S)
    supers = set(re.findall(r'"([^"]+)"', sup.group(1))) if sup else set()
    supers |= set(re.findall(r'path:\s*"([^"]+)"[^}]*superOnly:\s*true', txt))
    return out, supers


def _route_components():
    """AdminApp.jsx → {(dosya, dışa aktarım adı|None): [sayfa yolları]}."""
    txt = _read("AdminApp.jsx")
    comp_src = {}
    for default, named, spec in _IMPORT.findall(txt):
        f = _resolve("AdminApp.jsx", spec)
        if not f:
            continue
        if default:
            comp_src[default] = (f, None)
        for n in (named or "").split(","):
            n = n.strip()
            if n:
                comp_src[n] = (f, n)
    out = {}
    for m in re.finditer(r"<Route\s+(?:path=\"([^\"]+)\"|(index))\s+element=\{<(\w+)", txt):
        path = "/admin" if m.group(2) else "/admin/" + m.group(1)
        src = comp_src.get(m.group(3))
        if src:
            out.setdefault(src, []).append(path.split("/:")[0])
    return out


def _import_graph():
    graph = {}
    for p in FRONT.rglob("*.js*"):
        rel = str(p.relative_to(FRONT))
        if ".test." in rel or p.suffix not in (".js", ".jsx"):
            continue
        deps_ = set()
        for _d, _n, spec in _IMPORT.findall(p.read_text(encoding="utf-8")):
            r = _resolve(rel, spec)
            if r:
                deps_.add(r)
        graph[rel] = deps_
    return graph


def _pages_of_file(graph, roots):
    """Her dosya için onu (transitif) render eden sayfa yolları. Kök dosyalarda dışa aktarım bazında."""
    importers = {}
    for f, ds in graph.items():
        for d in ds:
            importers.setdefault(d, set()).add(f)
    root_files = {}
    for (f, name), paths in roots.items():
        root_files.setdefault(f, {}).setdefault(name, set()).update(paths)

    memo = {}

    def up(f, seen=frozenset()):
        if f in memo:
            return memo[f]
        if f in seen:
            return set()
        acc = set()
        if f in root_files:
            for ps in root_files[f].values():
                acc |= ps
        for imp in importers.get(f, ()):
            if imp in ("AdminApp.jsx", "pages/admin/AdminLayout.jsx"):
                continue
            acc |= up(imp, seen | {f})
        memo[f] = acc
        return acc
    return up, root_files


def _route_for(method, url):
    scope = {"type": "http", "path": url, "root_path": "", "method": method, "headers": [], "query_string": b""}
    for r in M.APP.router.routes:
        m, _ = r.matches(scope)
        if m == Match.FULL and isinstance(r, APIRoute):
            return r
    return None


def _frontend_calls():
    """[(dosya, konum, YÖNTEM, url)] — yalnız `${API}/...` biçimli, çözümlenebilir çağrılar."""
    out = []
    for p in list((FRONT / "pages" / "admin").glob("*.jsx")) + list((FRONT / "components" / "admin").rglob("*.js*")) \
            + [FRONT / "lib" / "headerMenu.js"]:
        rel = str(p.relative_to(FRONT))
        if ".test." in rel:
            continue
        txt = p.read_text(encoding="utf-8")
        bases = {ENV: ""}
        for name, tmpl, plus in _BASE.findall(txt):
            bases[name] = tmpl if tmpl else (plus or "")
        for m in _CALL.finditer(txt):
            if m.group(1):
                method, var, rest = m.group(1).upper(), m.group(2), m.group(3)
            else:
                var, rest = m.group(4), m.group(5)
                mm = re.search(r"method\s*:\s*[\"'](\w+)", txt[m.end():m.end() + 500])
                method = mm.group(1).upper() if mm else "GET"
            if var not in bases or rest.startswith("${"):
                continue
            path = (bases[var] + rest).split("?")[0]
            path = re.sub(r"\$\{[^}]*\}", "iam02x", path)
            if not path.startswith("/api/"):
                continue
            out.append((rel, m.start(), method, path))
    return out


def _covered(cls, perms, need, page_super):
    if cls in ("public", "optional", "customer", "staff"):
        return True
    if cls == "super":
        # Süper-admin uçları IAM-02'den ÖNCE de süper-admindi (sayfadaki süper-admin düğmeleri); IAM-02'nin
        # süpere taşıdığı yalnız 3 kasa ucu (gövde zaten süper istiyordu) REVIEWED_PAGE_EXCEPTIONS'ta.
        return True
    perm = perms[0]
    if page_super:
        return True
    anchors = set(P.PERMISSION_UPGRADE_GRANTS.get(perm, ()))
    # Sayfayı gören HER rol (izinlerden herhangi birine sahip) geçmeli.
    return bool(need) and all(q == perm or q in anchors for q in need)


@pytest.mark.skipif(not FRONT.is_dir(), reason="frontend kaynağı yok")
def test_every_admin_page_can_still_call_every_endpoint_it_uses():
    """Kimse erişim kaybetmez: sayfanın menü iznine sahip (geçiş sonrası) rol, sayfanın çağırdığı her ucu
    kullanır. Menüde izin bağı olmayan sayfalar (Görevler, Eğitim) yalnız ortak/herkese açık uç çağırabilir."""
    page_perms, supers = _page_perms()
    roots = _route_components()
    assert len(roots) > 60, "AdminApp rota ayrıştırması başarısız"
    graph = _import_graph()
    up, root_files = _pages_of_file(graph, roots)

    def up_importers(rel, name):
        """`rel` dosyasının `name` dışa aktarımını içe aktaran dosyaların sayfaları."""
        acc = set()
        for f, ds in graph.items():
            if rel not in ds or f in ("AdminApp.jsx", "pages/admin/AdminLayout.jsx"):
                continue
            for _d, named, spec in _IMPORT.findall(_read(f)):
                if _resolve(f, spec) == rel and name in [n.strip() for n in (named or "").split(",")]:
                    acc |= up(f)
        return acc
    calls = _frontend_calls()
    assert len(calls) > 400, f"frontend çağrı taraması başarısız ({len(calls)})"
    problems, checked = [], 0
    for rel, pos, method, url in calls:
        route = _route_for(method, url)
        if route is None:
            continue
        cls, perms = M.classify(route)
        key = f"{method} {route.path}"
        if key in PRE_IAM02_PERM_ROUTES:
            continue  # IAM-02 öncesi ince taneli kapı — bu değişiklik dokunmadı
        txt = _read(rel)
        pages = set(FILE_PAGE_OVERRIDES.get(rel, ()))
        if not pages and rel in root_files:
            exp = None
            for em in _EXPORT.finditer(txt):
                if em.start() <= pos:
                    exp = None if em.group(1) else (em.group(2) or em.group(3))  # default → içe aktarım adı None
            named = root_files[rel]
            if exp in named:
                pages = set(named[exp])
            elif exp is None:
                pages = set().union(*named.values())
            else:
                # Rotaya doğrudan bağlı olmayan dışa aktarım (ör. CurrencyRates → Ayarlar sekmesi): yalnız
                # bu dosyayı içe aktaran sayfalar; hiçbiri değilse (ölü bileşen) denetlenmez.
                pages = up_importers(rel, exp)
                if not pages:
                    continue
        if not pages:
            pages = up(rel)
        for page in sorted(pages):
            if (key, page) in REVIEWED_PAGE_EXCEPTIONS:
                continue
            need = page_perms.get(page, [])
            page_super = page in supers
            checked += 1
            if not need and not page_super:
                if cls not in ("public", "optional", "customer", "staff", "super"):  # super: yalnız süper-admin düğmesi
                    problems.append(f"{page} (herkese açık menü) → {key} [{cls} {perms}] ({rel})")
                continue
            if not _covered(cls, perms, need, page_super):
                problems.append(f"{page} {need} → {key} [{cls} {perms}] ({rel})")
    assert checked > 400, checked
    assert not problems, "Sayfa izni olan rol, sayfanın çağırdığı uca erişemiyor:\n  " + "\n  ".join(sorted(set(problems)))


# ───────────────────────────── 4) GET /api/settings bölünmesi ─────────────────────────────
PUBLIC_SETTINGS_KEYS = {"shipping_fee", "free_shipping_threshold", "payment_methods", "bank_transfer_discount_pct",
                        "cookie_consent"}


def _seed_main_settings():
    id_run(deps.db.settings.insert_one({
        "id": "main", "site_name": "IAM02 Mağaza", "trendyol_markup": 17.5, "barcode_range_start": "8680000000000",
        "barcode_range_end": "8689999999999", "default_vat_rate": 20, "shipping_fee": 90,
        "free_shipping_threshold": 750, "payment_methods": {"credit_card": True, "bank_transfer": False,
                                                            "secret_gateway": "x"},
        "bank_transfer_discount_pct": 3,
        "cookie_consent": {"heading": "Çerez", "body": "Metin", "policy_url": "/kvkk", "policy_label": "KVKK",
                           "internal_note": "gizli"},
        "company_info": {"tax_number": "1234567890"}, "smtp_password": "iam02-secret"}))


def test_public_settings_returns_only_storefront_fields(id_client):
    _seed_main_settings()
    r = id_client.get("/api/settings")
    assert r.status_code == 200
    data = r.json()
    assert set(data) == PUBLIC_SETTINGS_KEYS
    assert data["shipping_fee"] == 90 and data["free_shipping_threshold"] == 750
    assert data["payment_methods"] == {"credit_card": True, "bank_transfer": False}
    assert data["bank_transfer_discount_pct"] == 3
    assert data["cookie_consent"] == {"heading": "Çerez", "body": "Metin", "policy_url": "/kvkk", "policy_label": "KVKK"}
    for leaked in ("17.5", "8680000000000", "1234567890", "iam02-secret", "IAM02 Mağaza", "tenant_config", "gizli"):
        assert leaked not in r.text, leaked


def test_anonymous_settings_get_never_writes(id_client):
    before = id_findall("settings")
    r = id_client.get("/api/settings")
    assert r.status_code == 200 and set(r.json()) == PUBLIC_SETTINGS_KEYS
    assert id_findall("settings") == before == []


def test_full_settings_requires_settings_view(id_client):
    _seed_main_settings()
    id_seed_role("r_settings", ["settings.view"])
    id_seed_role("r_products", ["products.view"])
    s_user = id_seed_user(kind="staff", role_id="r_settings", email="iam02-set@example.test")
    p_user = id_seed_user(kind="staff", role_id="r_products", email="iam02-prd@example.test")
    cust = id_seed_user(kind="customer", email="iam02-cust@example.test")
    assert id_client.get("/api/settings/admin").status_code == 401
    assert id_client.get("/api/settings/admin", headers=id_auth(id_token(cust))).status_code == 403
    assert id_client.get("/api/settings/admin", headers=id_auth(id_token(p_user))).status_code == 403
    r = id_client.get("/api/settings/admin", headers=id_auth(id_token(s_user)))
    assert r.status_code == 200
    body = r.json()
    assert body["trendyol_markup"] == 17.5 and body["barcode_range_start"] == "8680000000000"
    assert "tenant_config" in body and body["shipping_fee"] == 90


def test_full_settings_on_empty_db_returns_defaults_without_writing(id_client):
    sup = id_seed_user(kind="super", email="iam02-super@example.test")
    r = id_client.get("/api/settings/admin", headers=id_auth(id_token(sup)))
    assert r.status_code == 200 and r.json()["id"] == "main" and r.json()["default_vat_rate"] == 10
    assert id_findall("settings") == []


def test_product_defaults_for_product_staff(id_client):
    _seed_main_settings()
    id_seed_role("r_products", ["products.view"])
    id_seed_role("r_orders", ["orders.view"])
    p_user = id_seed_user(kind="staff", role_id="r_products", email="iam02-prd2@example.test")
    o_user = id_seed_user(kind="staff", role_id="r_orders", email="iam02-ord2@example.test")
    assert id_client.get("/api/settings/product-defaults").status_code == 401
    assert id_client.get("/api/settings/product-defaults", headers=id_auth(id_token(o_user))).status_code == 403
    r = id_client.get("/api/settings/product-defaults", headers=id_auth(id_token(p_user)))
    assert r.status_code == 200 and r.json() == {"default_vat_rate": 20, "trendyol_markup": 17.5}
    # Ana ayarda fark yoksa Trendyol entegrasyon kartındaki değere düşer (GET /integrations/trendyol/settings ile aynı).
    id_run(deps.db.settings.update_one({"id": "main"}, {"$unset": {"trendyol_markup": ""}}))
    id_run(deps.db.settings.insert_one({"id": "trendyol", "default_markup": 12}))
    r = id_client.get("/api/settings/product-defaults", headers=id_auth(id_token(p_user)))
    assert r.json()["trendyol_markup"] == 12.0
