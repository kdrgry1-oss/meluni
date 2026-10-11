"""Canlı, salt-okunur kısa yük ölçümü (T18/SHOP02) — kullanıcı onaylı. Sipariş/ödeme YOK."""
import asyncio, time, statistics, json, httpx, random
B = "https://api.meluni.com.tr/api"
HEAVY = ["/products?search=elbise&limit=48", "/products?limit=96&sort=price_asc", "/products?search=pantolon&limit=48",
         "/products?limit=48&sort=newest", "/categories", "/page-blocks"]
CONC, DUR = 15, 90

def pct(xs, p):
    xs = sorted(xs); return xs[min(len(xs)-1, int(round(p/100*(len(xs)-1))))] if xs else float("nan")

async def checkout_probe(c, item, n, out, gap=1.0):
    body = {"cart_total": item["price"], "items": [{"product_id": item["id"], "category_id": item.get("category_id"),
            "price": item["price"], "qty": 1}], "user_id": None, "email": "yuk-testi@example.test"}
    for _ in range(n):
        t = time.perf_counter()
        try:
            r1 = await c.post(f"{B}/coupons/evaluate", json=body)
            r2 = await c.get(f"{B}/admin/rules/shipping/resolve", params={"cart_total": item["price"]})
            out.append(((time.perf_counter() - t) * 1000, r1.status_code, r2.status_code))
        except Exception as e:
            out.append((float("nan"), type(e).__name__, ""))
        await asyncio.sleep(gap)

async def heavy_worker(c, stop, stats):
    while time.time() < stop:
        u = random.choice(HEAVY)
        t = time.perf_counter()
        try:
            r = await c.get(B + u); stats.append(((time.perf_counter() - t) * 1000, r.status_code))
        except Exception as e:
            stats.append((float("nan"), type(e).__name__))

async def main():
    async with httpx.AsyncClient(timeout=30, headers={"user-agent": "meluni-guvenlik-yuk-olcumu/1"}) as c:
        p = (await c.get(f"{B}/products", params={"limit": 1})).json()
        prod = (p.get("products") or p.get("items") or p)[0] if not isinstance(p, list) else p[0]
        item = {"id": prod["id"], "price": float(prod.get("sale_price") or prod.get("price") or 100),
                "category_id": prod.get("category_id")}
        base = []; await checkout_probe(c, item, 20, base, gap=0.5)
        stop = time.time() + DUR; hs = []; under = []
        workers = [asyncio.create_task(heavy_worker(c, stop, hs)) for _ in range(CONC)]
        await checkout_probe(c, item, int(DUR) - 5, under, gap=1.0)
        await asyncio.gather(*workers)
        after = []; await checkout_probe(c, item, 10, after, gap=0.5)
    def summ(xs):
        ms = [x[0] for x in xs if x[0] == x[0]]
        codes = {}
        for x in xs: codes[f"{x[1]}/{x[2]}"] = codes.get(f"{x[1]}/{x[2]}", 0) + 1
        return {"n": len(xs), "p50_ms": round(pct(ms, 50)), "p95_ms": round(pct(ms, 95)), "max_ms": round(max(ms)) if ms else None, "codes": codes}
    hm = [x[0] for x in hs if x[0] == x[0]]; hc = {}
    for x in hs: hc[str(x[1])] = hc.get(str(x[1]), 0) + 1
    res = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "concurrency": CONC, "duration_s": DUR,
           "checkout_baseline": summ(base), "checkout_under_load": summ(under), "checkout_after": summ(after),
           "heavy": {"n": len(hs), "rps": round(len(hs) / DUR, 1), "p50_ms": round(pct(hm, 50)), "p95_ms": round(pct(hm, 95)), "codes": hc}}
    print(json.dumps(res, ensure_ascii=False, indent=1))
    json.dump(res, open("live_load_result.json", "w"), ensure_ascii=False, indent=1)

asyncio.run(main())
