"""SDLC-01/02 — statik rota yetki kapısı (route guard gate).

Uygulamayı IMPORT ETMEDEN (motor/Mongo/ortam değişkeni gerekmez) server.py'nin bağladığı
router'ları ve rota dekoratörlerini `ast` ile okur. Şu rotalar için bir yetki bağımlılığı
(require_admin / require_super_admin / require_permission(...) / require_auth / get_current_user)
ZORUNLUDUR:
  * yolu /admin altında olan HER rota (GET dahil),
  * HER POST / PUT / PATCH / DELETE rotası.

Yetki şu yollardan biriyle sağlanmış sayılır:
  - parametre varsayılanı `Depends(<guard>)`,
  - dekoratörde `dependencies=[Depends(<guard>)]`,
  - router seviyesinde `APIRouter(dependencies=[Depends(<guard>)])`,
  - modülde tanımlı ve kendisi bir guard'a bağımlı olan / gövdesinde guard çağıran yardımcı
    (ör. `require_admin_or_mobile`), ya da rota gövdesinde guard'ın DOĞRUDAN çağrılması
    (`await require_admin(credentials)`).

Bilerek herkese açık olan rotalar aşağıdaki ALLOWLIST'te GEREKÇESİYLE listelenir. Yeni bir
herkese açık POST/PUT/PATCH/DELETE (ya da /admin altında yetkisiz) rota eklenirse bu test
KIRMIZI olur: ya yetki bağımlılığı ekleyin ya da (gerçekten herkese açıksa ve kendi doğrulaması
varsa) ALLOWLIST'e gerekçesiyle ekleyin.

NOT: get_current_user OPSİYONEL kimliktir (oturum yoksa None döner); burada "bilinçli olarak
kimlik bağlamı okunuyor" işareti sayılır. Uçtaki asıl yetki kontrolü fonksiyon gövdesindedir.

Çalıştırma:  python -m pytest -q backend/tests/test_route_guards.py
         ya da  python backend/tests/test_route_guards.py   (pytest gerekmez)
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROUTES_DIR = BACKEND / "routes"
SERVER = BACKEND / "server.py"

GUARD_NAMES = {"require_admin", "require_super_admin", "require_permission", "require_auth",
               "get_current_user"}
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}

# ─────────────────────────────────────────────────────────────────────────────────────────────
# Bilerek herkese açık rotalar: "METHOD /api/tam/yol" → gerekçe.
# (Yol, server.py include prefix'i + router prefix'i + dekoratör yolu ile birleşik hâlidir.)
# ─────────────────────────────────────────────────────────────────────────────────────────────
ALLOWLIST: dict[str, str] = {}  # doldurulur: aşağıdaki _ALLOW listesi


def _allow(reason: str, *keys: str) -> None:
    for k in keys:
        ALLOWLIST[k] = reason


# (Liste aşağıda; tarama sonucu ile birebir tutulur.)
# __ALLOWLIST_BEGIN__
_allow("Vitrin/checkout herkese açık okuma: aktif kargo kuralını sepet tutarına göre çözer (PII yok, yazma yok); "
       "tarihsel olarak /admin/rules router'ında duruyor",
       "GET /api/admin/rules/shipping/resolve")
_allow("Vitrin/checkout herkese açık okuma: aktif ödeme tipi indirimlerini çözer (PII yok, yazma yok)",
       "GET /api/admin/rules/payment-discounts/resolve")
_allow("Müşteri stok/fiyat alarmı kaydı — misafir de kullanır; rate-limit 10/dk",
       "POST /api/alerts")
_allow("Atıf (attribution) ziyaret izleme — anonim ziyaretçi; yalnız oturum metriği yazar",
       "POST /api/attribution/track-visit")
_allow("Kimlik doğrulama girişi (sosyal giriş) — sağlayıcı token'ı sunucuda doğrulanır",
       "POST /api/auth/apple", "POST /api/auth/facebook", "POST /api/auth/google",
       "POST /api/auth/google/callback", "POST /api/auth/google/exchange", "POST /api/auth/google/session")
_allow("Kimlik doğrulama girişi — giriş/kayıt/e-posta doğrulama (rate-limit + kilit mekanizması)",
       "POST /api/auth/login", "POST /api/auth/register", "POST /api/auth/verify-email")
_allow("Parola sıfırlama akışı — OTP/e-posta token'ı ile doğrulanır, rate-limit",
       "POST /api/auth/forgot-password/email", "POST /api/auth/forgot-password/request-otp",
       "POST /api/auth/forgot-password/reset", "POST /api/auth/forgot-password/verify-otp")
_allow("Misafir siparişini hesaba çevirme — e-posta kodu + taze sipariş şartı + rate-limit (K2)",
       "POST /api/auth/convert-guest-order", "POST /api/auth/guest-convert/send-code")
_allow("MFA ikinci adım — giriş sırasında kısa ömürlü MFA bileti ile doğrulanır (henüz tam oturum yok)",
       "POST /api/auth/mfa/send", "POST /api/auth/mfa/verify")
_allow("Tarayıcı CAPI olay köprüsü — anonim ziyaretçi; sunucu doğrular/kısıtlar, PII hash'lenir",
       "POST /api/capi/event")
_allow("Sipariş sonrası terk edilmiş sepet oturumunu havuzdan düşürür (yalnız session_id ile silme; rate-limit)",
       "POST /api/cart/mark-ordered")
_allow("Personel davetini kabul akışı — tek kullanımlık davet token'ı (load_invite) + rate-limit ile "
       "doğrulanır; henüz hesap/oturum yok",
       "POST /api/auth/invite/inspect", "POST /api/auth/invite/mfa-start", "POST /api/auth/invite/accept")
_allow("İstemci hata günlüğü — anonim, boyut/oran sınırlı, redakte edilir",
       "POST /api/client-log")
_allow("CSP ihlal raporu (tarayıcı report-uri) — anonim; gövde 16 KB tavanı + IP/küresel oran sınırı; "
       "yalnız toplu sayaç yazar, kişisel veri saklanmaz (T19)",
       "POST /api/custom-theme/csp-report")
_allow("Güvenlik uyarısı onayı (SOC-01) — e-postadaki imzalı (HMAC), tek kullanımlık, 72 saatte sona eren "
       "bağlantı; yalnız onay kaydeder, uyarı içeriği göstermez; oran sınırlı",
       "POST /api/security/alert-ack")
_allow("Çerez onayı kaydı (KVKK) — anonim ziyaretçi",
       "POST /api/consent/log")
_allow("Sağlayıcı webhook'u — paylaşımlı sır/imza ile doğrulanır (fail-closed)",
       "POST /api/email-marketing/brevo-webhook", "POST /api/email-marketing/ses-webhook",
       "POST /api/inbound-mail", "POST /api/send-test", "POST /api/influencers/cargo-webhook",
       "POST /api/integrations/temu/webhook", "POST /api/meta-messaging/webhook",
       "POST /api/orders/cargo/mng-webhook", "POST /api/webhooks/trendyol", "POST /api/whatsapp/webhook")
_allow("Meta mention webhook STUB — yalnız loglar, veritabanına yazmaz/okumaz",
       "POST /api/influencers/meta-webhook")
_allow("Abonelikten çıkış (RFC 8058 one-click dahil) — imzalı e-posta token'ı (t) ile doğrulanır",
       "POST /api/email-marketing/unsubscribe")
_allow("Hediye çeki bakiye sorgusu (checkout) — rate-limit; bakiye düşmez",
       "POST /api/gift-cards/check")
_allow("İYS/izin OTP gönderimi — misafir checkout; rate-limit",
       "POST /api/iys/otp/send")
_allow("Bülten aboneliği — anonim; çift onay/İYS kuralları gövdede",
       "POST /api/newsletter/subscribe")
_allow("Misafir sipariş işlemleri (dekont/havale bildirimi, iade talebi) — sipariş no + iletişim bilgisi "
       "eşleşmesi ve rate-limit ile doğrulanır; ödeme/sipariş statüsü 'paid' YAPILMAZ",
       "POST /api/orders/by-number/{order_number}/payment-notification",
       "POST /api/orders/by-number/{order_number}/return-request",
       "POST /api/orders/by-number/{order_number}/return-request/cancel")
_allow("Misafir havale 'ödedim' bildirimi — sipariş varlığı + rate-limit + idempotent; yalnız bildirim "
       "kaydı açar, ödeme/sipariş statüsünü 'paid' YAPMAZ (personel onaylar)",
       "POST /api/payments/havale-notify")
_allow("Ödeme akışı (misafir checkout dahil) — iyzico imzası/token'ı ve sunucu tarafı tutar eşleşmesi "
       "ile doğrulanır (CLAUDE.md değişmez 1); callback/webhook iyzico'dan gelir",
       "POST /api/payment/3ds/callback", "POST /api/payment/3ds/initialize", "POST /api/payment/callback",
       "POST /api/payment/card/pay", "POST /api/payment/initialize", "POST /api/payment/installments",
       "POST /api/payment/webhook")
_allow("Sepet fiyatlama/öneri — salt hesaplama, yazma yok (misafir sepeti)",
       "POST /api/products/cart-pricing", "POST /api/products/cart-suggestions",
       "POST /api/products/checkout-deals")
_allow("Bakım modu 'açılınca haber ver' kaydı — anonim; rate-limit",
       "POST /api/settings/maintenance/notify")
_allow("Sepet paylaşım linki oluşturma — misafir; yalnız sepet içeriği (PII yok)",
       "POST /api/shared-carts")
_allow("Stokta yok 'gelince haber ver' kaydı — misafir; rate-limit",
       "POST /api/stock-notify")
# __ALLOWLIST_END__


# ───────────────────────────────── statik çözümleme ─────────────────────────────────────────

def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _const_str(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):  # f-string → sabit parçalar
        out = ""
        for v in node.values:
            if isinstance(v, ast.Constant):
                out += str(v.value)
            else:
                out += "{}"
        return out
    return None


def _kw(call: ast.Call, name: str):
    for k in call.keywords:
        if k.arg == name:
            return k.value
    return None


def _call_name(node) -> str | None:
    """`foo`, `mod.foo`, `foo(...)` → 'foo'."""
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


class _Module:
    """Bir route modülünün router'ları, guard isimleri ve rotaları."""

    def __init__(self, path: Path):
        self.path = path
        self.tree = _parse(path)
        self.guards: set[str] = set()
        self.routers: dict[str, dict] = {}  # var → {"prefix": str, "guarded": bool}
        self.sub_includes: list[tuple[str, str, str]] = []  # (parent_var, child_local, prefix)
        self.imports: dict[str, tuple[str, str]] = {}  # local → (module, attr)
        self._collect()

    # -- guard isimleri ------------------------------------------------------------------
    def _is_guard_expr(self, node) -> bool:
        """`Depends(x)` içindeki x bir guard mı? (Name, Attribute ya da require_permission(...) çağrısı)"""
        if node is None:
            return False
        if isinstance(node, ast.Call):
            nm = _call_name(node.func)
            # require_permission("x") / yerel fabrika (ör. _perm("x")) guard döndürür
            return nm in self.guards
        nm = _call_name(node)
        return nm in self.guards

    def _depends_target(self, node):
        if isinstance(node, ast.Call) and _call_name(node.func) in {"Depends", "Security"}:
            if node.args:
                return node.args[0]
            return _kw(node, "dependency")
        return None

    def _func_has_guard_dep(self, fn) -> bool:
        a = fn.args
        defaults = list(a.defaults) + [d for d in a.kw_defaults if d is not None]
        for d in defaults:
            if self._is_guard_expr(self._depends_target(d)):
                return True
            if isinstance(d, ast.Name) and d.id in self.guards:  # x = Depends(guard) alias
                return True
        # Annotated[dict, Depends(guard)]
        for arg in list(a.args) + list(a.kwonlyargs):
            ann = arg.annotation
            if isinstance(ann, ast.Subscript):
                for sub in ast.walk(ann):
                    if self._is_guard_expr(self._depends_target(sub)):
                        return True
        return False

    def _body_calls_guard(self, fn) -> bool:
        for n in ast.walk(fn):
            if isinstance(n, ast.Call) and _call_name(n.func) in self.guards:
                return True
        return False

    def _deps_list_guarded(self, node) -> bool:
        if isinstance(node, (ast.List, ast.Tuple)):
            for e in node.elts:
                if self._is_guard_expr(self._depends_target(e)):
                    return True
                if isinstance(e, ast.Name) and e.id in self.guards:
                    return True
        return False

    def _collect(self):
        body = self.tree.body
        for n in ast.walk(self.tree):
            if isinstance(n, ast.ImportFrom):
                for a in n.names:
                    local = a.asname or a.name
                    self.imports[local] = ("." * (n.level or 0) + (n.module or ""), a.name)
                    if a.name in GUARD_NAMES:
                        self.guards.add(local)
        self.guards |= GUARD_NAMES
        # yerel guard yardımcıları ve takma adlar — sabit noktaya kadar
        changed = True
        while changed:
            changed = False
            for n in body:
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name not in self.guards:
                    if n.decorator_list and any(self._route_info(d) for d in n.decorator_list):
                        continue  # rota fonksiyonu kendisi guard değildir
                    if self._func_has_guard_dep(n) or self._returns_guard(n):
                        self.guards.add(n.name)
                        changed = True
                elif isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                    tgt = n.targets[0].id
                    if tgt in self.guards:
                        continue
                    v = n.value
                    if self._is_guard_expr(v) or self._is_guard_expr(self._depends_target(v)):
                        self.guards.add(tgt)
                        changed = True
        # router'lar
        for n in body:
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) \
                    and _call_name(n.value.func) == "APIRouter":
                prefix = _const_str(_kw(n.value, "prefix")) or ""
                guarded = self._deps_list_guarded(_kw(n.value, "dependencies"))
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        self.routers[t.id] = {"prefix": prefix, "guarded": guarded}
        for n in ast.walk(self.tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr == "include_router" and isinstance(n.func.value, ast.Name) and n.args:
                child = n.args[0]
                if isinstance(child, ast.Name):
                    self.sub_includes.append((n.func.value.id, child.id, _const_str(_kw(n, "prefix")) or ""))

    def _returns_guard(self, fn) -> bool:
        """Guard'ı gövdesinde çağıran yardımcı (ör. `_require_admin_or_token`) ya da guard
        döndüren fabrika (ör. `def _perm(k): return require_permission(k)`)."""
        if fn.name.startswith("_test"):
            return False
        return self._body_calls_guard(fn)

    def _route_info(self, deco):
        if not (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute)):
            return None
        meth = deco.func.attr
        owner = deco.func.value
        if not isinstance(owner, ast.Name):
            return None
        if meth in HTTP_METHODS:
            methods = [meth.upper()]
        elif meth == "api_route":
            m = _kw(deco, "methods")
            methods = [(_const_str(e) or "").upper() for e in (m.elts if isinstance(m, (ast.List, ast.Tuple)) else [])] or ["GET"]
        else:
            return None
        path = _const_str(deco.args[0]) if deco.args else _const_str(_kw(deco, "path"))
        if path is None:
            return None
        return owner.id, methods, path, deco

    def routes(self):
        for n in ast.walk(self.tree):
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for d in n.decorator_list:
                info = self._route_info(d)
                if not info:
                    continue
                owner, methods, path, deco = info
                r = self.routers.get(owner, {"prefix": "", "guarded": False})
                guarded = (r["guarded"] or self._deps_list_guarded(_kw(deco, "dependencies"))
                           or self._func_has_guard_dep(n) or self._body_calls_guard(n))
                yield owner, methods, path, n.name, guarded, n.lineno


def _resolve_module(mod: str, base_pkg: str = "") -> Path | None:
    """'routes.orders' / '.orders' (routes paketi içinden) → dosya yolu."""
    if mod.startswith("."):
        mod = base_pkg + mod.lstrip(".")
        mod = mod if not base_pkg or mod.startswith(base_pkg) else mod
    p = BACKEND / (mod.replace(".", "/") + ".py")
    return p if p.exists() else None


def collect_routes():
    """server.py'nin bağladığı TÜM router'ların rotaları: [(method, full_path, file, func, guarded, line)]."""
    srv = _Module(SERVER)
    init = _parse(ROUTES_DIR / "__init__.py")
    pkg_exports: dict[str, tuple[str, str]] = {}
    for n in init.body:
        if isinstance(n, ast.ImportFrom):
            for a in n.names:
                pkg_exports[a.asname or a.name] = ("routes." + (n.module or ""), a.name)

    def resolve(local: str):
        mod, attr = srv.imports.get(local, (None, None))
        if mod is None:
            return None
        if mod == "routes":
            mod, attr = pkg_exports.get(attr, (None, None))
            if mod is None:
                return None
        p = _resolve_module(mod)
        return (p, attr) if p else None

    # server.py'deki include_router çağrıları (for-döngüsündeki tuple dahil)
    includes: list[tuple[str, str, str]] = []  # (parent_var, child_local, prefix)
    for n in ast.walk(srv.tree):
        if isinstance(n, ast.For) and isinstance(n.target, ast.Name) and isinstance(n.iter, (ast.Tuple, ast.List)):
            calls = [c for c in ast.walk(n) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                     and c.func.attr == "include_router" and c.args and isinstance(c.args[0], ast.Name)
                     and c.args[0].id == n.target.id]
            for c in calls:
                for e in n.iter.elts:
                    if isinstance(e, ast.Name):
                        includes.append((c.func.value.id if isinstance(c.func.value, ast.Name) else "",
                                         e.id, _const_str(_kw(c, "prefix")) or ""))
    includes += srv.sub_includes
    srv_prefix = {"app": "", "api_router": srv.routers.get("api_router", {}).get("prefix", "/api")}

    out = []
    seen = set()
    modules: dict[Path, _Module] = {}

    def add_router(path: Path, attr: str, prefix: str):
        mod = modules.get(path) or _Module(path)
        modules[path] = mod
        key = (path, attr, prefix)
        if key in seen:
            return
        seen.add(key)
        rp = mod.routers.get(attr, {}).get("prefix", "")
        for owner, methods, rpath, fname, guarded, line in mod.routes():
            if owner != attr:
                continue
            for m in methods:
                out.append((m, prefix + rp + rpath, str(path.relative_to(BACKEND)), fname, guarded, line))
        # modül içi alt-include (ör. integrations.py → trendyol/hb/common)
        for parent, child, sub_prefix in mod.sub_includes:
            if parent != attr:
                continue
            cm, ca = mod.imports.get(child, (None, None))
            if cm is None:
                continue
            cp = _resolve_module(cm.lstrip(".") if not cm.startswith(".") else "routes." + cm.lstrip("."))
            if cp:
                add_router(cp, ca, prefix + rp + sub_prefix)

    for parent, child, prefix in includes:
        if parent not in srv_prefix:
            continue
        base = srv_prefix[parent]
        if child == "api_router":
            continue
        r = resolve(child)
        if r:
            add_router(r[0], r[1], base + prefix)
    # server.py'nin kendi rotaları
    for owner, methods, rpath, fname, guarded, line in srv.routes():
        base = srv_prefix.get(owner)
        if base is None:
            continue
        for m in methods:
            out.append((m, base + rpath, "server.py", fname, guarded, line))
    return out


def find_violations():
    rows = collect_routes()
    bad = []
    for method, path, file, fname, guarded, line in rows:
        needs = method in WRITE_METHODS or path.startswith("/api/admin") or path.startswith("/admin") \
            or "/admin/" in path
        if not needs or guarded:
            continue
        key = f"{method} {path}"
        if key in ALLOWLIST:
            continue
        bad.append(f"{key}  ({file}:{line} {fname})")
    return sorted(set(bad)), rows


def test_routes_are_mounted_and_parsed():
    _, rows = find_violations()
    # Hızlı akıl sağlığı: çözümleyici gerçekten yüzlerce rota bulmalı (yanlışlıkla 0 → yeşil olmasın).
    assert len(rows) > 300, f"yalnız {len(rows)} rota bulundu — statik çözümleyici bozulmuş olabilir"


def test_every_admin_and_write_route_has_auth_dependency():
    bad, _ = find_violations()
    assert not bad, ("Yetki bağımlılığı OLMAYAN admin/yazma rotaları (yetki ekleyin ya da gerekçesiyle "
                     "ALLOWLIST'e yazın):\n  " + "\n  ".join(bad))


def test_allowlist_entries_have_reasons():
    for k, reason in ALLOWLIST.items():
        assert reason and len(reason.strip()) >= 10, f"ALLOWLIST girdisi gerekçesiz: {k}"
        meth = k.split(" ", 1)[0]
        assert meth in WRITE_METHODS | {"GET", "HEAD", "OPTIONS"}, k


def stale_allowlist_entries() -> list[str]:
    """Artık var olmayan ya da yetki kazanmış allowlist girdileri (bilgi amaçlı; testi kırmaz —
    eşzamanlı yetki eklemeleri CI'ı kırmasın)."""
    _, rows = find_violations()
    unguarded = {f"{m} {p}" for m, p, _f, _fn, g, _l in rows if not g}
    return sorted(k for k in ALLOWLIST if k not in unguarded)


if __name__ == "__main__":  # pytest olmadan: python backend/tests/test_route_guards.py
    _bad, _rows = find_violations()
    print(f"taranan rota: {len(_rows)} · allowlist: {len(ALLOWLIST)}")
    _stale = stale_allowlist_entries()
    if _stale:
        print("BİLGİ — eskimiş allowlist girdileri (kaldırılabilir):\n  " + "\n  ".join(_stale))
    if "--dump" in sys.argv:
        for _b in _bad:
            print(_b)
    if _bad:
        print("HATA — yetkisiz admin/yazma rotaları:\n  " + "\n  ".join(_bad))
        sys.exit(1)
    if len(_rows) <= 300:
        print("HATA — çok az rota bulundu; çözümleyici bozuk")
        sys.exit(1)
    print("OK — tüm admin/yazma rotalarında yetki bağımlılığı var (ya da gerekçeli allowlist'te).")
