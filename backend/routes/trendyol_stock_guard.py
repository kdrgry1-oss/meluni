"""
trendyol_stock_guard.py — Pazaryerinde satılamayacak ürünün stoğunu sıfırlar.

NEDEN (canlıda yaşandı): Trendyol'da, bizde KARŞILIĞI OLMAYAN barkodlar satışta
kaldı ve sipariş geldi. Gönderecek mal yok → sipariş iptal, mağaza puanı düşüyor.

Mevcut stok senkronu bu boşluğu kapatamaz, çünkü o ÜRÜNLERİMİZİN üzerinden
döner: veritabanında karşılığı olmayan bir barkod hiçbir döngüye girmez, dolayısıyla
stoğu hiç sıfırlanmaz. Barkod nasıl öksüz kalır:

  • Stok kodu/barkod düzeni değişti, ürün YENİ barkodlarla yeniden gönderildi —
    eskiler Trendyol'da satışta kaldı (canlıda 12 barkod, 120 adet).
  • Beslemeden düşen beden üründen temizlendi ama Trendyol'da duruyor.
  • Ürün panelden kalıcı silindi; kayıt gitti, ilan kaldı.

Bu modül TERSİNDEN çalışır: önce TRENDYOL'un listesini alır, her barkodu bizde
arar ve satılamaz olanı sıfırlar. Sıfırlama ürün kaydı GEREKTİRMEZ — fiyat/stok
ucu barkodla çalışır, fiyatı da ilanın kendi fiyatından okuruz.

Karar tablosu (Trendyol'da stoğu > 0 olan her barkod için):
  bizde yok                        → SIFIRLA (öksüz)
  ürün silinmiş / pasif            → SIFIRLA
  varyantın bizdeki stoğu 0        → SIFIRLA
  stok farklı                      → bizdeki değere ÇEK (sürüklenme)
  aynı                             → dokunma

Varsayılan KURU ÇALIŞMA: `uygula=False` ile yalnız rapor üretir.
"""
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import APIRouter, Body, Depends

from .deps import db, logger, require_admin, generate_id

router = APIRouter(prefix="/integrations", tags=["Trendyol Stok Koruması"])

# Tek Trendyol isteğinde gönderilecek en fazla kalem (fiyat/stok ucu sınırı).
_PARCA = 100


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


async def _istemci():
    """Yapılandırılmış Trendyol istemcisi; entegrasyon kapalıysa None."""
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from .integrations_trendyol import get_trendyol_config
    from trendyol_client import TrendyolClient

    cfg = await get_trendyol_config()
    if not cfg.get("is_active"):
        return None, cfg
    return TrendyolClient(
        supplier_id=cfg["supplier_id"],
        api_key=cfg["api_key"],
        api_secret=cfg["api_secret"],
        mode=cfg["mode"],
    ), cfg


async def _trendyol_satirlari(cli, sayfa_limit: int = 60) -> List[Dict[str, Any]]:
    """Trendyol'daki TÜM ilanları (arşivlenmemiş) döndürür."""
    satirlar: List[Dict[str, Any]] = []
    sayfa = 0
    while sayfa < sayfa_limit:
        res = await cli.get_filtered_products(page=sayfa, size=200, archived=False)
        icerik = (res or {}).get("content") or []
        if not icerik:
            break
        satirlar.extend(icerik)
        toplam_sayfa = (res or {}).get("totalPages") or 0
        sayfa += 1
        if sayfa >= toplam_sayfa:
            break
    return satirlar


async def _bizdeki_barkodlar() -> Dict[str, Dict[str, Any]]:
    """Barkod → {stok, satilabilir} haritası.

    `satilabilir`: ürün silinmemiş VE aktif. Varyant düzeyinde stok ayrıca
    taşınır; ürün pasifse varyant stoğu ne olursa olsun satılamaz.
    """
    harita: Dict[str, Dict[str, Any]] = {}
    alanlar = {"_id": 0, "id": 1, "barcode": 1, "stock": 1, "is_active": 1,
               "is_deleted": 1, "variants.barcode": 1, "variants.stock": 1}
    async for p in db.products.find({}, alanlar):
        satilabilir = not p.get("is_deleted") and p.get("is_active") is not False
        varyantlar = p.get("variants") or []
        if varyantlar:
            for v in varyantlar:
                bc = str(v.get("barcode") or "").strip()
                if bc:
                    harita[bc] = {"stok": int(v.get("stock") or 0),
                                  "satilabilir": satilabilir, "urun": p["id"]}
        elif str(p.get("barcode") or "").strip():
            harita[str(p["barcode"]).strip()] = {
                "stok": int(p.get("stock") or 0),
                "satilabilir": satilabilir, "urun": p["id"]}
    return harita


def _fiyat(satir: Dict[str, Any]) -> Tuple[float, float]:
    """İlanın kendi fiyatı — fiyat/stok ucu fiyatsız kalem kabul etmiyor.

    Sıfırlarken fiyata DOKUNMUYORUZ: ilandaki değeri aynen geri yazıyoruz ki
    stok düzeltmesi yanlışlıkla fiyat değiştirmesin.
    """
    satis = satir.get("salePrice")
    liste = satir.get("listPrice")
    try:
        satis = float(satis)
    except (TypeError, ValueError):
        satis = 0.0
    try:
        liste = float(liste)
    except (TypeError, ValueError):
        liste = satis
    return round(satis, 2), round(liste or satis, 2)


async def trendyol_stok_denetimi(uygula: bool = False,
                                 arsivle: bool = False,
                                 sadece_oksuz: bool = False,
                                 zorla: bool = False) -> Dict[str, Any]:
    """Trendyol ilanlarını bizim katalogla karşılaştırır, satılamazları sıfırlar.

    uygula=False  → hiçbir şey yazılmaz, yalnız rapor.
    arsivle=True  → ÖKSÜZ barkodlar sıfırlandıktan sonra arşivlenir (ilan
                    satıştan kalkar, slot iade olur; geri alınabilir).
    sadece_oksuz  → yalnız bizde HİÇ karşılığı olmayanlara dokunur.
    zorla=True    → toplu-sıfırlama kilidini aşar. Yalnız raporu okuyup
                    gerçekten istendiğinde kullanılır; otomatik iş ASLA
                    zorlamaz.
    """
    cli, cfg = await _istemci()
    if cli is None:
        return {"success": False, "mesaj": "Trendyol entegrasyonu kapalı."}

    satirlar = await _trendyol_satirlari(cli)
    bizdeki = await _bizdeki_barkodlar()

    duzeltilecek: List[Dict[str, Any]] = []
    oksuz: List[str] = []
    sayac = {"trendyol_ilan": len(satirlar), "oksuz": 0, "pasif_urun": 0,
             "bizde_stok_yok": 0, "surukleme": 0, "dokunulmadi": 0}

    for s in satirlar:
        bc = str(s.get("barcode") or "").strip()
        if not bc:
            continue
        ty_adet = int(s.get("quantity") or 0)
        kayit = bizdeki.get(bc)
        satis, liste = _fiyat(s)

        if kayit is None:
            # Bizde HİÇ yok — sipariş gelirse gönderecek mal da yok.
            if ty_adet > 0:
                sayac["oksuz"] += 1
                oksuz.append(bc)
                duzeltilecek.append({"barcode": bc, "quantity": 0,
                                     "salePrice": satis, "listPrice": liste,
                                     "_sebep": "oksuz", "_eski": ty_adet,
                                     "_baslik": str(s.get("title") or "")[:60]})
            continue

        if sadece_oksuz:
            sayac["dokunulmadi"] += 1
            continue

        hedef = kayit["stok"] if kayit["satilabilir"] else 0
        if hedef == ty_adet:
            sayac["dokunulmadi"] += 1
            continue

        if not kayit["satilabilir"]:
            sayac["pasif_urun"] += 1
            sebep = "pasif_urun"
        elif hedef == 0:
            sayac["bizde_stok_yok"] += 1
            sebep = "bizde_stok_yok"
        else:
            sayac["surukleme"] += 1
            sebep = "surukleme"
        duzeltilecek.append({"barcode": bc, "quantity": hedef,
                             "salePrice": satis, "listPrice": liste,
                             "_sebep": sebep, "_eski": ty_adet,
                             "_baslik": str(s.get("title") or "")[:60]})

    rapor = {
        "success": True,
        "uygulandi": bool(uygula),
        "sayac": sayac,
        "duzeltilecek": len(duzeltilecek),
        "ornekler": [
            {k: v for k, v in d.items() if not k.startswith("_")}
            | {"sebep": d["_sebep"], "eski_stok": d["_eski"], "baslik": d["_baslik"]}
            for d in duzeltilecek[:30]
        ],
    }

    if not uygula or not duzeltilecek:
        if not uygula:
            rapor["mesaj"] = "Kuru çalışma — hiçbir şey yazılmadı. Uygulamak için uygula=true."
        return rapor

    # GÜVENLİK KİLİDİ — TOPTAN SIFIRLAMAYA İZİN VERME.
    # Veritabanı sorgusu yarım dönerse (bağlantı koptu, koleksiyon boş geldi)
    # her ilan "bizde yok" görünür ve tek turda BÜTÜN katalog satıştan kalkar.
    # Böyle bir tablo stok gerçeği değil, arıza belirtisidir: dokunmuyoruz,
    # sayıp bildiriyoruz. Aynı kilit tedarikçi stok senkronunda da var.
    sifirlanacak = sum(1 for d in duzeltilecek if d["quantity"] == 0)
    if not bizdeki:
        rapor.update({"uygulandi": False, "durduruldu": "katalog_bos",
                      "mesaj": ("Kendi barkod listemiz BOŞ döndü — bu bir veri arızası. "
                                "Hiçbir şey yazılmadı.")})
        logger.error("[ty-guard] durduruldu: kendi barkod haritası boş")
        return rapor
    if not zorla and satirlar and sifirlanacak > len(satirlar) * 0.40:
        rapor.update({"uygulandi": False, "durduruldu": "toplu_sifirlama",
                      "mesaj": (f"{sifirlanacak}/{len(satirlar)} ilan sıfırlanacaktı — "
                                "oran fazla yüksek, arıza olabilir. Hiçbir şey yazılmadı. "
                                "Raporu inceleyip gerçekten isteniyorsa panelden "
                                "onaylayarak çalıştırın.")})
        logger.error("[ty-guard] durduruldu: %s/%s ilan sıfırlanacaktı",
                     sifirlanacak, len(satirlar))
        return rapor

    gonderilen = 0
    batch_ids: List[str] = []
    for i in range(0, len(duzeltilecek), _PARCA):
        kalemler = [{k: v for k, v in d.items() if not k.startswith("_")}
                    for d in duzeltilecek[i:i + _PARCA]]
        try:
            res = await cli.update_price_and_inventory(kalemler)
            bid = (res or {}).get("batchRequestId")
            if bid:
                batch_ids.append(bid)
                gonderilen += len(kalemler)
            else:
                # batchRequestId yoksa Trendyol isteği REDDETTİ — başarı sayma.
                logger.error(f"[ty-guard] parti reddedildi: {str(res)[:300]}")
        except Exception as e:
            logger.error(f"[ty-guard] parti gönderilemedi: {e}")

    arsivlenen = 0
    if arsivle and oksuz:
        for i in range(0, len(oksuz), 500):
            try:
                await cli.archive_products(oksuz[i:i + 500])
                arsivlenen += len(oksuz[i:i + 500])
            except Exception as e:
                logger.error(f"[ty-guard] arşivleme hatası: {e}")

    rapor.update({"gonderilen_kalem": gonderilen, "batch_ids": batch_ids,
                  "arsivlenen": arsivlenen})

    try:
        await db.trendyol_sync_logs.insert_one({
            "id": generate_id(),
            "started_at": _now_iso(),
            "status": "stock_guard",
            "products_attempted": len(duzeltilecek),
            "products_sent": gonderilen,
            "batch_request_id": batch_ids[0] if batch_ids else None,
            "message": (f"Stok koruması: {gonderilen} kalem düzeltildi "
                        f"({sayac['oksuz']} öksüz, {sayac['pasif_urun']} pasif ürün, "
                        f"{sayac['bizde_stok_yok']} stoksuz, {sayac['surukleme']} sürüklenme)"),
            "detay": sayac,
        })
    except Exception:
        pass
    return rapor


async def barkodlari_sifirla(barkodlar: List[str]) -> Dict[str, Any]:
    """Verilen barkodların Trendyol stoğunu HEMEN sıfırlar.

    Panelden ürün silindiğinde/pasife alındığında çağrılır: saatlik senkronu
    beklemek, o aradaki her siparişin gönderilemeyecek bir sipariş olması
    demek. Fiyat ilandan okunur, dokunulmaz.
    """
    barkodlar = [str(b).strip() for b in (barkodlar or []) if str(b).strip()]
    if not barkodlar:
        return {"success": True, "gonderilen": 0}
    cli, _ = await _istemci()
    if cli is None:
        return {"success": False, "mesaj": "Trendyol entegrasyonu kapalı."}

    kalemler: List[Dict[str, Any]] = []
    for bc in barkodlar:
        try:
            res = await cli.get_filtered_products(barcode=bc, size=1)
            satir = ((res or {}).get("content") or [None])[0]
        except Exception:
            satir = None
        if not satir:
            continue                      # Trendyol'da yok — yapacak bir şey yok
        satis, liste = _fiyat(satir)
        kalemler.append({"barcode": bc, "quantity": 0,
                         "salePrice": satis, "listPrice": liste})
    if not kalemler:
        return {"success": True, "gonderilen": 0}
    try:
        res = await cli.update_price_and_inventory(kalemler)
        return {"success": bool((res or {}).get("batchRequestId")),
                "gonderilen": len(kalemler),
                "batchRequestId": (res or {}).get("batchRequestId")}
    except Exception as e:
        logger.error(f"[ty-guard] anında sıfırlama hatası: {e}")
        return {"success": False, "hata": str(e)[:200]}


async def urun_barkodlari(product_id: str) -> List[str]:
    """Ürünün ve varyantlarının bütün barkodları."""
    p = await db.products.find_one(
        {"id": product_id}, {"_id": 0, "barcode": 1, "variants.barcode": 1})
    if not p:
        return []
    bl = [str(p.get("barcode") or "").strip()]
    bl += [str(v.get("barcode") or "").strip() for v in (p.get("variants") or [])]
    return [b for b in bl if b]


async def urunu_pazaryerinde_kapat(product_id: str) -> None:
    """Ürün panelden kalkınca pazaryerindeki stoğunu da sıfırlar (sessiz).

    Hata ASLA silme/pasife alma işlemini bozmaz: pazaryeri erişilemezse
    saatlik denetim aynı işi yapacak.
    """
    try:
        bl = await urun_barkodlari(product_id)
        if bl:
            await barkodlari_sifirla(bl)
    except Exception as e:
        logger.warning(f"[ty-guard] ürün kapatma sırasında sıfırlanamadı: {e}")


@router.post("/trendyol/stock-guard/reconcile")
async def stok_denetimi_ucu(payload: dict = Body(default={}),
                            current_user: dict = Depends(require_admin)):
    """Trendyol ilanlarını katalogla karşılaştır; satılamazları sıfırla.

    body: {uygula?: false, arsivle?: false, sadece_oksuz?: false}
    """
    p = payload or {}
    return await trendyol_stok_denetimi(
        uygula=bool(p.get("uygula")),
        arsivle=bool(p.get("arsivle")),
        sadece_oksuz=bool(p.get("sadece_oksuz")),
        zorla=bool(p.get("zorla")),
    )


@router.post("/trendyol/stock-guard/zero-barcodes")
async def barkod_sifirla_ucu(payload: dict = Body(...),
                             current_user: dict = Depends(require_admin)):
    """Verilen barkodların Trendyol stoğunu sıfırlar. body: {barcodes: [...]}"""
    return await barkodlari_sifirla((payload or {}).get("barcodes") or [])


@router.get("/trendyol/stock-guard/missing-variants")
async def beslemede_olmayan_bedenler(current_user: dict = Depends(require_admin)):
    """Beslemede ARTIK OLMAYAN bedenleri listeler.

    Bunlar tedarikçi XML'inden düşmüş varyantlar: stokları sıfırlanır ama kayıt
    SİLİNMEZ, çünkü barkodu kaybedersek pazaryerindeki ilanı bir daha
    kapatamayız. Temizliği yönetim, `cleanup-variants` ile bilinçli yapar.
    """
    urunler: List[Dict[str, Any]] = []
    toplam = 0
    async for p in db.products.find(
            {"variants.feed_missing": True},
            {"_id": 0, "id": 1, "name": 1, "stock_code": 1, "category_name": 1,
             "variants": 1}):
        eksik = [v for v in (p.get("variants") or []) if v.get("feed_missing")]
        if not eksik:
            continue
        toplam += len(eksik)
        urunler.append({
            "id": p["id"], "ad": p.get("name"), "stok_kodu": p.get("stock_code"),
            "kategori": p.get("category_name"),
            "toplam_beden": len(p.get("variants") or []),
            "eksik_bedenler": [
                {"beden": v.get("size"), "renk": v.get("color"),
                 "barkod": v.get("barcode"), "stok": v.get("stock"),
                 "kayip_tarihi": v.get("feed_missing_at")}
                for v in eksik],
        })
    return {"urun": len(urunler), "beden": toplam, "urunler": urunler}


@router.post("/trendyol/stock-guard/cleanup-variants")
async def bedenleri_temizle(payload: dict = Body(default={}),
                            current_user: dict = Depends(require_admin)):
    """Beslemede olmayan bedenleri ürün kartından kaldırır — GÜVENLİ SIRAYLA.

    SIRA ÖNEMLİ: önce pazaryerinde sıfırla, SONRA kaydı sil. Tersi yapılırsa
    barkod bizde kalmaz, Trendyol'daki ilan öksüz kalır ve satışta kalmaya devam
    eder — bu sorunun ta kendisi budur. Sıfırlama başarısız olan barkodun
    varyantı SİLİNMEZ, bir sonraki turda yeniden denenir.

    body: {uygula?: false, urun_id?: "", arsivle?: false}
    """
    p = payload or {}
    uygula = bool(p.get("uygula"))
    arsivle = bool(p.get("arsivle"))
    sorgu: Dict[str, Any] = {"variants.feed_missing": True}
    if p.get("urun_id"):
        sorgu = {"id": str(p["urun_id"])}

    hedefler: List[Dict[str, Any]] = []
    async for pr in db.products.find(sorgu, {"_id": 0, "id": 1, "name": 1, "variants": 1}):
        eksik = [v for v in (pr.get("variants") or []) if v.get("feed_missing")]
        # SON BEDENİ SİLME: bedensiz ürün satılamaz hâle gelir ve vitrinde
        # bozuk görünür. Hepsi kayıpsa karar ürünü pasife almaktır, beden
        # temizliği değil.
        if eksik and len(eksik) < len(pr.get("variants") or []):
            hedefler.append({"id": pr["id"], "ad": pr.get("name"), "eksik": eksik})

    ozet = {"urun": len(hedefler),
            "beden": sum(len(h["eksik"]) for h in hedefler),
            "sifirlanan": 0, "silinen": 0, "atlanan": 0}
    if not uygula:
        ozet["mesaj"] = "Kuru çalışma — hiçbir şey silinmedi. Uygulamak için uygula=true."
        ozet["ornekler"] = [
            {"ad": h["ad"], "bedenler": [v.get("size") for v in h["eksik"]]}
            for h in hedefler[:20]]
        return ozet

    for h in hedefler:
        barkodlar = [str(v.get("barcode") or "").strip() for v in h["eksik"]]
        barkodlar = [b for b in barkodlar if b]
        sonuc = await barkodlari_sifirla(barkodlar) if barkodlar else {"success": True}
        if not sonuc.get("success"):
            ozet["atlanan"] += len(h["eksik"])
            continue
        ozet["sifirlanan"] += int(sonuc.get("gonderilen") or 0)
        if arsivle and barkodlar:
            try:
                cli, _ = await _istemci()
                if cli:
                    await cli.archive_products(barkodlar)
            except Exception as e:
                logger.warning(f"[ty-guard] temizlikte arşivleme hatası: {e}")
        # Kaldırılan barkodları mezar taşı olarak sakla: ileride Trendyol'da
        # yeniden belirirse "bu bilerek kapatıldı" diyebilelim.
        try:
            for v in h["eksik"]:
                if v.get("barcode"):
                    await db.retired_barcodes.update_one(
                        {"barcode": str(v["barcode"])},
                        {"$set": {"barcode": str(v["barcode"]),
                                  "product_id": h["id"], "product_name": h["ad"],
                                  "size": v.get("size"), "color": v.get("color"),
                                  "reason": "beslemeden dustu",
                                  "retired_at": _now_iso()}}, upsert=True)
        except Exception:
            pass
        await db.products.update_one(
            {"id": h["id"]},
            {"$pull": {"variants": {"feed_missing": True}},
             "$set": {"updated_at": _now_iso()}})
        ozet["silinen"] += len(h["eksik"])

    # Ürün toplam stoğunu bedenlerden yeniden hesapla.
    for h in hedefler:
        pr = await db.products.find_one({"id": h["id"]}, {"_id": 0, "variants": 1})
        if pr:
            await db.products.update_one(
                {"id": h["id"]},
                {"$set": {"stock": sum(int(v.get("stock") or 0)
                                       for v in (pr.get("variants") or []))}})
    return ozet


async def saatlik_stok_denetimi():
    """Scheduler: satılamayan ilanların stoğunu saatte bir sıfırlar.

    UYGULAR (kuru çalışma değil) — bu işin amacı tam olarak budur. Arşivleme
    yapmaz: ilan silmek geri dönüşü olan ama gürültülü bir iş, onu yönetim
    panelden bilerek tetikler.
    """
    try:
        r = await trendyol_stok_denetimi(uygula=True, arsivle=False)
        s = r.get("sayac") or {}
        if r.get("gonderilen_kalem"):
            logger.info("[ty-guard] saatlik: %s kalem düzeltildi (öksüz=%s pasif=%s "
                        "stoksuz=%s sürüklenme=%s)",
                        r.get("gonderilen_kalem"), s.get("oksuz"), s.get("pasif_urun"),
                        s.get("bizde_stok_yok"), s.get("surukleme"))
    except Exception as e:
        logger.warning(f"[ty-guard] saatlik denetim hatası: {e}")
