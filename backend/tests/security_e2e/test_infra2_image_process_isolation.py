"""ADM-07 — image decode/convert runs in a SEPARATE PROCESS (image_worker.py).

* a worker that dies natively (os._exit / os.abort — what a Pillow segfault or the OOM killer looks
  like) does NOT kill the API process: the caller gets ImageWorkerCrashed, the pool is rebuilt and
  the next conversion works in a NEW child process;
* the upload endpoint maps such a crash to a clean 422 and the API (and checkout) keeps serving;
* a timed-out job's child process is KILLED (the work does not keep running in the background);
* existing limits are unchanged (pixel cap from the header, size cap, timeout) and return types of
  the converters are the same as before;
* if a process cannot be created (platform / sandbox) or IMAGE_PROCESS_ISOLATION=0, conversion falls
  back cleanly to the bounded thread pool and the self-test reports it.
"""
from __future__ import annotations

import asyncio
import io
import os
import time

import pytest
from PIL import Image

import _img_crash_helpers as crash
from conftest import KvUsers, kv_run
from kv_harness import kv_count, kv_r2  # noqa: F401

IMG = "/api/upload/image"


def _png(w, h, mode="RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, (w, h), (200, 30, 30) if mode == "RGB" else 0).save(buf, format="PNG")
    return buf.getvalue()


def _dead(pid: int, wait: float = 5.0) -> bool:
    """Linux: process gone or zombie (killed, not yet reaped by the executor)."""
    end = time.monotonic() + wait
    while time.monotonic() < end:
        try:
            with open(f"/proc/{pid}/stat") as f:
                if f.read().split(") ", 1)[1].split()[0] in ("Z", "X"):
                    return True
        except (FileNotFoundError, ProcessLookupError, IndexError):
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def iw(monkeypatch):
    import image_worker
    monkeypatch.delenv("IMAGE_PROCESS_ISOLATION", raising=False)
    image_worker._STATE["mode"] = "process"
    yield image_worker
    image_worker._STATE["mode"] = "process"
    image_worker._STATE["last_error"] = ""


@pytest.mark.parametrize("killer", [crash.hard_exit, crash.hard_abort])
def test_crashing_worker_does_not_kill_parent_and_next_conversion_works(iw, killer):
    parent = os.getpid()

    async def _scenario():
        pid_before = await iw.run_image_job_isolated(crash.child_pid)
        assert pid_before != parent
        crashes = iw.isolation_status()["crashes"]
        with pytest.raises(iw.ImageWorkerCrashed):
            await iw.run_image_job_isolated(killer)
        assert iw.isolation_status()["crashes"] == crashes + 1
        # the parent is alive and its event loop still works
        assert os.getpid() == parent
        await asyncio.sleep(0)
        # next conversion: real Pillow work, in a fresh child, same return type as before
        out = await iw.run_image_job_isolated(iw.resize_webp, _png(800, 600), 320, 80)
        assert isinstance(out, bytes) and Image.open(io.BytesIO(out)).size == (320, 240)
        pid_after = iw.isolation_status()["last_pid"]
        assert pid_after not in (0, parent)
        # several crashes in a row never exhaust the pool
        for _ in range(3):
            with pytest.raises(iw.ImageWorkerCrashed):
                await iw.run_image_job_isolated(killer)
        assert await iw.run_image_job_isolated(crash.child_pid) != parent
    asyncio.run(_scenario())


def test_upload_crash_returns_clean_422_and_api_keeps_serving(kv_client, kv_users, kv_r2, iw, monkeypatch):
    from types import SimpleNamespace
    from routes import upload as up
    h = KvUsers.hdr(kv_users.staff(super_admin=True))
    # the converter process dies natively while handling this upload
    monkeypatch.setattr(up, "_iw", SimpleNamespace(optimize_for_upload=crash.hard_exit_any))
    r = kv_client.post(IMG, headers=h, files={"file": ("x.png", _png(64, 64), "image/png")})
    assert r.status_code == 422, r.text
    assert "çökertti" in r.json()["detail"]
    assert kv_count("files") == 0 and not kv_r2.bucket("kv-public")
    # same process keeps serving: a real upload right after works (fresh child)
    monkeypatch.setattr(up, "_iw", iw)
    r = kv_client.post(IMG, headers=h, files={"file": ("y.png", _png(64, 64), "image/png")})
    assert r.status_code == 200, r.text
    assert r.json()["url"].endswith(".webp")
    assert kv_client.get("/api/health").status_code == 200          # app still serving


def test_timeout_kills_the_child_process(iw, tmp_path):
    async def _scenario():
        t0 = time.monotonic()
        with pytest.raises(iw.ImageTaskTimeout):
            await iw.run_image_job_isolated(crash.write_pid_then_sleep, str(tmp_path / "pid"), 30, timeout=1.5)
        assert time.monotonic() - t0 < 3.0
        pid = int((tmp_path / "pid").read_text())
        assert pid != os.getpid()
        assert _dead(pid), "timed-out conversion kept running in the background"
        # pool is rebuilt for the next job
        assert await iw.run_image_job_isolated(crash.child_pid) not in (pid, os.getpid())
    killed = iw.isolation_status()["timeouts_killed"]
    asyncio.run(_scenario())
    assert iw.isolation_status()["timeouts_killed"] >= killed + 1


def test_child_memory_is_capped_and_child_is_preferred_oom_victim(iw, monkeypatch):
    """OOM in a conversion → MemoryError in the CHILD (not a container-wide OOM); the kernel OOM killer
    prefers the child (oom_score_adj=1000) over the API process; a real 36 MP image still fits."""
    monkeypatch.setattr(iw, "_TLS", type(iw._TLS)())           # fresh executors with the test cap
    monkeypatch.setenv("IMAGE_WORKER_MAX_MEM_MB", "512")

    async def _scenario():
        cap, adj = await iw.run_image_job_isolated(crash.child_limits)
        assert cap == 512 * 1024 * 1024 and adj == 1000
        with open("/proc/self/oom_score_adj") as f:
            assert int(f.read().strip()) < 1000                  # the API process is NOT marked
        with pytest.raises(MemoryError):
            await iw.run_image_job_isolated(crash.allocate_mb, 2048)
        assert await iw.run_image_job_isolated(crash.allocate_mb, 16) == 16 * 1024 * 1024
    asyncio.run(_scenario())
    # default cap (1536 MB) leaves room for the largest allowed image (40 MP pixel cap)
    monkeypatch.setattr(iw, "_TLS", type(iw._TLS)())
    monkeypatch.delenv("IMAGE_WORKER_MAX_MEM_MB")
    buf = io.BytesIO()
    Image.new("RGB", (6000, 6000), (90, 120, 150)).save(buf, format="JPEG", quality=85)

    async def _big():
        cap, _ = await iw.run_image_job_isolated(crash.child_limits)
        assert cap == iw.DEFAULT_CHILD_MAX_MEM_MB * 1024 * 1024
        data, mime, _ext = await iw.run_image_job_isolated(iw.optimize_for_upload, buf.getvalue(), 2000, 88)
        assert mime == "image/webp" and Image.open(io.BytesIO(data)).size == (2000, 2000)
    asyncio.run(_big())


def test_limits_and_return_types_unchanged_in_child(iw):
    async def _scenario():
        # pixel cap is enforced from the header inside the child (decompression bomb never decoded)
        bomb = _png(8000, 6000, "L")
        t0 = time.monotonic()
        with pytest.raises(iw.InvalidImage) as ei:
            await iw.run_image_job_isolated(iw.optimize_for_upload, bomb, 2000, 88)
        assert "piksel tavanı" in str(ei.value) and time.monotonic() - t0 < 3.0
        with pytest.raises(iw.ImageTooLarge):
            await iw.run_image_job_isolated(iw.to_jpeg, bomb, 2000)
        data, mime, ext = await iw.run_image_job_isolated(iw.optimize_for_upload, _png(3000, 1000), 2000, 88)
        assert (mime, ext) == ("image/webp", "webp") and Image.open(io.BytesIO(data)).size == (2000, 666)
        jpg = await iw.run_image_job_isolated(iw.to_jpeg, _png(300, 300), 2000)
        assert jpg[:3] == b"\xff\xd8\xff"
        assert await iw.run_image_job_isolated(iw.normalize_rgb_jpeg, b"not an image") is None
        from xlsx_images import fit_canvas_isolated
        cell = await fit_canvas_isolated(_png(500, 500), "medium")
        assert Image.open(io.BytesIO(cell)).size == (132, 176)
    asyncio.run(_scenario())


def test_fallback_to_threads_when_processes_unavailable_and_selftest_reports(iw, monkeypatch):
    from routes.deps import db
    from security import selftest

    def _no_proc():
        raise OSError("sem_open unavailable (test)")
    monkeypatch.setattr(iw, "_TLS", type(iw._TLS)())           # fresh per-thread executors
    monkeypatch.setattr(iw, "_new_executor", _no_proc)
    out = kv_run(iw.run_image_job_isolated(iw.resize_webp, _png(400, 400), 160, 80))
    assert Image.open(io.BytesIO(out)).size == (160, 160)
    st = iw.isolation_status()
    assert st["mode"] == "thread-fallback" and "sem_open" in st["last_error"]
    status, detail = kv_run(selftest._c_image_isolation(db))
    assert status == "fail" and "iş parçacığına" in detail
    # explicit opt-out via env → warn, still converts
    iw._STATE["mode"] = "process"
    monkeypatch.setenv("IMAGE_PROCESS_ISOLATION", "0")
    out = kv_run(iw.run_image_job_isolated(iw.resize_webp, _png(400, 400), 160, 80))
    assert out and kv_run(selftest._c_image_isolation(db))[0] == "warn"
    monkeypatch.delenv("IMAGE_PROCESS_ISOLATION")
    assert kv_run(selftest._c_image_isolation(db))[0] == "ok"
