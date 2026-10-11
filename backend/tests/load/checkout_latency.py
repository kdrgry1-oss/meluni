#!/usr/bin/env python3
"""Checkout latency under expensive public load (T18 / SHOP02) — LOCAL / STAGING tool, NOT run in CI.

Fires N concurrent "expensive" public requests (on-the-fly image resize, storefront search/list,
merchant XML feed) in waves and, while they are in flight, measures the latency of a checkout
request. Prints p50/p95/max for the checkout with and without background load.

Never point this at production: by default only localhost is allowed; another host needs
--allow-host explicitly. Creating real orders is OFF unless --create-orders is given together with
--product-id of a dedicated test product (orders created this way must be deleted afterwards and the
stock restored — see CLAUDE.md "canlı test").

Usage:
    python backend/tests/load/checkout_latency.py --base-url http://localhost:8001 \
        --image-path big.png --concurrency 20 --rounds 5
    python backend/tests/load/checkout_latency.py --base-url http://localhost:8001 \
        --create-orders --product-id TEST-123 --rounds 10
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from urllib.parse import urlsplit

import httpx

DEFAULT_EXPENSIVE = [
    "/api/products?search=elbise&limit=48",
    "/api/products?limit=96&sort=price_asc",
    "/api/products/google-merchant-feed.xml",
]


def _pct(xs, p):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round(p / 100.0 * (len(xs) - 1)))))
    return xs[k]


async def _checkout(client: httpx.AsyncClient, args) -> float:
    t = time.perf_counter()
    if args.create_orders:
        r = await client.post("/api/orders", json={
            "items": [{"product_id": args.product_id, "quantity": 1, "price": 1}],
            "payment_method": "credit_card",
            "shipping_address": {"first_name": "Load", "last_name": "Test", "email": "load-test@example.invalid",
                                 "phone": "05550000000", "city": "Istanbul", "district": "Test",
                                 "address": "Load test", "postal_code": "34000"}})
    else:
        # read-only checkout-adjacent call (cart pricing) when real order creation is not allowed
        r = await client.post("/api/products/cart-pricing", json={"items": []})
    _ = r.status_code
    return time.perf_counter() - t


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--allow-host", action="append", default=[])
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--image-path", action="append", default=[], help="stored upload path, e.g. <uuid>.png")
    ap.add_argument("--create-orders", action="store_true")
    ap.add_argument("--product-id", default="")
    ap.add_argument("--budget", type=float, default=2.0, help="p95 checkout budget under load (s)")
    args = ap.parse_args()

    host = urlsplit(args.base_url).hostname or ""
    if host not in ("localhost", "127.0.0.1", "::1") and host not in args.allow_host:
        print(f"refusing to load-test {host!r}; pass --allow-host {host} for a staging host", file=sys.stderr)
        return 2
    if args.create_orders and not args.product_id:
        print("--create-orders needs --product-id of a dedicated test product", file=sys.stderr)
        return 2

    expensive = list(DEFAULT_EXPENSIVE)
    for p in args.image_path:
        expensive += [f"/api/upload/files/{p}?w={w}" for w in (320, 480, 640, 800, 1080)]

    async with httpx.AsyncClient(base_url=args.base_url, timeout=60) as client:
        idle = [await _checkout(client, args) for _ in range(args.rounds)]
        loaded = []
        for _ in range(args.rounds):
            burst = [client.get(expensive[i % len(expensive)]) for i in range(args.concurrency)]

            async def _delayed():
                await asyncio.sleep(0.05)
                return await _checkout(client, args)
            res = await asyncio.gather(*burst, _delayed(), return_exceptions=True)
            if isinstance(res[-1], float):
                loaded.append(res[-1])
    for name, xs in (("idle", idle), ("under load", loaded)):
        print(f"checkout {name:>10}: n={len(xs)} p50={statistics.median(xs) if xs else float('nan'):.3f}s "
              f"p95={_pct(xs, 95):.3f}s max={max(xs) if xs else float('nan'):.3f}s")
    ok = bool(loaded) and _pct(loaded, 95) <= args.budget
    print("RESULT:", "OK" if ok else f"OVER BUDGET ({args.budget}s)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
