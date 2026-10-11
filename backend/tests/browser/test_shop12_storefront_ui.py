"""SHOP12 — tarayıcı tabanlı vitrin arayüz testleri (Playwright, gerçek Chromium).

Kabul ölçütü (e-ticaret-guvenlik-blueprint.md:597): dar ekran ve klavye kullanımında kullanıcı doğru
ürün/tutar/işlem bağlamını görebilir; tekrar düğmesine basmak çift sipariş yaratmaz. Network
kesintisinde ekranda kesin başarı yazılmaz; toparlanma yolu (aynı idempotency anahtarıyla yeniden
deneme) çalışır.

ÇALIŞTIRMA (CI'da kendiliğinden ATLANIR — tarayıcı ve canlı/önizleme vitrin adresi gerekir):

    pip install playwright==1.56.0 && python -m playwright install chromium
    STOREFRONT_BASE_URL=https://<vitrin> python -m pytest -q backend/tests/browser

İsteğe bağlı: STOREFRONT_BROWSER_PROXY (ör. http://127.0.0.1:3128), STOREFRONT_BROWSER_ARGS (Chromium
ek argümanları, boşlukla ayrılmış), STOREFRONT_CHROMIUM_EXECUTABLE.

GÜVENLİK / CANLI VERİ: Testler canlı vitrine karşı SALT-OKUNUR çalışır.
  • Sipariş (POST */api/orders) ve ödeme başlatma (POST */api/payment/...) istekleri route ile
    tarayıcıda ABORT edilir ya da sahte yanıtla karşılanır — sunucuya HİÇ ulaşmaz; testler bunu
    ayrıca doğrular (yakalayıcı sayaçları).
  • Diğer tüm yazma istekleri (sepet izleme, CAPI, atıf, çerez onayı, istemci günlüğü, …) de
    abort edilir; yalnız saf hesaplama uçlarına (sepet fiyatlama, kupon/kampanya değerlendirme)
    izin verilir; taksit sorgusu sahte yanıtla karşılanır (kart BIN'i sunucuya gitmez).
  • Üçüncü taraf (analitik/piksel) istekleri abort edilir — reklam hesaplarına bot verisi gitmez.
  • Formlar yalnız example.test sahte verisiyle doldurulur; sepet tarayıcının localStorage'ındadır.
"""
from __future__ import annotations

import json
import os
import re
from urllib.parse import urlsplit

import pytest

BASE = (os.environ.get("STOREFRONT_BASE_URL") or "").strip().rstrip("/")
if not BASE:
    pytest.skip("STOREFRONT_BASE_URL tanımlı değil (tarayıcı testleri yalnız açıkça istenince çalışır)",
                allow_module_level=True)
sync_api = pytest.importorskip("playwright.sync_api", reason="playwright kurulu değil")

NARROW = {"width": 360, "height": 740}
DESKTOP = {"width": 1366, "height": 900}
SUCCESS_TEXTS = ("Siparişiniz alındı", "Siparişiniz Alındı", "Sipariş numaranız", "Sipariş Numaranız")
# Sunucuya ulaşmasına izin verilen yazma-olmayan hesaplama uçları (POST ama veri yazmaz).
COMPUTE_ONLY = ("/api/products/cart-pricing", "/api/coupons/evaluate", "/api/promotions/evaluate",
                "/api/campaigns/evaluate", "/api/cart/evaluate")
FAKE_EMAIL = "shop12-ui-test@example.test"
FAKE_CARD = {"holder": "SHOP TEST", "number": "5528790000000008", "expiry": "1230", "cvc": "123"}


def _site_domain() -> str:
    h = (urlsplit(BASE).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def _allowed_host(host: str) -> bool:
    """Vitrinin kendi alan adı (+ api./cdn. alt alanları) ve yazı tipleri; geri kalan 3. taraf abort."""
    d = _site_domain()
    return host == d or host.endswith("." + d) or host in ("fonts.googleapis.com", "fonts.gstatic.com")


class ApiGuard:
    """Bağlam düzeyinde tek yakalayıcı: yazma isteklerini sunucuya göndermez, kaydeder."""

    def __init__(self):
        self.overrides = []          # [(regex, handler(route, request) -> None)]
        self.block_get = []          # [regex] — test kapsamı dışı GET'ler (ör. pazarlama açılır penceresi)
        self.log = []                # [(method, path, fate)]

    def handle(self, route, request):
        url, method = request.url, request.method
        parts = urlsplit(url)
        host, path = (parts.hostname or "").lower(), parts.path
        if not _allowed_host(host):
            return route.abort()
        if method == "GET" and any(re.search(rx, path) for rx in self.block_get):
            return route.fulfill(status=200, content_type="application/json", body='{"items": []}')
        if "/api/" in path and method not in ("GET", "HEAD", "OPTIONS"):
            for rx, fn in self.overrides:
                if re.search(rx, path):
                    self.log.append((method, path, "override"))
                    return fn(route, request)
            if path.endswith("/api/payment/installments"):
                self.log.append((method, path, "stub"))
                return route.fulfill(status=200, content_type="application/json",
                                     body=json.dumps({"options": []}))
            if any(path.endswith(p) for p in COMPUTE_ONLY):
                self.log.append((method, path, "continue"))
                return route.continue_()
            self.log.append((method, path, "abort"))
            return route.abort()
        return route.continue_()

    def fates(self, rx):
        return [f for (_m, p, f) in self.log if re.search(rx, p)]


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        kw = {"headless": True}
        if os.environ.get("STOREFRONT_BROWSER_PROXY"):
            kw["proxy"] = {"server": os.environ["STOREFRONT_BROWSER_PROXY"]}
        if os.environ.get("STOREFRONT_BROWSER_ARGS"):
            kw["args"] = os.environ["STOREFRONT_BROWSER_ARGS"].split()
        if os.environ.get("STOREFRONT_CHROMIUM_EXECUTABLE"):
            kw["executable_path"] = os.environ["STOREFRONT_CHROMIUM_EXECUTABLE"]
        b = p.chromium.launch(**kw)
        yield b
        b.close()


def _context(browser, viewport, mobile=False):
    opts = {"viewport": viewport, "locale": "tr-TR", "timezone_id": "Europe/Istanbul"}
    if mobile:
        opts.update(is_mobile=True, has_touch=True, device_scale_factor=2)
    ctx = browser.new_context(**opts)
    # Çerez bandı kararı önceden verilmiş say (POST /consent/log tetiklenmesin; bant düğmeleri örtmesin).
    ctx.add_init_script("try{localStorage.setItem('meluni_cookie_consent',JSON.stringify("
                        "{necessary:true,analytics:false,marketing:false,v:1}))}catch(e){}")
    guard = ApiGuard()
    ctx.route("**/*", guard.handle)
    return ctx, guard


def _page(ctx):
    page = ctx.new_page()
    # Pazarlama açılır penceresi (bülten kartı / orta modal) gecikmeli açılıp tıklamaları örtebilir:
    # gerçek kullanıcı gibi "Kapat" ile kapatılır (yalnız localStorage'a yazar).
    popup = page.locator('[data-testid="site-popup-newsletter"], [data-testid="site-popup"]')
    page.add_locator_handler(popup, lambda loc: loc.locator('button[aria-label="Kapat"]').first.click())
    return page


@pytest.fixture
def narrow(browser):
    ctx, guard = _context(browser, NARROW, mobile=True)
    yield _page(ctx), guard
    ctx.close()


@pytest.fixture
def desktop(browser):
    ctx, guard = _context(browser, DESKTOP)
    yield _page(ctx), guard
    ctx.close()


# ───────────────────────────────────── yardımcılar ─────────────────────────────────────
def _goto(page, path, settle_ms=1500):
    page.goto(BASE + path, wait_until="domcontentloaded", timeout=45000)
    try:
        page.wait_for_load_state("networkidle", timeout=12000)
    except Exception:  # noqa: BLE001 — uzun-yoklama/analitik: networkidle şart değil
        pass
    page.wait_for_timeout(settle_ms)


_BUYABLE_URL = {}


def _find_buyable_product(page) -> str:
    """Ana sayfa → ilk kategoriler → stokta bedeni olan ilk ürün (URL önbelleklenir)."""
    if _BUYABLE_URL.get("url"):
        return _BUYABLE_URL["url"]
    _goto(page, "/")
    # Menü bağlantıları kısa slug (/giyim) ya da /kategori/<slug> olabilir; ürün kartı içeren ilk sayfalar.
    cats = page.eval_on_selector_all(
        'a[href^="/"]', """els => [...new Set(els.map(e => e.getAttribute('href')))].filter(h => h.length > 2 &&
            !/^\\/(giris|hesabim|sepet|odeme|admin|sayfa|sss|sikca|siparis|iade|gizlilik|arama|full-look|tema)/.test(h))""")
    for cat in cats[:6]:
        _goto(page, cat)
        prods = page.eval_on_selector_all('[data-testid^="product-card-"] a[href^="/"]',
                                          "els => [...new Set(els.map(e => e.getAttribute('href')))]")
        for prod in prods[:6]:
            _goto(page, prod, 800)
            if page.locator('[data-testid="add-to-cart-btn"]').count() == 0:
                continue
            sizes = page.locator('[data-testid^="size-btn-"]:not(.line-through)')
            if sizes.count() or page.locator('[data-testid="add-to-cart-btn"]:not([disabled])').count():
                _BUYABLE_URL["url"] = prod
                return prod
    pytest.skip("Stokta ürün bulunamadı")


def _cart_count(page) -> int:
    return page.evaluate("() => { try { return (JSON.parse(localStorage.getItem('cart')||'[]')||[]).length } catch(e) { return -1 } }")


def _add_to_cart_with_pointer(page):
    prod = _find_buyable_product(page)
    _goto(page, prod)
    sizes = page.locator('[data-testid^="size-btn-"]:not(.line-through)')
    if sizes.count():
        sizes.first.click()
    page.locator('[data-testid="add-to-cart-btn"]').click()
    page.wait_for_timeout(1200)
    assert _cart_count(page) >= 1, "ürün sepete eklenemedi"


def _no_success_shown(page):
    assert "/order-success" not in page.url and "/siparis-tamamlandi" not in page.url, page.url
    body = page.inner_text("body")
    for t in SUCCESS_TEXTS:
        assert t not in body, f"ağ hatasında kesin başarı metni göründü: {t!r}"


def _toasts(page) -> str:
    return " | ".join(page.locator("[data-sonner-toast]").all_inner_texts())


def _fill_checkout_with_pointer(page, method="bank_transfer"):
    """Misafir ödeme formunu SAHTE example.test verisiyle doldurur (gönderMEZ)."""
    if page.locator('[data-testid="contact-email"]').count():
        page.fill('[data-testid="contact-email"]', FAKE_EMAIL)
    page.click('[data-testid="edit-shipping-addr-btn"]')
    modal = page.locator('[data-testid="address-modal"]')
    modal.wait_for(state="visible")
    modal.locator('input[autocomplete="given-name"]').fill("Test")
    modal.locator('input[autocomplete="family-name"]').fill("Kullanici")
    modal.locator('input[autocomplete="tel"]').fill("05550000000")
    modal.locator('textarea[autocomplete="street-address"]').fill("Ornek Mah. Test Sok. No:1")
    for tid, val in (("address-modal-city-select", "İstanbul"), ("address-modal-district-select", "Kadıköy")):
        box = modal.locator(f'[data-testid="{tid}"]')
        box.wait_for(state="visible")
        page.wait_for_function("(t) => !document.querySelector(`[data-testid=\"${t}\"]`).disabled", arg=tid,
                               timeout=15000)
        box.click()
        box.fill(val)
        page.locator('[role="option"]', has_text=val).first.click()
    modal.locator('[data-testid="save-address-btn"]').click()
    modal.wait_for(state="detached")
    _choose_payment(page, method)
    cb = page.locator('[data-testid="accept-terms-checkbox"]')
    if not cb.is_checked():
        cb.check()


def _choose_payment(page, method):
    radio = page.locator(f'input[name="payment"][value="{method}"]')
    if radio.count() == 0:                        # yöntem kapalıysa açık olan ilkini kullan
        radio = page.locator('input[name="payment"]').first
        method = radio.get_attribute("value")
    page.locator(f'label:has(input[name="payment"][value="{method}"])').click()
    if method == "credit_card":
        card = page.locator('[data-testid="payment-block"]')
        card.locator('input[autocomplete="cc-name"]').fill(FAKE_CARD["holder"])
        card.locator('input[autocomplete="cc-number"]').fill(FAKE_CARD["number"])
        card.locator('input[autocomplete="cc-exp"]').fill(FAKE_CARD["expiry"])
        card.locator('input[autocomplete="cc-csc"]').fill(FAKE_CARD["cvc"])
    return method


def _overflow(page) -> dict:
    return page.evaluate("""() => {
        const vw = window.innerWidth;
        const off = [];
        for (const el of document.querySelectorAll('body *')) {
            const cs = getComputedStyle(el);
            if (cs.display === 'none' || cs.visibility === 'hidden' || cs.position === 'fixed') continue;
            const r = el.getBoundingClientRect();
            if (r.width > 0 && r.height > 0 && r.right > vw + 1) {
                // yatay kaydırmalı (overflow-x) kap içindeki öğeler sayfayı taşırmaz
                let p = el.parentElement, clipped = false;
                while (p && p !== document.body) {
                    const ps = getComputedStyle(p);
                    if (/(auto|scroll|hidden|clip)/.test(ps.overflowX)) { clipped = true; break; }
                    p = p.parentElement;
                }
                if (!clipped) off.push((el.getAttribute('data-testid') || el.tagName) + ':' + Math.round(r.right));
            }
        }
        return {vw, sw: document.documentElement.scrollWidth, bw: document.body.scrollWidth, off: off.slice(0, 10)};
    }""")


def _assert_no_hscroll(page, where):
    o = _overflow(page)
    assert o["sw"] <= o["vw"] + 1 and o["bw"] <= o["vw"] + 1, f"{where}: yatay taşma {o}"
    assert not o["off"], f"{where}: görünür alandan taşan öğeler {o['off']}"


def _in_viewport_x(page, selector):
    box = page.locator(selector).first.bounding_box()
    vw = page.viewport_size["width"]
    return box is not None and box["x"] >= -1 and box["x"] + box["width"] <= vw + 1


# ───────────────────────────── 1) dar ekran (360×740) ─────────────────────────────
def test_narrow_checkout_renders_without_horizontal_overflow(narrow):
    page, guard = narrow
    _add_to_cart_with_pointer(page)
    _assert_no_hscroll(page, "ürün sayfası")
    item = json.loads(page.evaluate("localStorage.getItem('cart')"))[0]
    _goto(page, "/sepet")
    _assert_no_hscroll(page, "sepet")
    _goto(page, "/odeme", 2500)
    assert page.locator('[data-testid="checkout-page"]').count() == 1
    _assert_no_hscroll(page, "ödeme")
    # birincil düğmeler görünür ve ekran genişliği içinde
    for sel in ('[data-testid="edit-shipping-addr-btn"]', '[data-testid="place-order-btn"]'):
        page.locator(sel).first.scroll_into_view_if_needed()
        assert page.locator(sel).first.is_visible(), sel
        assert _in_viewport_x(page, sel), f"{sel} ekran dışına taşıyor"
    # doğru ürün/tutar bağlamı: özet ürün adını, düğme ödenecek tutarı gösterir
    page.click('[data-testid="cart-summary-block"] button')
    assert item["name"][:20] in page.inner_text('[data-testid="cart-summary-block"]')
    btn = page.inner_text('[data-testid="place-order-btn"]')
    m = re.search(r"([\d.,]+)\s*TL", btn)
    assert m and float(m.group(1).replace(",", "")) > 0, btn
    # adres penceresi de dar ekranda taşmadan açılır
    page.click('[data-testid="edit-shipping-addr-btn"]')
    page.locator('[data-testid="address-modal"]').wait_for(state="visible")
    _assert_no_hscroll(page, "adres penceresi")
    assert _in_viewport_x(page, '[data-testid="save-address-btn"]')
    assert not guard.fates(r"/api/orders$|/api/payment/")      # hiçbir sipariş/ödeme isteği yok


# ───────────────────────────── 2) yalnız klavye ─────────────────────────────
_FOCUS_INFO = """() => {
    const el = document.activeElement;
    if (!el || el === document.body) return null;
    const r = el.getBoundingClientRect();
    const tiny = r.width <= 2 || r.height <= 2;            // sr-only (ör. ödeme yöntemi radyosu)
    const t = tiny ? (el.closest('label') || el) : el;
    const cs = getComputedStyle(t);
    return {tid: el.getAttribute('data-testid') || '', tag: el.tagName, type: el.type || '',
            name: el.name || '', value: el.value || '', ac: el.getAttribute('autocomplete') || '',
            cls: el.className && el.className.baseVal === undefined ? el.className : '',
            fv: el.matches(':focus-visible'),
            style: [cs.outlineStyle, cs.outlineWidth, cs.outlineColor, cs.boxShadow, cs.borderTopColor,
                    cs.borderBottomColor, cs.backgroundColor, cs.color].join('|')};
}"""


_STYLE_OF = """(el) => {
    const r = el.getBoundingClientRect();
    const t = (r.width <= 2 || r.height <= 2) ? (el.closest('label') || el) : el;
    const cs = getComputedStyle(t);
    return [cs.outlineStyle, cs.outlineWidth, cs.outlineColor, cs.boxShadow, cs.borderTopColor,
            cs.borderBottomColor, cs.backgroundColor, cs.color].join('|');
}"""


class Keyboard:
    """Yalnız Tab/Shift+Tab ile gezer; odak görünürlüğünü odaklı ↔ odaksız stil farkıyla ölçer."""

    SETTLE_MS = 350        # transition-all (150 ms) bitmeden okunan stil yanıltır

    def __init__(self, page):
        self.page = page

    def tab_until(self, pred, limit=250, shift=False):
        for _ in range(limit):
            self.page.keyboard.press("Shift+Tab" if shift else "Tab")
            info = self.page.evaluate(_FOCUS_INFO)
            if info and pred(info):
                return info
        raise AssertionError("klavye ile hedef öğeye ulaşılamadı")

    def visible_focus_now(self):
        """Şu an odaktaki öğe :focus-visible ve görünümü odak dışı halinden farklı mı (outline, halka,
        kenarlık, arka plan, renk)? sr-only öğelerde (ödeme radyosu) ölçüm saran <label> üzerindedir."""
        el = self.page.evaluate_handle("document.activeElement")
        self.page.wait_for_timeout(self.SETTLE_MS)
        fv = self.page.evaluate("(el) => el.matches(':focus-visible')", el)
        focused = self.page.evaluate(_STYLE_OF, el)
        self.page.evaluate("(el) => el.blur()", el)
        self.page.wait_for_timeout(self.SETTLE_MS)
        blurred = self.page.evaluate(_STYLE_OF, el)
        self.page.evaluate("(el) => el.focus()", el)     # son etkileşim klavye → yine :focus-visible
        self.page.wait_for_timeout(self.SETTLE_MS)
        return bool(fv and focused != blurred)


def test_keyboard_only_add_to_cart_and_checkout_form(desktop):
    page, guard = desktop
    # Gecikmeli pazarlama açılır penceresi klavye akışının ortasında açılıp odağı/listeyi kapatmasın
    # (bu test onu kapsamıyor; işaretçi testleri onu kullanıcı gibi kapatır).
    guard.block_get.append(r"/api/storefront/popups$")
    prod = _find_buyable_product(page)
    _goto(page, prod)
    kb = Keyboard(page)
    page.evaluate("() => { document.activeElement && document.activeElement.blur(); window.scrollTo(0,0); }")
    # beden seç (Enter) → sepete ekle (Enter)
    if page.locator('[data-testid^="size-btn-"]').count():
        kb.tab_until(lambda i: i["tid"].startswith("size-btn-") and "line-through" not in i["cls"])
        assert kb.visible_focus_now(), "beden düğmesinde görünür odak yok"
        page.keyboard.press("Enter")
    kb.tab_until(lambda i: i["tid"] == "add-to-cart-btn")
    assert kb.visible_focus_now(), "sepete ekle düğmesinde görünür odak yok"
    page.keyboard.press("Enter")
    page.wait_for_timeout(1200)
    assert _cart_count(page) >= 1, "klavyeyle sepete eklenemedi"

    _goto(page, "/odeme", 2500)
    page.evaluate("() => { document.activeElement && document.activeElement.blur(); window.scrollTo(0,0); }")
    if page.locator('[data-testid="contact-email"]').count():
        kb.tab_until(lambda i: i["tid"] == "contact-email")
        assert kb.visible_focus_now(), "e-posta alanında görünür odak yok"
        page.keyboard.type(FAKE_EMAIL)
    kb.tab_until(lambda i: i["tid"] == "edit-shipping-addr-btn")
    assert kb.visible_focus_now(), "adres düğmesinde görünür odak yok"
    page.keyboard.press("Enter")
    page.locator('[data-testid="address-modal"]').wait_for(state="visible")
    # adres penceresi alanları — yalnız klavye
    for ac, text in (("given-name", "Test"), ("family-name", "Kullanici"), ("tel", "05550000000"),
                     ("street-address", "Ornek Mah. Test Sok. No:1")):
        kb.tab_until(lambda i, ac=ac: i["ac"] == ac, limit=120)
        assert kb.visible_focus_now(), f"{ac} alanında görünür odak yok"
        page.keyboard.type(text)
    for tid, val in (("address-modal-city-select", "İstanbul"), ("address-modal-district-select", "Kadıköy")):
        page.wait_for_function("(t) => !document.querySelector(`[data-testid=\"${t}\"]`).disabled", arg=tid,
                               timeout=15000)
        kb.tab_until(lambda i, tid=tid: i["tid"] == tid, limit=20)
        page.keyboard.type(val)
        page.locator('[role="option"]', has_text=val).first.wait_for(state="visible")
        page.keyboard.press("Enter")
    kb.tab_until(lambda i: i["tid"] == "save-address-btn", limit=20)
    page.keyboard.press("Enter")
    page.locator('[data-testid="address-modal"]').wait_for(state="detached")
    assert "Test" in page.inner_text('[data-testid="address-block"]')
    # ödeme yöntemi radyosu (sr-only) — odak etiket üzerinde görünür olmalı; Space ile seçilir
    info = kb.tab_until(lambda i: i["name"] == "payment")
    assert kb.visible_focus_now(), f"ödeme yöntemi seçeneğinde görünür odak yok ({info['value']})"
    page.keyboard.press("Space")
    method = page.evaluate("() => document.querySelector('input[name=payment]:checked').value")
    if method == "credit_card":
        for ac, key in (("cc-name", "holder"), ("cc-number", "number"), ("cc-exp", "expiry"), ("cc-csc", "cvc")):
            kb.tab_until(lambda i, ac=ac: i["ac"] == ac, limit=60)
            page.keyboard.type(FAKE_CARD[key])
    kb.tab_until(lambda i: i["tid"] == "accept-terms-checkbox")
    assert kb.visible_focus_now(), "sözleşme onay kutusunda görünür odak yok"
    page.keyboard.press("Space")
    assert page.locator('[data-testid="accept-terms-checkbox"]').is_checked()
    btn = kb.tab_until(lambda i: i["tid"] == "place-order-btn", shift=True, limit=10)
    assert btn and kb.visible_focus_now(), "ödeme düğmesinde görünür odak yok"
    # klavyeyle gönder → sipariş isteği tarayıcıda kesilir (sunucuya ULAŞMAZ), başarı yazılmaz
    hits = []

    def _cut(route, request):
        hits.append(request.post_data_json)
        route.abort("internetdisconnected")
    guard.overrides.append((r"/api/orders$", _cut))
    page.keyboard.press("Enter")
    page.wait_for_timeout(2500)
    assert len(hits) == 1
    _no_success_shown(page)


# ───────────────────────────── 3) ağ kesintisi ─────────────────────────────
def test_network_cut_on_order_submit_never_shows_success(desktop):
    page, guard = desktop
    _add_to_cart_with_pointer(page)
    _goto(page, "/odeme", 2500)
    method = "bank_transfer"
    _fill_checkout_with_pointer(page, method)
    aborted = []

    def _cut(route, request):
        aborted.append(request.post_data_json or {})
        route.abort("internetdisconnected")       # istek sunucuya HİÇ gitmez
    guard.overrides.append((r"/api/orders$", _cut))
    page.click('[data-testid="place-order-btn"]')
    page.wait_for_timeout(3000)
    assert len(aborted) == 1, "sipariş isteği yakalanıp kesilmedi"
    _no_success_shown(page)
    assert urlsplit(page.url).path == "/odeme"
    assert "Sipariş oluşturulamadı" in _toasts(page) or "oluşturulamadı" in page.inner_text("body")
    # yeniden deneme mümkün (düğme tekrar etkin) ve sepet korunuyor
    btn = page.locator('[data-testid="place-order-btn"]')
    assert btn.is_enabled() and "Ödeme Yap" in btn.inner_text()
    assert _cart_count(page) >= 1
    # toparlanma yolu: ikinci deneme AYNI idempotency anahtarını taşır → sunucu ilk deneme ulaşmış olsa
    # bile tek sipariş döndürür (sunucu tarafı: test_idempotency_same_key_same_payload_returns_same_order)
    page.click('[data-testid="place-order-btn"]')
    page.wait_for_timeout(2500)
    assert len(aborted) == 2
    k1, k2 = aborted[0].get("idempotency_key"), aborted[1].get("idempotency_key")
    assert k1 and k1 == k2, (k1, k2)
    _no_success_shown(page)
    # hiçbir sipariş/ödeme isteği sunucuya iletilmedi
    assert "continue" not in guard.fates(r"/api/orders|/api/payment/")


def test_payment_init_failure_never_shows_success(desktop):
    page, guard = desktop
    _add_to_cart_with_pointer(page)
    _goto(page, "/odeme", 2500)
    if page.locator('input[name="payment"][value="credit_card"]').count() == 0:
        pytest.skip("kart ödemesi bu vitrinde kapalı")
    _fill_checkout_with_pointer(page, "credit_card")
    orders, inits = [], []

    def _fake_order(route, request):
        # Sipariş SUNUCUYA GİTMEZ: tarayıcıda sahte "oluşturuldu (ödeme bekleniyor)" yanıtı.
        orders.append(request.post_data_json or {})
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "success": True, "order_id": "shop12-ui-fake", "order_number": "SHOP12-UI-FAKE",
            "payment_status": "pending", "status": "awaiting_payment", "total": 1}))

    def _cut_init(route, request):
        inits.append(request.url)
        route.abort("internetdisconnected")
    guard.overrides.append((r"/api/orders$", _fake_order))
    guard.overrides.append((r"/api/payment/3ds/", _cut_init))
    page.click('[data-testid="place-order-btn"]')
    page.wait_for_timeout(3500)
    assert len(orders) == 1 and len(inits) == 1, (orders, inits)
    assert "3ds/initialize" in inits[0]
    _no_success_shown(page)
    assert urlsplit(page.url).path == "/odeme"            # 3DS sayfası açılmadı, başarıya gidilmedi
    assert "SHOP12-UI-FAKE" not in page.inner_text("body")
    toasts = _toasts(page)
    assert "oluşturulamadı" in toasts or "başlatılamadı" in toasts, toasts
    assert page.locator('[data-testid="place-order-btn"]').is_enabled()
    assert _cart_count(page) >= 1                           # sepet silinmedi → yeniden deneme mümkün
    assert "continue" not in guard.fates(r"/api/orders|/api/payment/")
