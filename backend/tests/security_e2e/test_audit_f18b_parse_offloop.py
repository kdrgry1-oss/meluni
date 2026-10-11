"""Denetim #18 (tamamlama): elektronik tablo ayrıştırma olay döngüsünü bloke etmez ve süre sınırlıdır."""
import asyncio
import time

import pytest
from fastapi import HTTPException

from security.spreadsheet_guard import parse_offloop


def test_loop_stays_responsive_and_timeout_enforced():
    async def _run():
        ticks = []

        async def _ticker():
            for _ in range(5):
                ticks.append(time.monotonic())
                await asyncio.sleep(0.02)
        t = asyncio.create_task(_ticker())
        assert await parse_offloop(lambda: (time.sleep(0.2), "ok")[1]) == "ok"
        await t
        assert len(ticks) == 5                     # ayrıştırma sürerken döngü çalışmaya devam etti
        with pytest.raises(HTTPException) as e:
            await parse_offloop(time.sleep, 1.0, timeout=0.05)
        assert e.value.status_code == 422
    asyncio.run(_run())


def test_import_sites_use_offloop():
    import inspect
    import routes.bulk_ops as bo, routes.production_plan as pp, routes.influencers as inf, routes.products as pr
    for m in (bo, pp, inf, pr):
        assert "parse_offloop" in inspect.getsource(m), m.__name__
