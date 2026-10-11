"""
Vitrin (storefront) bütünlük izleyicisi + karantina — T19 / ADM-08.

NEDEN: Panelden yayınlanan kod (özel tema JS/CSS, pikseller) zaten izleniyor; ama Cloudflare
Pages'in müşteriye GERÇEKTEN sunduğu index.html / CSP başlığı / derlenmiş JS paketlerini dışarıdan
izleyen bir kontrol yoktu. Bu modül 15 dakikada bir (scheduler `storefront_integrity_watch`):

  1. Vitrin index.html'ini SSRF-güvenli `security_fetch.safe_fetch` ile çeker. Taban adres
     firma ayarlarından (company.get_site_url → tenant_config.domains.storefront_url → SITE_URL
     env) gelir — beyaz etiket: kodda alan adı yok.
  2. Her <script src>, <link rel=modulepreload|preload as=script>, çalıştırılabilir satır içi
     <script> (ld+json gibi veri blokları hariç — kenar SEO katmanı bunları sayfaya göre üretir),
     sunulan CSP başlığı (Content-Security-Policy / -Report-Only) ve varsa asset-manifest.json
     ayrıştırılır; aynı-origin JS paketleri indirilip sha256 özetleri alınır.
  3. Uyarılar (security_event → zincirli denetim kaydı + süper admin e-postası; her DEĞİŞİKLİK için
     BİR KEZ — aynı sorun her 15 dakikada yeniden e-posta üretmez):
       • script, ne aynı-origin ne sunulan CSP script-src izin listesinde ne de özel kod script
         host manifestinde olan bir host'tan yükleniyor  → kritik
       • CSP başlığı kayboldu ya da report-uri/report-to, object-src 'none', frame-ancestors,
         base-uri'yi kaybetti, script-src'ye joker kaynak (*, https:, http:, data:) girdi ya da
         daha önce sunulan ZORLAYICI CSP kalktı (yalnız Report-Only'ye düştü) → kritik;
         CSP script-src'ye yeni host eklendi → uyarı
       • satır içi script'ler (sayı/özet) BİLİNEN BİR DEPLOY OLMADAN değişti → uyarı
       • AYNI dosya adlı bir JS paketinin İÇERİĞİ değişti (CRA dosya adları içerik özetlidir; bu
         asla olmamalı — gerçek kurcalama sinyali) → kritik
       • vitrin daha önce görülmüş bir sürüme döndü (rollback) → uyarı
       • karantinadaki bir parmak izi yeniden sunuluyor → KRİTİK, kısma YOK (her turda)
       • vitrine 4 tur (≈1 saat) üst üste ulaşılamadı → uyarı
  4. BUILD KİMLİĞİ (T19): frontend derlemesi kaynak git commit'ini gömer (craco.config.js →
     Cloudflare Pages CF_PAGES_COMMIT_SHA/CF_PAGES_BRANCH): index.html <meta name="build-sha"> +
     /version.json {"sha","branch","built_at"}. Backend ile frontend AYNI push'tan deploy edilir;
     bu yüzden sunulan build "beklenen" sayılır ancak:
       • sha == backend'in kendi deploy commit'i (RAILWAY_GIT_COMMIT_SHA / GIT_SHA), ya da
       • backend sha'sı BUILD_MATCH_GRACE_S (30 dk) içinde ona dönerse (Railway Pages'ten geç/erken
         biter), ya da
       • daha önce bu yollarla doğrulanmış AYNI sha + AYNI sürüm parmak izi sunuluyorsa (geri dönüş),
       • ya da süper admin o (sha, sürüm) çiftini gerekçeyle onayladıysa (denetimli).
     Aksi halde → KRİTİK `storefront_build_unverified`: sha yok/"unknown" (önceden kimlikli build
     sunulmuşken hemen; ilk kurulumda 30 dk sonra), meta ≠ version.json, dal beklenen dal değil
     (STOREFRONT_RELEASE_BRANCH, yoksa RAILWAY_GIT_BRANCH + "main"), eşleşmeme 30 dk'yı aştı, ya da
     daha önce doğrulanmış bir sha FARKLI içerikle (sürüm parmak izi) sunuluyor. Doğrulanmış yeni
     deploy yalnız denetim kaydına yazılır (storefront_bundle_changed + storefront_build_verified).
     SINIR: build kimliği imzalı değildir; derlemeyi kontrol eden saldırgan backend'in güncel
     sha'sını (version.json'da herkese açık) taklit edebilir — bu durumu paket özeti/kurcalama,
     host izin listesi ve zorlayıcı CSP denetimleri yakalamaya devam eder.
  5. Paket parmak izleri `settings.storefront_integrity` içinde (first_seen/last_seen/
     last_verified) tutulur. asset-manifest'teki parçalar (chunk) ilk görüldüklerinde ve her turda
     en eski doğrulananlardan CHUNK_VERIFY_PER_RUN tanesi yeniden özetlenir (bant genişliği sınırlı).

KARANTİNA (`settings.storefront_quarantine`): süper admin bir parmak izini (paket sha256'sı,
sürüm parmak izi, satır içi script özeti, ya da özel tema / piksel geçmiş sürümü) "kötü" olarak
işaretler (fail-closed `run_audited`). İzleyici o parmak izini yeniden sunulurken görürse (ör.
Cloudflare'de eski deploy'a rollback) her turda kısma olmadan kritik uyarı gönderir. Panelden
yayınlanan kod için karantina ENGELLEYİCİDİR: özel tema / piksel kaydı karantinadaki bir sürümle
aynıysa 409 ile reddedilir (geri dönüş aynı zararlı sürümü getiremez).

Kişisel veri işlenmez: yalnız herkese açık vitrin sayfası, başlıkları ve statik paketler okunur.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Iterable, Optional
from urllib.parse import urljoin, urlsplit

logger = logging.getLogger(__name__)

STATE_ID = "storefront_integrity"
QUARANTINE_ID = "storefront_quarantine"

INDEX_MAX_BYTES = 2 * 1024 * 1024
MANIFEST_MAX_BYTES = 512 * 1024
BUNDLE_MAX_BYTES = 12 * 1024 * 1024
RUN_BYTES_BUDGET = 60 * 1024 * 1024       # tur başına toplam indirme tavanı
FETCH_TIMEOUT = 20.0
CHUNK_VERIFY_PER_RUN = 12                 # tur başına yeniden doğrulanan eski parça sayısı
BUNDLE_KEEP = 800
RELEASES_KEEP = 30
FAIL_ALERT_AFTER = 4                      # üst üste başarısız tur (≈1 saat) → uyarı
VERSION_MAX_BYTES = 16 * 1024
BUILD_MATCH_GRACE_S = 30 * 60             # backend ↔ vitrin build eşleşmesi için bekleme penceresi
BUILDS_KEEP = 50
APPROVAL_ID = "storefront_build_approvals"
_UA = "StorefrontIntegrityWatch/1.0"

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")
_WILDCARD_SCRIPT_SRC = {"*", "http:", "https:", "data:", "http://*", "https://*"}
_JS_TYPES = {"", "text/javascript", "application/javascript", "module", "text/ecmascript",
             "application/ecmascript", "application/x-javascript", "text/x-javascript",
             "text/x-ecmascript", "text/jscript", "text/livescript"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(b) -> str:
    if isinstance(b, str):
        b = b.encode("utf-8")
    return hashlib.sha256(b or b"").hexdigest()


def _db():
    from routes.deps import db
    return db


# ═══════════════════════════════════ Ayrıştırma ═══════════════════════════════════

class _ScriptParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts: list = []
        self.preloads: list = []
        self.build_sha: Optional[str] = None
        self._cur: Optional[dict] = None

    def handle_starttag(self, tag, attrs):
        a = {str(k or "").lower(): (v if v is not None else "") for k, v in attrs}
        if tag == "meta":
            # İlk <meta name="build-sha"> geçerli (sonradan eklenen kopya ilkini ezemez).
            if a.get("name", "").strip().lower() == "build-sha" and self.build_sha is None:
                self.build_sha = a.get("content", "").strip()[:80]
        elif tag == "script":
            self._flush()
            self._cur = {"src": a.get("src", "").strip(), "type": a.get("type", "").strip().lower(),
                         "integrity": a.get("integrity", "").strip(), "text": []}
        elif tag == "link":
            rel = a.get("rel", "").lower().split()
            if a.get("href") and ("modulepreload" in rel or ("preload" in rel and a.get("as", "").lower() == "script")):
                self.preloads.append(a["href"].strip())

    def handle_data(self, data):
        if self._cur is not None:
            self._cur["text"].append(data)

    def handle_endtag(self, tag):
        if tag == "script":
            self._flush()

    def _flush(self):
        if self._cur is not None:
            self._cur["text"] = "".join(self._cur["text"])
            self.scripts.append(self._cur)
            self._cur = None

    def close(self):
        super().close()
        self._flush()


def _is_executable_type(t: str) -> bool:
    t = (t or "").split(";")[0].strip().lower()
    return t in _JS_TYPES or t.startswith("text/javascript")


def parse_index(html: str) -> dict:
    """index.html → {"external": [src…], "inline": [{sha256, csp, length}], "data_blocks": n,
    "build_sha": <meta build-sha içeriği | None>}."""
    p = _ScriptParser()
    try:
        p.feed(html or "")
        p.close()
    except Exception as e:  # noqa: BLE001 — bozuk HTML: elde edilenle devam
        logger.warning("[storefront-integrity] HTML ayrıştırma uyarısı: %s", type(e).__name__)
        p._flush()
    external, inline, data_blocks = [], [], 0
    for s in p.scripts:
        if s["src"]:
            external.append(s["src"])
        elif _is_executable_type(s["type"]):
            text = s["text"]
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            inline.append({"sha256": digest.hex(), "length": len(text),
                           "csp": "'sha256-" + base64.b64encode(digest).decode() + "'"})
        else:
            data_blocks += 1
    for href in p.preloads:
        if href not in external:
            external.append(href)
    return {"external": external, "inline": inline, "data_blocks": data_blocks, "build_sha": p.build_sha}


def parse_csp(value: str) -> dict:
    """CSP başlık değeri → {yönerge: [kaynaklar]} (ilk tanım geçerli, tarayıcı davranışı)."""
    out: dict = {}
    for part in str(value or "").split(";"):
        toks = part.strip().split()
        if not toks:
            continue
        name = toks[0].lower()
        if name not in out:
            out[name] = toks[1:]
    return out


def _script_sources(csp: dict) -> Optional[list]:
    for d in ("script-src-elem", "script-src", "default-src"):
        if d in csp:
            return csp[d]
    return None


_HOST_SRC = re.compile(r"^(?:([a-z][a-z0-9+.-]*)://)?(\*|\*\.[^/:]+|[^/:*]+)(?::(\*|\d+))?(?:/.*)?$")


def _default_port(scheme: str) -> int:
    return 443 if scheme in ("https", "wss") else 80


def source_allows(expr: str, url: str, page_origin: tuple) -> bool:
    """Tek bir CSP kaynak ifadesi `url`'e izin veriyor mu? (CSP3 host/scheme-source eşleşmesi)"""
    e = str(expr or "").strip().lower()
    try:
        u = urlsplit(url)
        scheme, host = (u.scheme or "").lower(), (u.hostname or "").lower()
        port = u.port or _default_port(scheme)
    except ValueError:
        return False
    if not e:
        return False
    if e == "'self'":
        return (scheme, host, port) == page_origin
    if e.startswith("'"):
        return False   # 'unsafe-inline', 'none', nonce/hash, 'strict-dynamic' → harici URL'e izin değil
    if e == "*":
        return scheme in ("http", "https")
    if re.fullmatch(r"[a-z][a-z0-9+.-]*:", e):
        want = e[:-1]
        return scheme == want or (want == "http" and scheme == "https")
    m = _HOST_SRC.match(e)
    if not m:
        return False
    s_scheme, s_host, s_port = m.group(1), m.group(2), m.group(3)
    if s_scheme:
        if not (scheme == s_scheme or (s_scheme == "http" and scheme == "https")):
            return False
    elif scheme not in ("http", "https") or (page_origin[0] == "https" and scheme != "https"):
        return False
    if s_host == "*":
        pass
    elif s_host.startswith("*."):
        if not host.endswith(s_host[1:]) or host == s_host[2:]:
            return False
    elif host != s_host:
        return False
    if s_port and s_port != "*":
        return port == int(s_port)
    if s_port is None:
        return port == _default_port(scheme) or (s_scheme is None and port == _default_port(page_origin[0]))
    return True


def csp_flags(csp: Optional[dict]) -> dict:
    csp = csp or {}
    obj = [x.lower() for x in (csp.get("object-src") or [])]
    script = [x.lower() for x in (_script_sources(csp) or [])]
    return {"present": bool(csp),
            "report": bool(csp.get("report-uri") or csp.get("report-to")),
            "object_src_none": obj == ["'none'"],
            "frame_ancestors": "frame-ancestors" in csp,
            "base_uri": "base-uri" in csp,
            # script-src (yoksa default-src) var ve joker kaynak (*, https:, http:, data:) İÇERMİYOR
            "script_restricted": bool(script) and not (set(script) & _WILDCARD_SCRIPT_SRC)}


def _looks_like_html(res) -> bool:
    ct = str(getattr(res, "content_type", "") or "").lower()
    head = (getattr(res, "content", b"") or b"")[:200].lstrip().lower()
    return "text/html" in ct or head.startswith(b"<!doctype") or head.startswith(b"<html")


def _is_challenge(res) -> bool:
    """Cloudflare bot/challenge sayfası → vitrinin gerçek içeriği değil (ölçüm başarısız sayılır)."""
    h = getattr(res, "headers", {}) or {}
    if str(h.get("cf-mitigated") or "").lower() == "challenge":
        return True
    body = (getattr(res, "content", b"") or b"")[:20000].lower()
    return b"/cdn-cgi/challenge-platform/" in body and (b"just a moment" in body or b"cf-chl" in body)


# ═══════════════════════════════════ Ağ ═══════════════════════════════════

async def _fetch(url: str, max_bytes: int):
    """SSRF-güvenli GET (pinli DNS, yönlendirme hop'ları yeniden denetlenir, boyut/süre sınırlı)."""
    from security_fetch import safe_fetch
    return await safe_fetch(url, max_bytes=max_bytes, timeout=FETCH_TIMEOUT, allow_redirects=3,
                            headers={"User-Agent": _UA, "Cache-Control": "no-cache"})


async def _storefront_base(db) -> str:
    try:
        from company import get_site_url
        return (await get_site_url(db) or "").strip().rstrip("/")
    except Exception as e:  # noqa: BLE001
        logger.warning("[storefront-integrity] vitrin adresi okunamadı: %s", type(e).__name__)
        return ""


# ═══════════════════════════════════ Build kimliği ═══════════════════════════════════

def norm_git_sha(v) -> str:
    """Geçerli (7-40 onaltılık) git commit'i → küçük harf; "unknown", boş, işlenmemiş
    %REACT_APP_BUILD_SHA% ya da başka bir şey → ""."""
    s = str(v or "").strip().lower()
    return s if _GIT_SHA_RE.match(s) else ""


def git_sha_eq(a: str, b: str) -> bool:
    """Kısa/uzun biçimler arası eşleşme (biri diğerinin öneki, en az 7 karakter)."""
    a, b = norm_git_sha(a), norm_git_sha(b)
    return bool(a and b) and min(len(a), len(b)) >= 7 and (a.startswith(b) or b.startswith(a))


def backend_build_sha() -> str:
    """Backend'in kendi deploy commit'i (Railway RAILWAY_GIT_COMMIT_SHA; kendi barındırmada GIT_SHA)."""
    return norm_git_sha(os.environ.get("RAILWAY_GIT_COMMIT_SHA") or os.environ.get("GIT_SHA"))


def expected_branches() -> list:
    """Vitrinin yayın dalı. Beyaz etiket: STOREFRONT_RELEASE_BRANCH (virgüllü liste) → yoksa
    backend'in deploy dalı (RAILWAY_GIT_BRANCH) + "main"."""
    raw = (os.environ.get("STOREFRONT_RELEASE_BRANCH") or "").strip()
    if raw:
        vals = [x.strip() for x in raw.split(",")]
    else:
        vals = [(os.environ.get("RAILWAY_GIT_BRANCH") or "").strip(), "main"]
    return [v for v in dict.fromkeys(vals) if v and _BRANCH_RE.match(v)]


def _norm_branch(v) -> str:
    s = str(v or "").strip()
    return s if (_BRANCH_RE.match(s) and s.lower() not in ("unknown", "head")) else ""


async def _fetch_version(base: str, budget: dict) -> dict:
    """/version.json → {"status": ok|absent|invalid|error, "sha", "branch", "built_at"}."""
    try:
        res = await _fetch(base + "/version.json", VERSION_MAX_BYTES)
    except Exception as e:  # noqa: BLE001
        return {"status": f"error:{type(e).__name__}"}
    budget["left"] -= len(res.content or b"")
    if res.status_code != 200 or _looks_like_html(res):
        return {"status": "absent"}   # SPA geri dönüşü: bu deploy version.json üretmemiş
    try:
        data = json.loads(res.content.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return {"status": "invalid"}
    if not isinstance(data, dict):
        return {"status": "invalid"}
    return {"status": "ok", "sha_raw": str(data.get("sha") or "")[:80], "sha": norm_git_sha(data.get("sha")),
            "branch": _norm_branch(data.get("branch")), "built_at": str(data.get("built_at") or "")[:40]}


async def build_approvals(db) -> list:
    doc = await db.settings.find_one({"id": APPROVAL_ID}, {"_id": 0}) or {}
    return [i for i in (doc.get("items") or []) if isinstance(i, dict) and i.get("sha")]


def _age_s(since: str, now_iso: str) -> float:
    try:
        return (datetime.fromisoformat(now_iso) - datetime.fromisoformat(str(since))).total_seconds()
    except Exception:  # noqa: BLE001
        return 0.0


def evaluate_build(prev: dict, meta_sha_raw, version: dict, release_fp: str, run_at: str,
                   approvals: list) -> tuple:
    """Sunulan build kimliğini değerlendirir → (build özeti, builds listesi, missing_since, sorunlar, olaylar).

    Saf fonksiyon (ağ/DB yok) — kurallar modül başındaki 4. maddede."""
    meta_sha = norm_git_sha(meta_sha_raw)
    file_sha = version.get("sha") or ""
    branch = version.get("branch") or ""
    sha = meta_sha or file_sha
    backend = backend_build_sha()
    branches = expected_branches()
    builds = {b["sha"]: dict(b) for b in (prev.get("builds") or []) if isinstance(b, dict) and b.get("sha")}
    had_identity = bool(builds) or bool((prev.get("build") or {}).get("sha"))
    issues, events = [], []
    missing_since = ""
    summary = {"sha": sha, "meta_sha": meta_sha, "file_sha": file_sha, "branch": branch,
               "built_at": version.get("built_at") or "", "version_status": version.get("status") or "",
               "backend_sha": backend, "expected_branches": branches, "verified_by": "", "status": ""}
    body_tail = ("Bu deploy'u siz (main'e push) yapmadıysanız Cloudflare Pages'te güvenli bir deploy'u "
                 "yeniden yayınlayın ve Pages/GitHub erişim kayıtlarını inceleyin. Meşru bir deploy ise "
                 "(ör. backend'in yeniden deploy edilmediği yalnız-frontend değişikliği) süper admin olarak "
                 "POST /api/admin/security/storefront-integrity/approve-build {sha, reason} ile gerekçeli onay "
                 "verin (denetim kaydına yazılır; onay yalnız bu sha + sürüm için geçerlidir).")

    def _issue(problem: str, key: str, detail: dict):
        issues.append({"key": key, "kind": "storefront_build_unverified",
                       "meta": {"problem": problem, "build": (sha or "-")[:12], "backend": (backend or "-")[:12],
                                **detail},
                       "body": "Vitrinde sunulan build, backend'in deploy ettiği commit ile doğrulanamadı. " + body_tail})

    if meta_sha and file_sha and not git_sha_eq(meta_sha, file_sha):
        summary["status"] = "conflict"
        _issue("index.html build-sha ile version.json farklı", f"build:conflict:{meta_sha[:12]}:{file_sha[:12]}",
               {"meta": meta_sha[:12], "version_json": file_sha[:12]})
    elif not sha:
        missing_since = prev.get("build_missing_since") or run_at
        if had_identity:
            summary["status"] = "missing"
            _issue("build kimliği yok (önceki build'ler kimlikliydi)", f"build:missing:{release_fp[:16]}",
                   {"version_json": version.get("status") or "-"})
        elif _age_s(missing_since, run_at) > BUILD_MATCH_GRACE_S:
            summary["status"] = "missing"
            _issue("build kimliği yok (30 dk'dır)", f"build:missing:{release_fp[:16]}",
                   {"version_json": version.get("status") or "-"})
        else:
            summary["status"] = "pending"
    elif branch and branches and branch not in branches:
        summary["status"] = "branch"
        _issue(f"beklenmeyen dal: {branch}", f"build:branch:{sha[:12]}:{branch[:40]}",
               {"dal": branch[:60], "beklenen": ", ".join(branches)[:120]})
    else:
        rec = builds.get(sha) or {"sha": sha, "branch": branch, "first_seen": run_at, "release_fps": []}
        rec["last_seen"] = run_at
        fps = list(rec.get("release_fps") or [])
        approved = next((a for a in approvals if git_sha_eq(a.get("sha"), sha)
                         and a.get("release_fp") in (release_fp, "", None)), None)
        if backend and git_sha_eq(sha, backend):
            how = "backend"
        elif approved:
            how = "approval"
        elif rec.get("verified_at") and release_fp in fps:
            how = rec.get("verified_by") or "backend"      # daha önce doğrulanmış aynı sürüm (geri dönüş)
        else:
            how = ""
        if how:
            if not rec.get("verified_at") or release_fp not in fps:     # yeni (sha, sürüm) çifti
                events.append(("storefront_build_verified", {"build": sha[:12], "dogrulayan": how,
                                                             "surum": release_fp[:16], "fp": f"{sha[:12]}:{release_fp[:8]}"}))
            rec.setdefault("verified_at", run_at)
            rec["verified_by"] = rec.get("verified_by") or how
            if release_fp not in fps:
                fps.append(release_fp)
            summary.update(status="verified", verified_by=how)
        elif not backend:
            summary["status"] = "unverifiable"         # backend commit'i bilinmiyor (kendi barındırma)
        elif rec.get("verified_at"):
            # Daha önce doğrulanmış sha, DAHA ÖNCE GÖRÜLMEMİŞ içerikle (sürüm parmak izi) sunuluyor.
            summary["status"] = "unverified"
            _issue("doğrulanmış build kimliği farklı içerikle sunuluyor", f"build:content:{sha[:12]}:{release_fp[:16]}",
                   {"surum": release_fp[:16]})
        elif _age_s(rec.get("first_seen") or run_at, run_at) > BUILD_MATCH_GRACE_S:
            summary["status"] = "unverified"
            _issue("backend deploy commit'iyle 30 dk'dır eşleşmiyor", f"build:unverified:{sha[:12]}",
                   {"ilk_gorulme": str(rec.get("first_seen") or "")[:19]})
        else:
            summary["status"] = "pending"
        rec["release_fps"] = fps[-5:]
        builds[sha] = rec
    blist = sorted(builds.values(), key=lambda b: str(b.get("last_seen") or ""), reverse=True)[:BUILDS_KEEP]
    return summary, blist, missing_since, issues, events


# ═══════════════════════════════════ Karantina ═══════════════════════════════════

async def quarantine_items(db=None) -> list:
    db = db if db is not None else _db()
    doc = await db.settings.find_one({"id": QUARANTINE_ID}, {"_id": 0}) or {}
    return [i for i in (doc.get("items") or []) if isinstance(i, dict) and i.get("sha256")]


async def quarantined_set(db=None) -> set:
    return {str(i["sha256"]).lower() for i in await quarantine_items(db)}


async def assert_code_not_quarantined(codes: Iterable[Optional[str]], *, source: str,
                                      actor: Optional[dict] = None, request=None) -> None:
    """Panelden yayınlanacak kod (özel tema / piksel) karantinadaki bir sürümle aynıysa 409.
    Karantina listesi okunamazsa FAIL-CLOSED (503): vitrinde ödeme sayfasında da çalışan kod,
    doğrulanamadan yayınlanmaz."""
    from fastapi import HTTPException
    shas = {_sha(str(c)) for c in codes if c}
    if not shas:
        return
    try:
        q = await quarantined_set()
    except Exception:
        raise HTTPException(status_code=503, detail="Karantina listesi doğrulanamadı; güvenlik gereği kod yayınlanmadı. "
                                                    "Tekrar deneyin.")
    hit = sorted(shas & q)
    if not hit:
        return
    try:
        from security.events import security_event
        await security_event("storefront_quarantine_blocked", actor,
                             {"source": source, "sha256": ", ".join(h[:16] for h in hit), "fp": hit[0][:16]},
                             request=request,
                             body="Karantinaya alınmış (zararlı işaretli) bir kod sürümü yeniden yayınlanmak istendi; "
                                  "işlem engellendi.")
    except Exception:  # noqa: BLE001
        pass
    raise HTTPException(status_code=409, detail="Bu kod karantinaya alınmış (zararlı işaretli) bir sürümle aynı; "
                                                "yayınlanamaz. Güvenlik Paneli → Vitrin bütünlüğü.")


# ═══════════════════════════════════ İzleyici ═══════════════════════════════════

def _bundle_key(url: str) -> str:
    try:
        return urlsplit(url).path or "/"
    except ValueError:
        return ""


async def _hash_bundle(url: str, budget: dict) -> dict:
    """Aynı-origin paketi indirip özetler → {"status": ok|missing|html_fallback|error|budget, ...}."""
    if budget["left"] <= 0:
        return {"status": "budget"}
    try:
        res = await _fetch(url, min(BUNDLE_MAX_BYTES, max(1, budget["left"])))
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": type(e).__name__}
    budget["left"] -= len(res.content or b"")
    if res.status_code != 200:
        return {"status": "missing", "http": res.status_code}
    if _looks_like_html(res):
        return {"status": "html_fallback"}   # Pages SPA geri dönüşü: dosya bu deploy'da yok
    return {"status": "ok", "sha256": _sha(res.content), "size": len(res.content)}


async def _manifest_js(base: str, budget: dict) -> tuple:
    """asset-manifest.json → (js yolları listesi, durum)."""
    try:
        res = await _fetch(base + "/asset-manifest.json", MANIFEST_MAX_BYTES)
    except Exception as e:  # noqa: BLE001
        return [], f"error:{type(e).__name__}"
    budget["left"] -= len(res.content or b"")
    if res.status_code != 200 or _looks_like_html(res):
        return [], "absent"
    try:
        data = json.loads(res.content.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return [], "invalid"
    vals = []
    files = data.get("files") if isinstance(data, dict) else None
    if isinstance(files, dict):
        vals += [v for v in files.values() if isinstance(v, str)]
    ents = data.get("entrypoints") if isinstance(data, dict) else None
    if isinstance(ents, list):
        vals += ["/" + v.lstrip("/") for v in ents if isinstance(v, str)]
    out = []
    for v in vals:
        path = _bundle_key(urljoin(base + "/", v))
        if path.endswith(".js") and path not in out:
            out.append(path)
    return out[:400], "ok"


async def _alert_issue(kind: str, key: str, meta: dict, *, body: str = "") -> None:
    from security.events import security_event
    meta = dict(meta or {})
    meta.setdefault("fp", hashlib.sha256(key.encode()).hexdigest()[:16])
    await security_event(kind, None, meta, body=body)


async def _alert_quarantined(hits: list, run_at: str) -> None:
    """Karantinadaki sürüm sunuluyor → denetim kaydı + KISMASIZ kritik uyarı (benzersiz parmak izi)."""
    from security.events import security_event
    from security.alerts import send_alert
    desc = "; ".join(f"{h.get('kind') or '?'} {h.get('label') or ''} {h['sha256'][:16]}"[:160] for h in hits[:10])
    meta = {"hits": desc, "count": len(hits), "fp": hits[0]["sha256"][:16]}
    await security_event("storefront_quarantined_served", None, meta, alert=False)
    await send_alert(
        "storefront_quarantined_served",
        "KARANTİNADAKİ vitrin sürümü yeniden sunuluyor",
        "Vitrin bütünlük izleyicisi, 'kötü/karantina' olarak işaretlenmiş bir parmak izini müşteriye sunulan "
        f"vitrinde gördü (ör. eski bir deploy'a geri dönüş).\n\nEşleşen: {desc}\n\n"
        "Hemen Cloudflare Pages'te güvenli bir deploy'u yeniden yayınlayın ve Güvenlik Paneli → Vitrin "
        "bütünlüğü ekranını inceleyin. Bu uyarı sorun sürdükçe her turda (15 dk) tekrarlanır.",
        level="critical", fingerprint=f"storefront_quarantine:{hits[0]['sha256'][:16]}:{run_at}",
        meta={"kind": "storefront_quarantined_served"})


async def run_storefront_integrity_check(*, db=None) -> dict:
    """Bir izleme turu. Sonucu `settings.storefront_integrity`'e yazar ve döndürür. Asla fırlatmaz."""
    try:
        return await _run(db if db is not None else _db())
    except Exception as e:  # noqa: BLE001
        logger.warning("[storefront-integrity] tur başarısız: %s: %s", type(e).__name__, e)
        try:
            await (db if db is not None else _db()).settings.update_one(
                {"id": STATE_ID}, {"$set": {"last_run_at": _now(), "last_status": "error",
                                            "last_error": f"{type(e).__name__}"[:120]}}, upsert=True)
        except Exception:  # noqa: BLE001
            pass
        return {"status": "error", "error": type(e).__name__}


async def _run(db) -> dict:
    run_at = _now()
    t0 = time.monotonic()
    prev = await db.settings.find_one({"id": STATE_ID}, {"_id": 0}) or {}
    base = await _storefront_base(db)
    if not base:
        await db.settings.update_one({"id": STATE_ID}, {"$set": {
            "id": STATE_ID, "last_run_at": run_at, "last_status": "skipped",
            "last_error": "vitrin adresi (firma ayarları → site adresi) tanımlı değil"}}, upsert=True)
        return {"status": "skipped"}

    # ── 1) index.html + sunulan CSP ──
    fail_meta = None
    try:
        res = await _fetch(base + "/", INDEX_MAX_BYTES)
        if res.status_code != 200:
            fail_meta = f"HTTP {res.status_code}"
        elif _is_challenge(res):
            fail_meta = "Cloudflare doğrulama sayfası döndü (bot koruması)"
    except Exception as e:  # noqa: BLE001
        res, fail_meta = None, f"{type(e).__name__}"
    if fail_meta:
        fails = int(prev.get("consecutive_failures") or 0) + 1
        alerted = list(prev.get("alerted_keys") or [])
        if fails >= FAIL_ALERT_AFTER and "fetch_failed" not in alerted:
            await _alert_issue("storefront_watch_failed", "fetch_failed",
                               {"base": base, "error": fail_meta, "consecutive": fails},
                               body="Vitrin bütünlüğü bu süre boyunca doğrulanamadı.")
            alerted.append("fetch_failed")
        await db.settings.update_one({"id": STATE_ID}, {"$set": {
            "id": STATE_ID, "last_run_at": run_at, "last_status": "error", "last_error": fail_meta[:160],
            "consecutive_failures": fails, "alerted_keys": alerted, "base_url": base}}, upsert=True)
        return {"status": "error", "error": fail_meta}

    page_url = res.url or (base + "/")
    pu = urlsplit(page_url)
    page_origin = ((pu.scheme or "https").lower(), (pu.hostname or "").lower(),
                   pu.port or _default_port((pu.scheme or "https").lower()))
    html = res.content.decode("utf-8", errors="replace")
    parsed = parse_index(html)
    hdr = res.headers or {}
    enf_value = str(hdr.get("content-security-policy") or "")
    ro_value = str(hdr.get("content-security-policy-report-only") or "")
    if enf_value:
        csp_name, csp_value = "Content-Security-Policy", enf_value
    elif ro_value:
        csp_name, csp_value = "Content-Security-Policy-Report-Only", ro_value
    else:
        csp_name, csp_value = "", ""
    csp = parse_csp(csp_value)
    flags = csp_flags(csp)
    script_srcs = _script_sources(csp) or []

    try:
        from routes.custom_theme import script_manifest
        manifest_hosts = set((await script_manifest() or {}).get("hosts") or [])
    except Exception:  # noqa: BLE001
        manifest_hosts = set()

    issues: list = []        # {"key","kind","level","meta","body"}

    # ── 2) Script kaynakları: aynı-origin / CSP izin listesi / özel kod manifesti ──
    scripts, same_origin_urls = [], []
    for src in parsed["external"]:
        url = urljoin(page_url, src)
        su = urlsplit(url)
        host = (su.hostname or "").lower()
        same = ((su.scheme or "").lower(), host, su.port or _default_port((su.scheme or "").lower())) == page_origin
        allowed_by = "same-origin" if same else (
            "csp" if any(source_allows(x, url, page_origin) for x in script_srcs) else (
                "manifest" if host and host in manifest_hosts else ""))
        scripts.append({"src": src[:300], "host": host or su.scheme, "allowed_by": allowed_by or "YOK"})
        if same:
            same_origin_urls.append(url)
        elif not allowed_by:
            issues.append({"key": f"host:{host or su.scheme}", "kind": "storefront_unknown_script_host",
                           "meta": {"host": host or su.scheme, "src": src[:200]},
                           "body": "Vitrin index.html'i ne aynı-origin ne CSP script-src ne de özel kod manifestinde "
                                   "olan bir adresten script yüklüyor. Siz eklemediyseniz deploy'u hemen geri alın."})

    # ── 3) CSP başlığı ──
    if not flags["present"]:
        issues.append({"key": "csp:missing", "kind": "storefront_csp_weakened",
                       "meta": {"problem": "CSP başlığı yok", "header": "-"},
                       "body": "Vitrin artık hiçbir CSP başlığı (Content-Security-Policy[-Report-Only]) sunmuyor."})
    else:
        for flag, label in (("report", "report-uri/report-to yok"), ("object_src_none", "object-src 'none' yok"),
                            ("frame_ancestors", "frame-ancestors yok"), ("base_uri", "base-uri yok"),
                            ("script_restricted", "script-src yok ya da joker kaynak (*, https:, data:) içeriyor")):
            if not flags[flag]:
                issues.append({"key": f"csp:{flag}", "kind": "storefront_csp_weakened",
                               "meta": {"problem": label, "header": csp_name},
                               "body": "Vitrinin CSP başlığı zayıfladı."})
        if prev.get("csp_enforced_ever") and not enf_value:
            # Zorlayıcı CSP daha önce sunuluyordu; artık yalnız Report-Only → tarayıcı hiçbir şeyi engellemiyor.
            issues.append({"key": "csp:enforce_lost", "kind": "storefront_csp_weakened",
                           "meta": {"problem": "zorlayıcı CSP kalktı (yalnız Report-Only)", "header": csp_name},
                           "body": "Vitrin artık zorlayıcı Content-Security-Policy sunmuyor; tarayıcı izinsiz script'leri "
                                   "engellemiyor. frontend/public/_headers'ı ve son deploy'u inceleyin."})
    # Sıkı Report-Only (ölçüm): sunulan satır içi script özetlerinden politikada OLMAYANLAR — _headers
    # güncellemesi için bilgi (uyarı üretmez).
    ro_script = set(_script_sources(parse_csp(ro_value)) or []) if (enf_value and ro_value) else set()
    strict_missing = [i["csp"] for i in parsed["inline"] if ro_script and i["csp"] not in ro_script]
    prev_srcs = set(prev.get("csp_script_src") or [])
    if prev.get("csp_sha256") and prev_srcs:
        for s in sorted(set(script_srcs) - prev_srcs):
            if s.startswith("'") and s not in ("'unsafe-inline'", "'unsafe-eval'", "'unsafe-hashes'"):
                continue
            issues.append({"key": f"csp_src:{s}", "kind": "storefront_csp_source_added",
                           "meta": {"source": s[:120], "header": csp_name},
                           "body": "CSP script izin listesine yeni bir kaynak eklendi. Bu değişikliği siz yapmadıysanız "
                                   "frontend/public/_headers'ı ve son deploy'u inceleyin."})

    # ── 4) Paketler: index'teki aynı-origin script'ler + asset-manifest parçaları ──
    budget = {"left": RUN_BYTES_BUDGET}
    bundles = {b["path"]: dict(b) for b in (prev.get("bundles") or []) if isinstance(b, dict) and b.get("path")}
    known_paths = set(bundles)
    entry_paths = [p for p in (_bundle_key(u) for u in same_origin_urls) if p]
    manifest_paths, manifest_status = await _manifest_js(base, budget)
    new_paths = [p for p in dict.fromkeys(entry_paths + manifest_paths) if p not in known_paths]
    deploy = any(p not in known_paths for p in entry_paths)

    to_verify = list(dict.fromkeys(entry_paths + [p for p in manifest_paths if p not in known_paths]))
    old_chunks = sorted((p for p in manifest_paths if p in known_paths and p not in entry_paths),
                        key=lambda p: str(bundles[p].get("last_verified") or ""))
    to_verify += old_chunks[:CHUNK_VERIFY_PER_RUN]
    verified, skipped = 0, {}
    origin_base = f"{page_origin[0]}://{pu.netloc}"
    for path in to_verify:
        r = await _hash_bundle(origin_base + path, budget)
        if r["status"] != "ok":
            skipped[r["status"]] = skipped.get(r["status"], 0) + 1
            continue
        verified += 1
        ent = bundles.get(path)
        if ent is None:
            bundles[path] = {"path": path, "sha256": r["sha256"], "size": r["size"], "first_seen": run_at,
                             "last_seen": run_at, "last_verified": run_at, "entry": path in entry_paths}
            continue
        ent.update({"last_seen": run_at, "last_verified": run_at, "entry": path in entry_paths})
        if r["sha256"] != ent.get("sha256"):
            # İçerik özetli dosya adı AYNI, içerik FARKLI → kurcalama sinyali (taban özet DEĞİŞTİRİLMEZ).
            ent["changed_sha256"], ent["changed_at"] = r["sha256"], run_at
            issues.append({"key": f"tamper:{path}:{r['sha256'][:16]}", "kind": "storefront_bundle_tampered",
                           "meta": {"bundle": path, "expected": ent["sha256"][:16], "served": r["sha256"][:16]},
                           "body": "Aynı (içerik özetli) dosya adıyla sunulan JS paketinin içeriği değişti. Derleme "
                                   "çıktıları asla böyle değişmez: CDN/Pages hesabında kurcalama olabilir. Hemen "
                                   "güvenli bir deploy'u yeniden yayınlayın ve erişim kayıtlarını inceleyin."})
        elif ent.get("changed_sha256"):
            ent.pop("changed_sha256", None)
            ent.pop("changed_at", None)
    for p in entry_paths + manifest_paths:
        if p in bundles:
            bundles[p]["last_seen"] = run_at
    for p, b in bundles.items():
        b["entry"] = p in entry_paths

    # ── 5) Satır içi script'ler ve sürüm parmak izi ──
    inline_hashes = sorted(i["sha256"] for i in parsed["inline"])
    inline_fp = _sha("\n".join(inline_hashes))
    entry_lines = sorted(f"{p}={(bundles.get(p) or {}).get('sha256', '?')}" for p in entry_paths)
    release_fp = _sha("\n".join(entry_lines + inline_hashes))
    first_run = not prev.get("release_fp")
    inline_changed = (not first_run) and prev.get("inline_fp") and prev.get("inline_fp") != inline_fp
    if inline_changed and not deploy:
        issues.append({"key": f"inline:{inline_fp[:16]}", "kind": "storefront_inline_changed",
                       "meta": {"eski_adet": len(prev.get("inline_hashes") or []), "yeni_adet": len(inline_hashes),
                                "eklenen": ", ".join(h[:12] for h in sorted(set(inline_hashes) - set(prev.get("inline_hashes") or [])))[:300]},
                       "body": "Vitrin index.html'indeki satır içi script'ler yeni bir paket (deploy) olmadan değişti. "
                               "public/index.html'i siz değiştirip yalnız onu yayınladıysanız bu beklenen bir "
                               "durumdur; aksi halde son deploy'u ve Pages erişimini inceleyin."})

    releases = [r for r in (prev.get("releases") or []) if isinstance(r, dict)]
    rel = next((r for r in releases if r.get("fp") == release_fp), None)
    events = []
    if not first_run and prev.get("release_fp") != release_fp:
        if deploy:
            events.append(("storefront_bundle_changed", {
                "yeni_paket": ", ".join(p.rsplit("/", 1)[-1] for p in entry_paths if p not in known_paths)[:300],
                "yeni_parca": len(new_paths), "surum": release_fp[:16],
                "satir_ici_degisti": bool(inline_changed), "fp": release_fp[:16]}))
        elif rel is not None and set(entry_paths) != set(prev.get("entry_paths") or []):
            issues.append({"key": f"rollback:{release_fp[:16]}", "kind": "storefront_rollback",
                           "meta": {"surum": release_fp[:16], "ilk_gorulme": str(rel.get("first_seen") or "")[:19],
                                    "paketler": ", ".join(p.rsplit("/", 1)[-1] for p in entry_paths)[:300]},
                           "body": "Vitrin daha önce sunulmuş bir sürüme geri döndü (rollback). Bu sürüm daha önce "
                                   "zararlı olarak işaretlendiyse ayrıca kritik karantina uyarısı gelir."})
    if rel is None:
        rel = {"fp": release_fp, "first_seen": run_at, "entries": [p.rsplit("/", 1)[-1] for p in entry_paths][:10],
               "inline": len(inline_hashes)}
        releases.append(rel)
    rel["last_seen"] = run_at
    releases = sorted(releases, key=lambda r: str(r.get("last_seen") or ""), reverse=True)[:RELEASES_KEEP]

    # ── 5b) Build kimliği (T19): meta build-sha + /version.json ↔ backend deploy commit'i ──
    version = await _fetch_version(base, budget)
    try:
        approvals = await build_approvals(db)
    except Exception:  # noqa: BLE001
        approvals = []
    build, builds, build_missing_since, b_issues, b_events = evaluate_build(
        prev, parsed.get("build_sha"), version, release_fp, run_at, approvals)
    rel["build"] = (build.get("sha") or "")[:12]
    issues += b_issues
    events += b_events
    for kind, meta in events:
        if kind == "storefront_bundle_changed":
            meta.update({"build": (build.get("sha") or "-")[:12], "build_durum": build.get("status") or "-"})

    # ── 6) Karantina: sunulan parmak izleri ──
    try:
        q_items = await quarantine_items(db)
    except Exception:  # noqa: BLE001
        q_items = []
    q_map = {str(i["sha256"]).lower(): i for i in q_items}
    served = {release_fp: ("release", release_fp[:16])}
    for h in inline_hashes:
        served[h] = ("inline", h[:16])
    for p in entry_paths + manifest_paths:
        b = bundles.get(p) or {}
        for k in ("sha256", "changed_sha256"):
            if b.get(k):
                served[b[k]] = ("bundle", p.rsplit("/", 1)[-1])
    q_hits = [{"sha256": s, "kind": served[s][0], "label": served[s][1],
               "reason": str(q_map[s].get("reason") or "")[:80]} for s in served if s in q_map]
    if q_hits:
        await _alert_quarantined(q_hits, run_at)

    # ── 7) Uyarılar: her değişiklik için bir kez ──
    prev_alerted = set(prev.get("alerted_keys") or []) - {"fetch_failed"}
    current_keys = []
    for it in issues:
        current_keys.append(it["key"])
        if it["key"] in prev_alerted:
            continue
        await _alert_issue(it["kind"], it["key"], it["meta"], body=it.get("body", ""))
    for kind, meta in events:
        from security.events import security_event
        await security_event(kind, None, meta, alert=False)   # beklenen deploy: yalnız denetim kaydı

    # Paket listesi tavanı: şu an referans verilenler korunur, kalanlar en son görülene göre.
    current_set = set(entry_paths) | set(manifest_paths)
    keep = [b for b in bundles.values() if b["path"] in current_set]
    rest = sorted((b for b in bundles.values() if b["path"] not in current_set),
                  key=lambda b: str(b.get("last_seen") or ""), reverse=True)
    bundle_list = (keep + rest)[:BUNDLE_KEEP]

    critical = {"storefront_unknown_script_host", "storefront_csp_weakened", "storefront_bundle_tampered",
                "storefront_build_unverified"}
    status = "fail" if (q_hits or any(i["kind"] in critical for i in issues)) else (
        "warn" if issues else "ok")
    summary = {
        "id": STATE_ID, "base_url": base, "page_url": page_url[:200], "last_run_at": run_at,
        "last_status": status, "last_error": "", "consecutive_failures": 0,
        "duration_ms": int((time.monotonic() - t0) * 1000),
        "issues": [{"kind": i["kind"], "key": i["key"][:200],
                    "detail": "; ".join(f"{k}={v}" for k, v in i["meta"].items() if k != "fp")[:300]} for i in issues],
        "quarantine_hits": q_hits,
        "alerted_keys": current_keys,
        "index_sha256": _sha(res.content), "index_bytes": len(res.content),
        "scripts": scripts[:60], "entry_paths": entry_paths,
        "inline_hashes": inline_hashes, "inline_fp": inline_fp, "inline_count": len(inline_hashes),
        "inline_csp_sources": [i["csp"] for i in parsed["inline"]][:60],
        "data_blocks": parsed["data_blocks"],
        "csp_header": csp_name, "csp_sha256": _sha(csp_value) if csp_value else "", "csp_flags": flags,
        "csp_script_src": script_srcs[:120],
        "csp_enforced": bool(enf_value), "csp_enforced_ever": bool(prev.get("csp_enforced_ever") or enf_value),
        "csp_report_only": bool(ro_value), "csp_report_only_sha256": _sha(ro_value) if ro_value else "",
        "strict_csp_inline_missing": strict_missing[:30],
        "build": build, "builds": builds, "build_missing_since": build_missing_since,
        "manifest_status": manifest_status, "manifest_js": len(manifest_paths),
        "verified_this_run": verified, "skipped_this_run": skipped,
        "bytes_this_run": RUN_BYTES_BUDGET - budget["left"],
        "release_fp": release_fp, "releases": releases, "bundles": bundle_list,
        "deploy_detected": bool(deploy and not first_run),
        # Özeti hiç alınamamış ana paket (boyut tavanı / SPA geri dönüşü / ağ) → öz-test uyarır.
        "entry_unverified": [p for p in entry_paths if not (bundles.get(p) or {}).get("sha256")],
    }
    if prev.get("csp_sha256") and summary["csp_sha256"] != prev.get("csp_sha256"):
        from security.events import security_event
        await security_event("storefront_csp_changed", None,
                             {"header": csp_name or "-", "fp": summary["csp_sha256"][:16]}, alert=False)
    await db.settings.update_one({"id": STATE_ID}, {"$set": summary}, upsert=True)
    if issues or q_hits:
        logger.warning("[storefront-integrity] %s: %d sorun, %d karantina eşleşmesi", status, len(issues), len(q_hits))
    return {k: summary[k] for k in ("last_status", "issues", "quarantine_hits", "release_fp", "verified_this_run",
                                    "deploy_detected", "inline_count", "csp_flags", "csp_enforced", "build")}


# ═══════════════════════════════════ Öz-test ═══════════════════════════════════

async def selftest_status(db) -> tuple:
    """("ok"|"warn"|"fail", açıklama) — security/selftest.py "Vitrin bütünlüğü"."""
    st = await db.settings.find_one({"id": STATE_ID}, {"_id": 0, "bundles": 0, "releases": 0, "builds": 0}) or {}
    q = await quarantine_items(db)
    # Panelden yayınlanan kod şu an karantinadaki bir sürümle aynı mı (doğrudan DB değişikliği)?
    live_codes = []
    ct = await db.settings.find_one({"id": "custom_theme"}, {"_id": 0}) or {}
    live_codes += [ct.get("custom_css"), ct.get("custom_js") if ct.get("js_enabled") else None]
    async for p in db.marketing_pixels.find({"is_active": True}, {"_id": 0, "head_snippet": 1, "body_snippet": 1}):
        live_codes += [p.get("head_snippet"), p.get("body_snippet")]
    qs = {str(i["sha256"]).lower() for i in q}
    live_hits = [c for c in live_codes if c and _sha(str(c)) in qs]
    if live_hits:
        return "fail", f"karantinadaki {len(live_hits)} özel kod/piksel sürümü şu an vitrinde yayında"
    if not st:
        return "warn", "vitrin bütünlük izleyicisi henüz çalışmadı (15 dk'lık iş)"
    last = str(st.get("last_run_at") or "")
    status = st.get("last_status")
    if status == "skipped":
        return "warn", st.get("last_error") or "vitrin adresi tanımlı değil"
    try:
        age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() / 3600
    except Exception:  # noqa: BLE001
        age_h = 999
    if age_h > 2:
        return "warn", f"izleyici {int(age_h)} saattir çalışmadı (son: {last[:16]})"
    if status == "error":
        return "warn", (f"vitrine ulaşılamadı: {st.get('last_error') or '?'} "
                        f"({st.get('consecutive_failures') or 0} tur üst üste)")
    if st.get("quarantine_hits"):
        return "fail", "KARANTİNADAKİ sürüm sunuluyor: " + ", ".join(
            f"{h.get('kind')} {h.get('label')}" for h in st["quarantine_hits"][:5])
    if status == "fail":
        return "fail", "; ".join(f"{i.get('kind')}: {i.get('detail')}" for i in (st.get("issues") or [])[:4])[:400]
    fl = st.get("csp_flags") or {}
    tail = (f"{len(st.get('scripts') or [])} harici + {st.get('inline_count', 0)} satır içi script, "
            f"{st.get('verified_this_run', 0)} paket özeti bu turda doğrulandı; "
            f"CSP: {st.get('csp_header') or 'YOK'} (report={'var' if fl.get('report') else 'yok'}, "
            f"object-src 'none'={'var' if fl.get('object_src_none') else 'yok'}, "
            f"frame-ancestors={'var' if fl.get('frame_ancestors') else 'yok'}); karantina: {len(q)} kayıt; "
            f"son: {last[:16]}")
    b = st.get("build") or {}
    b_label = {"verified": f"doğrulandı ({b.get('verified_by') or '?'})", "pending": "backend eşleşmesi bekleniyor",
               "unverifiable": "backend commit'i bilinmiyor"}.get(b.get("status"), b.get("status") or "?")
    tail = f"build {(b.get('sha') or 'YOK')[:12]}: {b_label}; " + tail
    if st.get("entry_unverified"):
        return "warn", ("ana JS paketinin özeti alınamadı (boyut tavanı / erişim): "
                        + ", ".join(st["entry_unverified"][:3]) + " — " + tail)
    if not st.get("csp_enforced"):
        return "warn", "CSP zorlamada değil (yalnız Report-Only ya da hiç) — " + tail
    if b.get("status") == "unverifiable":
        return "warn", ("vitrin build kimliği doğrulanamıyor: backend deploy commit'i (RAILWAY_GIT_COMMIT_SHA / "
                        "GIT_SHA) tanımlı değil — " + tail)
    if status == "warn":
        return "warn", "; ".join(f"{i.get('kind')}: {i.get('detail')}" for i in (st.get("issues") or [])[:3])[:300] + " — " + tail
    return "ok", tail


# ═══════════════════════════════════ Yönetim uçları ═══════════════════════════════════
from fastapi import APIRouter, Depends, HTTPException, Request  # noqa: E402

from routes.deps import require_super_admin  # noqa: E402

router = APIRouter(prefix="/admin/security/storefront-integrity", tags=["security-storefront-integrity"])


@router.get("")
async def get_storefront_integrity(current_user: dict = Depends(require_super_admin)):
    """İzleyicinin son durumu + karantina listesi (yalnız süper admin)."""
    db = _db()
    st = await db.settings.find_one({"id": STATE_ID}, {"_id": 0}) or {}
    return {"state": st, "quarantine": await quarantine_items(db)}


@router.post("/run")
async def post_storefront_integrity_run(current_user: dict = Depends(require_super_admin)):
    """İzleyiciyi hemen bir kez çalıştır."""
    return await run_storefront_integrity_check()


async def _resolve_quarantine_targets(db, payload: dict) -> list:
    """İstekten karantinaya alınacak (sha256, kind, label) listesi."""
    out = []
    sha = str(payload.get("sha256") or "").strip().lower()
    if sha:
        if not _SHA_RE.match(sha):
            raise HTTPException(status_code=400, detail="sha256 64 karakterlik onaltılık özet olmalı")
        st = await db.settings.find_one({"id": STATE_ID}, {"_id": 0}) or {}
        kind, label = "unknown", sha[:16]
        if sha == st.get("release_fp") or any(r.get("fp") == sha for r in st.get("releases") or []):
            kind = "release"
        elif sha in (st.get("inline_hashes") or []):
            kind = "inline"
        else:
            b = next((b for b in st.get("bundles") or [] if sha in (b.get("sha256"), b.get("changed_sha256"))), None)
            if b:
                kind, label = "bundle", b["path"]
        out.append((sha, kind, label))
    bpath = str(payload.get("bundle_path") or "").strip()
    if bpath:
        st = await db.settings.find_one({"id": STATE_ID}, {"_id": 0, "bundles": 1}) or {}
        b = next((b for b in st.get("bundles") or [] if b.get("path") == bpath), None)
        if not b:
            raise HTTPException(status_code=404, detail="Bu paket izleyicide kayıtlı değil")
        out.append((b.get("changed_sha256") or b["sha256"], "bundle", bpath))
    hid = str(payload.get("custom_theme_history_id") or "").strip()
    if hid:
        h = await db.custom_theme_history.find_one({"id": hid}, {"_id": 0})
        if not h:
            raise HTTPException(status_code=404, detail="Özel tema sürümü bulunamadı")
        for field, kind in (("custom_js", "custom_js"), ("custom_css", "custom_css")):
            if h.get(field):
                out.append((_sha(str(h[field])), kind, f"custom_theme_history:{hid}"))
    pid = str(payload.get("pixel_history_id") or "").strip()
    if pid:
        h = await db.marketing_pixel_history.find_one({"id": pid}, {"_id": 0})
        if not h:
            raise HTTPException(status_code=404, detail="Piksel sürümü bulunamadı")
        snap = h.get("snapshot") if isinstance(h.get("snapshot"), dict) else h
        for field, kind in (("head_snippet", "pixel_head"), ("body_snippet", "pixel_body")):
            if snap.get(field):
                out.append((_sha(str(snap[field])), kind, f"pixel_history:{pid}"))
    if not out:
        raise HTTPException(status_code=400, detail="sha256, bundle_path, custom_theme_history_id ya da "
                                                    "pixel_history_id verin")
    return out


@router.post("/quarantine")
async def add_quarantine(payload: dict, request: Request, current_user: dict = Depends(require_super_admin)):
    """Bir parmak izini 'kötü/karantina' olarak işaretle (fail-closed denetimli)."""
    from activity_audit import run_audited
    db = _db()
    reason = str((payload or {}).get("reason") or "").strip()
    if len(reason) < 5:
        raise HTTPException(status_code=400, detail="Karantina gerekçesi zorunlu (en az 5 karakter)")
    targets = await _resolve_quarantine_targets(db, payload or {})
    by = str(current_user.get("email") or current_user.get("id") or "")
    now = _now()

    async def _perform():
        await db.settings.update_one({"id": QUARANTINE_ID},
                                     {"$setOnInsert": {"id": QUARANTINE_ID, "items": []}}, upsert=True)
        added = []
        for sha, kind, label in targets:
            r = await db.settings.update_one(
                {"id": QUARANTINE_ID, "items.sha256": {"$ne": sha}},
                {"$push": {"items": {"sha256": sha, "kind": kind, "label": label[:200], "reason": reason[:300],
                                     "by": by, "at": now}}})
            if getattr(r, "modified_count", 0):
                added.append(sha)
        return added

    added = await run_audited(
        db, action="storefront.quarantine_add", entity_type="storefront_quarantine",
        entity_id=",".join(t[0][:16] for t in targets)[:160], perform=_perform,
        after={"targets": [{"sha256": t[0][:16], "kind": t[1], "label": t[2][:80]} for t in targets],
               "reason_len": len(reason)},
        current_user=current_user, request=request)
    st = await db.settings.find_one({"id": STATE_ID}, {"_id": 0, "release_fp": 1, "inline_hashes": 1,
                                                       "bundles": 1, "entry_paths": 1}) or {}
    served_now = {st.get("release_fp")} | set(st.get("inline_hashes") or []) | {
        b.get("sha256") for b in st.get("bundles") or [] if b.get("path") in set(st.get("entry_paths") or [])}
    currently_served = [t[0] for t in targets if t[0] in served_now]
    try:
        from security.events import security_event
        await security_event("storefront_quarantine_changed", current_user,
                             {"islem": "eklendi", "adet": len(added), "tur": ", ".join(sorted({t[1] for t in targets})),
                              "simdi_sunuluyor": bool(currently_served), "fp": (added or [targets[0][0]])[0][:16]},
                             request=request)
    except Exception:  # noqa: BLE001
        pass
    return {"success": True, "added": added, "targets": [{"sha256": t[0], "kind": t[1], "label": t[2]} for t in targets],
            "currently_served": currently_served}


@router.delete("/quarantine/{sha256}")
async def remove_quarantine(sha256: str, request: Request, current_user: dict = Depends(require_super_admin)):
    """Karantinadan çıkar (fail-closed denetimli)."""
    from activity_audit import run_audited
    db = _db()
    sha = str(sha256 or "").strip().lower()
    if not _SHA_RE.match(sha):
        raise HTTPException(status_code=400, detail="Geçersiz sha256")
    if sha not in await quarantined_set(db):
        raise HTTPException(status_code=404, detail="Bu parmak izi karantinada değil")

    async def _perform():
        await db.settings.update_one({"id": QUARANTINE_ID}, {"$pull": {"items": {"sha256": sha}}})
        return True

    await run_audited(db, action="storefront.quarantine_remove", entity_type="storefront_quarantine",
                      entity_id=sha[:16], perform=_perform, current_user=current_user, request=request)
    try:
        from security.events import security_event
        await security_event("storefront_quarantine_changed", current_user,
                             {"islem": "kaldırıldı", "sha256": sha[:16], "fp": sha[:16]}, request=request)
    except Exception:  # noqa: BLE001
        pass
    return {"success": True}


@router.post("/approve-build")
async def approve_build(payload: dict, request: Request, current_user: dict = Depends(require_super_admin)):
    """Şu an sunulan, backend commit'iyle eşleşmeyen build'i (sha + sürüm parmak izi) gerekçeyle
    onayla — ör. backend'in yeniden deploy edilmediği yalnız-frontend değişikliği. Fail-closed
    denetimli; onay yalnız O (sha, sürüm) çiftine geçerlidir: aynı sha farklı içerikle gelirse yine
    kritik uyarı üretir."""
    from activity_audit import run_audited
    db = _db()
    reason = str((payload or {}).get("reason") or "").strip()
    if len(reason) < 5:
        raise HTTPException(status_code=400, detail="Onay gerekçesi zorunlu (en az 5 karakter)")
    sha = norm_git_sha((payload or {}).get("sha"))
    if not sha:
        raise HTTPException(status_code=400, detail="sha geçerli bir git commit'i olmalı (7-40 onaltılık)")
    st = await db.settings.find_one({"id": STATE_ID}, {"_id": 0, "build": 1, "release_fp": 1}) or {}
    served = (st.get("build") or {}).get("sha") or ""
    if not git_sha_eq(sha, served):
        raise HTTPException(status_code=409, detail="Yalnız vitrinde ŞU AN sunulan build onaylanabilir "
                                                    f"(sunulan: {served[:12] or 'kimlik yok'}).")
    release_fp = str(st.get("release_fp") or "")
    by = str(current_user.get("email") or current_user.get("id") or "")
    item = {"sha": served, "release_fp": release_fp, "reason": reason[:300], "by": by, "at": _now()}

    async def _perform():
        await db.settings.update_one({"id": APPROVAL_ID}, {"$setOnInsert": {"id": APPROVAL_ID, "items": []}},
                                     upsert=True)
        await db.settings.update_one({"id": APPROVAL_ID},
                                     {"$push": {"items": {"$each": [item], "$slice": -BUILDS_KEEP}}})
        return True

    await run_audited(db, action="storefront.build_approve", entity_type="storefront_build",
                      entity_id=served[:12], perform=_perform,
                      after={"sha": served[:12], "surum": release_fp[:16], "reason_len": len(reason)},
                      current_user=current_user, request=request)
    try:
        from security.events import security_event
        await security_event("storefront_build_approved", current_user,
                             {"build": served[:12], "surum": release_fp[:16], "fp": f"{served[:12]}:{release_fp[:8]}"},
                             request=request)
    except Exception:  # noqa: BLE001
        pass
    return {"success": True, "approved": {"sha": served, "release_fp": release_fp}}
