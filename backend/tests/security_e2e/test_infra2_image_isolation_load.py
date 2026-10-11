"""ADM-07 / T18 / SHOP02 — image conversion isolation and checkout latency under load.

* ADM-07: image decoding happens in a bounded worker pool with a per-task timeout (and, since the
  process-isolation change, in a separate child process — see test_infra2_image_process_isolation.py);
  the pixel cap is checked from the HEADER before any decode, so a decompression-bomb PNG is rejected
  quickly and never decoded; concurrency of conversions is bounded by the pool size.
* T18 / SHOP02: 20 concurrent expensive public requests (on-the-fly image resize, storefront
  product search/list, merchant XML feed) must not push a concurrent POST /api/orders over a
  (generous) latency budget, and the event loop must stay responsive.
"""
from __future__ import annotations

import asyncio
import base64
import io
import threading
import time

import pytest
from PIL import Image

from conftest import KvUsers
from kv_harness import kv_count, kv_r2, kv_mongomock_convert  # noqa: F401
from pay_harness import pay_create_order, pay_seed_product, pay_drain
from routes import deps

IMG = "/api/upload/image"


def _png(w, h, mode="L") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, (w, h)).save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def _staff(kv_users):
    return KvUsers.hdr(kv_users.staff(super_admin=True))


# ═══════════════════════════════════════ ADM-07 ═══════════════════════════════════════
@pytest.mark.parametrize("w,h,mode", [
    (8000, 6000, "L"),      # 48 MP: above our 40 MP cap, below PIL's own 2× bomb error → OUR header check
    (12000, 12000, "1"),    # 144 MP: PIL DecompressionBombError at open
])
def test_decompression_bomb_png_rejected_quickly_without_decode(kv_client, kv_users, kv_r2, monkeypatch, w, h, mode):
    from PIL import ImageFile
    bomb = _png(w, h, mode)
    assert len(bomb) < 2 * 1024 * 1024       # tiny on the wire, huge when decoded
    decoded = {"n": 0}
    real_load = ImageFile.ImageFile.load

    def _spy_load(self, *a, **k):
        decoded["n"] += 1
        return real_load(self, *a, **k)
    monkeypatch.setattr(ImageFile.ImageFile, "load", _spy_load)
    t0 = time.monotonic()
    r = kv_client.post(IMG, headers=_staff(kv_users), files={"file": ("bomb.png", bomb, "image/png")})
    elapsed = time.monotonic() - t0
    assert r.status_code == 400, r.text
    assert elapsed < 2.0, elapsed
    assert decoded["n"] == 0                  # rejected from the header — never decoded
    assert not kv_r2.bucket("kv-public") and kv_count("files") == 0


def test_pixel_guard_unit_and_timeout_and_bounded_pool():
    from routes import upload as up
    with pytest.raises(up.ImageTooLarge):
        up.open_image_guarded(_png(8000, 6000))
    im = up.open_image_guarded(_png(100, 100))
    assert im.size == (100, 100)

    async def _scenario():
        # per-task timeout: caller gets ImageTaskTimeout fast even if the job is slow
        t0 = time.monotonic()
        with pytest.raises(up.ImageTaskTimeout):
            await up.run_image_job(time.sleep, 0.6, timeout=0.05)
        assert time.monotonic() - t0 < 0.5
        await asyncio.sleep(0.7)             # let the orphaned job finish
        # bounded concurrency: never more than IMAGE_WORKERS conversions at once
        state = {"cur": 0, "max": 0, "names": set()}
        lock = threading.Lock()

        def _job():
            with lock:
                state["cur"] += 1
                state["max"] = max(state["max"], state["cur"])
                state["names"].add(threading.current_thread().name.split("_")[0])
            time.sleep(0.05)
            with lock:
                state["cur"] -= 1
            return True
        res = await asyncio.gather(*[up.run_image_job(_job) for _ in range(8)])
        assert all(res)
        assert state["max"] <= up.IMAGE_WORKERS
        assert state["names"] == {"img-convert"}
    asyncio.run(_scenario())


def test_resize_on_the_fly_runs_in_image_pool(kv_client, monkeypatch):
    from routes import upload as up
    from routes.deps import db
    from conftest import kv_run
    kv_run(db.files.insert_one({"id": "f1", "storage_path": "pic.png", "content_type": "image/png",
                                "data_b64": base64.b64encode(_png(1200, 800, "RGB")).decode(),
                                "is_deleted": False}))
    import os
    import image_worker
    seen = []
    real = up.run_image_job_isolated

    async def _spy(fn, *a, **k):
        seen.append(fn)
        return await real(fn, *a, **k)
    monkeypatch.setattr(up, "run_image_job_isolated", _spy)
    r = kv_client.get("/api/upload/files/pic.png?w=480")
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    assert Image.open(io.BytesIO(r.content)).size[0] == 480
    # ADM-07: the resize is dispatched to the bounded pool AND decoded in a separate process
    assert seen == [image_worker.resize_webp]
    st = image_worker.isolation_status()
    assert st["mode"] == "process" and st["last_pid"] and st["last_pid"] != os.getpid()


# ═══════════════════════════════════════ T18 / SHOP02 ═══════════════════════════════════════
def _gradient_png(w=2400, h=1600) -> bytes:
    im = Image.linear_gradient("L").resize((w, h)).convert("RGB")
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_concurrent_expensive_public_requests_do_not_stall_checkout(pay_http, pay_env, kv_mongomock_convert):
    await pay_seed_product("p1", stock=50)
    await deps.db.products.insert_many([
        {"id": f"bulk{i}", "name": f"Elbise Model {i}", "slug": f"elbise-{i}", "price": 100.0 + i, "stock": 3,
         "is_active": True, "variants": [], "images": [f"https://cdn.kv-test.invalid/{i}.jpg"],
         "category_id": "cat1", "created_at": "2026-09-01T00:00:00+00:00"} for i in range(200)])
    big = _gradient_png()
    for i in range(4):
        await deps.db.files.insert_one({"id": f"img{i}", "storage_path": f"big{i}.png", "content_type": "image/png",
                                        "data_b64": base64.b64encode(big).decode(), "is_deleted": False})

    # warm-up + baseline latency of a lone checkout
    await pay_create_order(pay_http)
    t0 = time.perf_counter()
    r = await pay_create_order(pay_http)
    baseline = time.perf_counter() - t0
    assert r.status_code == 200, r.text

    expensive = ([f"/api/upload/files/big{i % 4}.png?w={(320, 480, 640, 800, 1080)[i % 5]}" for i in range(10)]
                 + ["/api/products?search=elbise&limit=48", "/api/products?limit=96&sort=price_asc"] * 4
                 + ["/api/products/google-merchant-feed.xml"] * 2)
    assert len(expensive) == 20

    lag = {"max": 0.0, "stop": False}

    async def _heartbeat():
        while not lag["stop"]:
            t = time.perf_counter()
            await asyncio.sleep(0.01)
            lag["max"] = max(lag["max"], time.perf_counter() - t - 0.01)

    async def _order_after_start():
        await asyncio.sleep(0.02)            # the expensive burst is already in flight
        t = time.perf_counter()
        resp = await pay_http.post("/api/orders", json={
            "items": [{"product_id": "p1", "quantity": 1, "price": 1}], "payment_method": "credit_card",
            "shipping_address": {"first_name": "A", "last_name": "B", "email": "buyer@example.test",
                                 "phone": "05551234567", "city": "Istanbul", "district": "Kadikoy",
                                 "address": "Test Mah. 1", "postal_code": "34000"}})
        return resp, time.perf_counter() - t

    hb = asyncio.create_task(_heartbeat())
    t_load = time.perf_counter()
    results = await asyncio.gather(*[pay_http.get(u) for u in expensive], _order_after_start())
    load_total = time.perf_counter() - t_load
    lag["stop"] = True
    await hb
    await pay_drain()

    order_resp, order_latency = results[-1]
    assert order_resp.status_code == 200, order_resp.text
    assert all(x.status_code == 200 for x in results[:-1]), [(u, x.status_code) for u, x in zip(expensive, results)]
    budget = max(5.0, 10 * baseline)          # generous on purpose (CI noise); real target is far lower
    assert order_latency < budget, (order_latency, baseline, load_total)
    assert lag["max"] < 2.0, lag             # image work is off the event loop
