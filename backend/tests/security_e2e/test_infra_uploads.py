"""Uploads: content sniffing (never trust name/Content-Type), WebP re-encode, SVG refusal, ?w= bucketing."""
from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from kv_harness import kv_r2, kv_insert, kv_findall, kv_count  # noqa: F401
from conftest import KvUsers

IMG = "/api/upload/image"
VID = "/api/upload/video"


def _png(w=10, h=8, mode="RGB") -> bytes:
    b = io.BytesIO()
    Image.new(mode, (w, h), (200, 30, 30) if mode == "RGB" else (200, 30, 30, 120)).save(b, format="PNG")
    return b.getvalue()


def _staff(kv_users):
    return KvUsers.hdr(kv_users.staff(perms=["products.edit"]))


@pytest.mark.parametrize("name,ctype,data", [
    ("photo.jpg", "image/jpeg", b"this is plain text pretending to be a jpeg image, honest" * 3),
    ("photo.png", "image/png", b"<html><script>alert(1)</script></html>" * 4),
    ("x.jpg", "image/jpeg", b"\xff\xd8\xff" + b"\x00" * 64),           # JPEG magic but not decodable
    ("x.gif", "image/gif", b"GIF89a" + b"\x00" * 40),                   # GIF magic, garbage body
    ("x.exe", "application/octet-stream", b"MZ\x90\x00" + b"\x00" * 60),
])
def test_fake_images_rejected(kv_client, kv_users, kv_r2, name, ctype, data):
    r = kv_client.post(IMG, headers=_staff(kv_users), files={"file": (name, data, ctype)})
    assert r.status_code == 400, r.text
    assert not kv_r2.bucket("kv-public") and kv_count("files") == 0


@pytest.mark.parametrize("name,ctype", [("logo.svg", "image/svg+xml"), ("logo.png", "image/png"),
                                        ("logo.jpg", "image/jpeg")])
def test_svg_rejected_under_any_name(kv_client, kv_users, kv_r2, name, ctype):
    svg = (b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
           b'<script>alert(document.cookie)</script><rect width="10" height="10"/></svg>')
    r = kv_client.post(IMG, headers=_staff(kv_users), files={"file": (name, svg, ctype)})
    assert r.status_code == 400
    assert not kv_r2.bucket("kv-public") and kv_count("files") == 0


def test_real_png_stored_as_webp_in_r2(kv_client, kv_users, kv_r2):
    r = kv_client.post(IMG, headers=_staff(kv_users), files={"file": ("evil.html", _png(), "text/html")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["path"].endswith(".webp") and body["url"].startswith("https://cdn.kv-test.invalid/uploads/")
    objs = kv_r2.bucket("kv-public")
    assert len(objs) == 1
    key, (data, ct) = next(iter(objs.items()))
    assert key.startswith("uploads/") and key.endswith(".webp") and ct == "image/webp"
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    assert Image.open(io.BytesIO(data)).size == (10, 8)
    rec = kv_findall("files")[0]
    assert rec["content_type"] == "image/webp" and "data_b64" not in rec  # bytes are not in Mongo
    assert not kv_r2.bucket("kv-archive")


def test_huge_image_is_downscaled(kv_client, kv_users, kv_r2):
    r = kv_client.post(IMG, headers=_staff(kv_users), files={"file": ("big.png", _png(3000, 1500), "image/png")})
    assert r.status_code == 200
    data, _ = next(iter(kv_r2.bucket("kv-public").values()))
    assert Image.open(io.BytesIO(data)).size == (2000, 1000)


def test_upload_requires_staff(kv_client, kv_users, kv_r2):
    assert kv_client.post(IMG, files={"file": ("a.png", _png(), "image/png")}).status_code == 401
    cust = KvUsers.hdr(kv_users.customer())
    assert kv_client.post(IMG, headers=cust, files={"file": ("a.png", _png(), "image/png")}).status_code == 403
    assert kv_client.post(VID, headers=cust, files={"file": ("a.mp4", b"x" * 64, "video/mp4")}).status_code == 403
    assert not kv_r2.bucket("kv-public")


MP4_HEAD = (b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00\x00\x00\x08free" + b"\x00" * 64)


@pytest.mark.parametrize("name,ctype,data", [
    ("clip.mp4", "video/mp4", b"definitely not a video file, just text bytes........"),
    ("clip.mp4", "video/mp4", _png()),                          # an image, not a video
    ("clip.webm", "video/webm", b"\x1a\x45\xdf\xa3" + b"matroska" + b"\x00" * 60),   # EBML but not webm
    ("clip.mov", "video/quicktime", b"<svg xmlns='http://www.w3.org/2000/svg'><script>x</script></svg>"),
    ("clip.mp4", "video/mp4", b"\x00\x00\x00\x18ftypavif\x00\x00\x00\x00avifmif1" + b"\x00" * 40),  # AVIF still
])
def test_video_wrong_magic_rejected(kv_client, kv_users, kv_r2, name, ctype, data):
    r = kv_client.post(VID, headers=_staff(kv_users), files={"file": (name, data, ctype)})
    assert r.status_code == 400, r.text
    assert not kv_r2.bucket("kv-public")


def test_real_mp4_header_accepted_and_extension_from_content(kv_client, kv_users, kv_r2):
    r = kv_client.post(VID, headers=_staff(kv_users), files={"file": ("x.html", MP4_HEAD, "text/html")})
    assert r.status_code == 200, r.text
    key, (_data, ct) = next(iter(kv_r2.bucket("kv-public").items()))
    assert key.endswith(".mp4") and ct == "video/mp4"


# ───────────────────────────── ?w= bucketing ─────────────────────────────
@pytest.mark.parametrize("w,expected", [(1, 160), (160, 160), (161, 320), (1001, 1080), (1080, 1080),
                                        (1081, 1440), (5000, 1920), (0, 0), (-5, 0), ("abc", 0)])
def test_bucket_w(w, expected):
    from routes.upload import _bucket_w
    assert _bucket_w(w) == expected


def test_serve_resize_uses_bucketed_width(kv_client):
    kv_insert("files", {"id": "f1", "storage_path": "legacy.png", "content_type": "image/png",
                        "data_b64": base64.b64encode(_png(1500, 300)).decode(), "is_deleted": False})
    for w, exp in ((1001, 1080), (1002, 1080), (1079, 1080), (650, 800)):
        r = kv_client.get(f"/api/upload/files/legacy.png?w={w}")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/webp"
        assert Image.open(io.BytesIO(r.content)).size[0] == exp, w
    # identical bytes for different raw w values in the same bucket (cache-key stability)
    a = kv_client.get("/api/upload/files/legacy.png?w=1001").content
    b = kv_client.get("/api/files/legacy.png?w=1050").content
    assert a == b


def test_serve_path_traversal_blocked(kv_client):
    for p in ("../server.py", "..%2Fserver.py", "%2e%2e/%2e%2e/etc/passwd", "/etc/passwd"):
        r = kv_client.get(f"/api/upload/files/{p}")
        assert r.status_code == 404, p
