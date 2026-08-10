"""
Application-level background scheduler (APScheduler).
Runs auto-cancellation of unpaid orders, etc.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
import uuid

from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def auto_cancel_unpaid_havale_orders():
    """Cancel havale/transfer orders that remain unpaid after 72 hours and restock."""
    from routes.deps import db  # lazy import
    from routes.orders import _restock_order_once
    import business_rules as _BR

    try:
        # AYAR: süre admin panelinden (İşletme Kuralları) yönetilir; varsayılan 72 saat.
        _hrs = int(await _BR.get_rule(db, "order.havale_cancel_hours", 72) or 72)
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=_hrs)).isoformat()
        # Accept both payment_method names.
        # ÖNEMLİ: Havale siparişleri "awaiting_payment" durumunda bekler (create_order öyle set eder);
        # eski sorgu yalnız pending/confirmed'e bakıyordu → awaiting_payment havaleler HİÇ iptal
        # edilmiyordu. Güvenli kapsam: DEKONT BİLDİRİLMEMİŞ (payment_notified HARİÇ) ve ödemesi
        # ONAYLANMAMIŞ (paid değil) havaleler 72 saatte iptal edilir. payment_notified (müşteri
        # dekont iletmiş) otomatik iptal EDİLMEZ — admin kontrol etsin (yanlışlıkla ödeyeni iptal etme).
        query = {
            "payment_status": {"$nin": ["paid", "expired", "refunded"]},
            "status": {"$in": ["pending", "awaiting_payment"]},
            "payment_method": {"$in": ["transfer", "havale", "bank_transfer", "eft", "havale_eft", "banka_havale"]},
            "created_at": {"$lt": cutoff},
        }
        cancelled = 0
        async for order in db.orders.find(query, {"_id": 0}):
            try:
                # O16: Önce durumu güncelle, SONRA idempotent iade yap.
                # TOCTOU koruması (D1 fix): sorgu ile update arasında müşteri ödemiş/dekont
                # bildirmiş olabilir. Filtreye payment_status != paid + status re-check ekle;
                # matched_count 0 ise (arada ödendi/durum değişti) İPTAL DE STOK GERİ DE YAPMA
                # → "72. saatte ödeyen müşterinin siparişi iptal edilip stoğu geri eklenmesi" biter.
                res = await db.orders.update_one(
                    {"id": order["id"],
                     "payment_status": {"$nin": ["paid", "expired", "refunded"]},
                     "status": {"$in": ["pending", "awaiting_payment"]}},
                    {"$set": {
                        "status": "cancelled",
                        "payment_status": "expired",
                        "cancel_reason": "72 saat içinde havale ödemesi yapılmadı (otomatik iptal)",
                        "auto_cancelled": True,
                        "cancelled_at": datetime.now(timezone.utc).isoformat(),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }}
                )
                if res.matched_count == 0:
                    continue  # arada ödendi / dekont bildirildi / durum değişti — dokunma
                await _restock_order_once(order, "havale_auto_cancel")
                cancelled += 1
                # Müşteriye bildirim: "Siparişiniz ödeme yapılmadığı için iptal edildi" (SMS+e-posta).
                # Standart 'order_cancelled' event'i (Ayarlar → Bildirimler → 'Sipariş İptal Edildi').
                try:
                    from notification_service import send_notification
                    from routes.orders import _order_notify_vars
                    _addr = order.get("shipping_address") or {}
                    _phone = _addr.get("phone") or order.get("phone")
                    _email = _addr.get("email") or order.get("email")
                    _ch = None
                    _label = "İptal Edildi"
                    try:
                        from order_statuses import get_status_config, customer_label_for
                        _cfg = await get_status_config(db)
                        _nz = (_cfg.get("notify") or {}).get("cancelled") or {}
                        _ch = [c for c in ("sms", "email") if _nz.get(c)] or None
                        _label = customer_label_for("cancelled")
                    except Exception:
                        pass
                    if _ch is None:
                        _ch = (["email"] if _email else []) + (["sms"] if _phone else [])
                    _vars = await _order_notify_vars(
                        order, status_label=_label,
                        cancel_reason="72 saat içinde havale ödemesi yapılmadı",
                    )
                    if _ch:
                        await send_notification(
                            db, "order_cancelled",
                            to_phone=_phone, to_email=_email,
                            variables=_vars, channels=_ch,
                        )
                except Exception as _e_notif:
                    logger.warning(f"[scheduler] havale-cancel notif failed for {order.get('order_number')}: {_e_notif}")
            except Exception as e_item:
                logger.error(f"Failed to cancel order {order.get('order_number')}: {e_item}")
        if cancelled:
            logger.info(f"[scheduler] Auto-cancelled {cancelled} unpaid havale orders (>72h)")
    except Exception as e:
        logger.exception(f"[scheduler] auto_cancel_unpaid_havale_orders failed: {e}")


async def auto_cancel_unpaid_card_orders():
    """Başarısız/ödenmemiş KART siparişlerini 24 saat sonra iptal edip stoğu geri ekler.

    KRİTİK: Bu iş zamanlanmamıştı → başarısız kart ödemeleri (iyzico 3DS reddi, yarıda kalan
    ödeme) create_order'da düşürülen stoğu KALICI sızdırıyordu (ürünler yanlışlıkla tükeniyordu).
    COD/havale HARİÇ (meşru şekilde bekler + kendi akışları var). Restock idempotenttir
    (_restock_order_once — 'auto_cancel_expired' hareketi bir kez eklenir)."""
    from routes.deps import db  # lazy import
    from routes.orders import _restock_order_once
    import business_rules as _BR
    try:
        # AYAR (varsayılan 3 saat): 3DS başlatılıp ödenmeyen kart siparişleri fazla beklemesin.
        # Güvenli: webhook (saniyeler) + reconcile (her 15dk) gerçekten çekilen ödemeyi bu süreden
        # ÇOK önce 'paid' yapar. Süre admin panelinden (İşletme Kuralları) yönetilir.
        _uch = int(await _BR.get_rule(db, "order.unpaid_card_cancel_hours", 3) or 3)
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=_uch)).isoformat()
        _cod_bank = ["cash_on_delivery", "kapida", "kapida_odeme", "cod",
                     "bank_transfer", "havale", "eft", "havale_eft", "banka_havale", "transfer"]
        query = {
            "payment_status": {"$in": ["pending", "failed"]},
            "status": {"$in": ["pending", "awaiting_payment"]},
            "payment_method": {"$nin": _cod_bank},
            "created_at": {"$lt": cutoff},
        }
        cancelled = 0
        async for order in db.orders.find(query, {"_id": 0}):
            try:
                # TOCTOU koruması: sorgu ile update arasında ödeme onaylanmış olabilir.
                # Filtreye payment_status != paid ekle → ödenmiş sipariş iptal/geri stok
                # EDİLMESİN. matched_count 0 ise (ödenmiş) restock da yapılmaz.
                res = await db.orders.update_one(
                    {"id": order["id"], "payment_status": {"$nin": ["paid", "refunded"]},
                     "status": {"$in": ["pending", "awaiting_payment"]}},
                    {"$set": {
                        # status="cancelled" DEĞİL "payment_failed": bu siparişin parası HİÇ alınmadı.
                        # "İptal Edildi" yazılırsa ekip "ödeme alınmıştı" sanıp yanlışlıkla PARA İADESİ
                        # yapıyordu. "payment_failed" = Ödeme Alınamadı → iade GEREKMEZ, ana listede
                        # görünmez. (Gerçekten para çekilmiş olsaydı reconcile job 24s dolmadan
                        # 'paid' yapardı; bu guard paid'i zaten atlıyor.)
                        "status": "payment_failed",
                        "payment_status": "expired",
                        "cancel_reason": "Ödeme 3 saat içinde tamamlanmadı — para HİÇ alınmadı (iade gerekmez)",
                        "auto_cancelled": True,
                        # Denetim #2: bu sipariş restock edildi. Geç reconcile (iyzico'dan para
                        # çekildiği ortaya çıkarsa) tekrar paid+confirmed olursa stok TEKRAR
                        # düşülmeli (oversell önle) — payment._mark_order_from_payment bu işareti okur.
                        "_restocked_by_autocancel": True,
                        "cancelled_at": datetime.now(timezone.utc).isoformat(),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }}
                )
                if res.matched_count == 0:
                    continue  # arada ödendi/durumu değişti — dokunma
                await _restock_order_once(order, "auto_cancel_expired")
                cancelled += 1
            except Exception as e_item:
                logger.error(f"Failed to cancel card order {order.get('order_number')}: {e_item}")
        if cancelled:
            logger.info(f"[scheduler] Auto-cancelled {cancelled} unpaid/failed card orders (>3h)")
    except Exception as e:
        logger.exception(f"[scheduler] auto_cancel_unpaid_card_orders failed: {e}")


async def reconcile_charged_but_unrecorded_orders():
    """OTOMATİK KURTARMA: iyzico'dan para ÇEKİLMİŞ ama 'ödendi' işaretlenmemiş kart siparişlerini
    (needs_reconciliation bayraklı VEYA pending/failed) iyzico'dan paymentId ile doğrulayıp
    finalize eder — kimsenin elle 'recover-charged' çalıştırmasına gerek kalmaz.
    _finalize_by_payment_id belirsiz cevapta siparişe DOKUNMAZ (yanlış paid yazmaz).
    Kapsam: son 7 gün, kart (COD/havale hariç), tur başına en çok 50 sipariş."""
    from routes.deps import db  # lazy import
    try:
        from routes.payment import _finalize_by_payment_id
    except Exception as e:
        logger.warning(f"[scheduler] reconcile import atlandı: {e}")
        return
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        _cod_bank = ["cash_on_delivery", "kapida", "kapida_odeme", "cod",
                     "bank_transfer", "havale", "eft", "havale_eft", "banka_havale", "transfer"]
        query = {
            "created_at": {"$gt": cutoff},
            "payment_method": {"$nin": _cod_bank},
            "payment_status": {"$ne": "paid"},
            "$or": [
                {"needs_reconciliation": True},
                {"payment_status": {"$in": ["pending", "failed"]}},
            ],
        }
        recovered = 0
        candidates = await db.orders.find(
            query, {"_id": 0, "id": 1, "reconcile_payment_id": 1,
                    "iyzico_payment_id": 1, "payment_id": 1, "order_number": 1,
                    "iyzico_retrieve_response": 1}
        ).sort("created_at", -1).to_list(50)
        for order in candidates:
            # KÖK NEDEN #1: eski 'failed' siparişlerde paymentId ÜST DÜZEYDE yok, yalnızca
            # iyzico_retrieve_response.paymentId içinde saklı. O yüzden nested alanı da OKU →
            # bayrağı olmayan mevcut takılı siparişler de otomatik kurtarılabilsin.
            _nested_pid = ""
            try:
                _nested_pid = str((order.get("iyzico_retrieve_response") or {}).get("paymentId") or "").strip()
            except Exception:
                _nested_pid = ""
            pid = str(order.get("reconcile_payment_id") or order.get("iyzico_payment_id")
                      or order.get("payment_id") or _nested_pid or "").strip()
            if not pid:
                continue  # paymentId yok → otomatik doğrulanamaz (webhook/callback bekler)
            try:
                paid = await _finalize_by_payment_id(order["id"], pid)
            except Exception as _e:
                logger.warning(f"[scheduler] reconcile finalize hata {order.get('order_number')}: {_e}")
                continue
            if paid:
                await db.orders.update_one(
                    {"id": order["id"]},
                    {"$unset": {"needs_reconciliation": "", "reconcile_payment_id": ""}})
                recovered += 1
        if recovered:
            logger.info(f"[scheduler] iyzico reconcile: {recovered} çekilmiş sipariş otomatik finalize edildi")
    except Exception as e:
        logger.exception(f"[scheduler] reconcile_charged_but_unrecorded_orders failed: {e}")


async def retry_pending_iys_consents():
    """Bildirilmemiş (reported=false) İYS izinlerini NetGSM'e periyodik YENİDEN gönderir.

    Neden: NetGSM İYS modülü aktivasyonu / İYS→NetGSM yetkilendirme yansıması gecikebilir
    ('iys modulunuzu aktiflestirin', code 40). Bu iş, modül aktif olur olmaz bekleyen tüm
    izinleri (W10427 vb.) KENDİLİĞİNDEN gönderir — kimsenin elle /iys/retry çalıştırması
    gerekmez. Başarılı olan (reported=true) kayıtlar sorgudan düşer; sadece son 60 günün
    bildirilmemişleri, her turda en çok 50 tanesi denenir (aşırı yüklenme olmaz)."""
    from routes.deps import db  # lazy import
    try:
        from routes.iys import _report_to_netgsm_iys
    except Exception as e:
        logger.warning(f"[scheduler] iys retry import atlandı: {e}")
        return
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        q = {"reported": {"$ne": True}, "created_at": {"$gte": cutoff}}
        recs = await db.iys_consents.find(q, {"_id": 0}).sort("created_at", -1).limit(50).to_list(None)
        if not recs:
            return
        ok = 0
        for r in recs:
            try:
                if await _report_to_netgsm_iys(r):
                    ok += 1
            except Exception as e_item:
                logger.warning(f"[scheduler] iys retry kayıt hata: {e_item}")
        if ok:
            logger.info(f"[scheduler] İYS: {ok}/{len(recs)} bekleyen izin NetGSM'e gönderildi")
    except Exception as e:
        logger.exception(f"[scheduler] retry_pending_iys_consents failed: {e}")


async def _ensure_hb_2min_sync():
    """Tek seferlik: Hepsiburada hesabını 2 dk'da bir STOK + SİPARİŞ senkronuna ayarlar.
    settings.hb_sync_2min_v1 bayrağıyla yalnızca bir kez uygulanır; sonradan
    Admin → Otomasyon panelinden serbestçe değiştirilebilir (bayrak tekrar yazmaz)."""
    from routes.deps import db  # lazy import
    try:
        flag = await db.settings.find_one({"id": "hb_sync_2min_v1"})
        if flag:
            return
        await db.marketplace_accounts.update_one(
            {"key": "hepsiburada"},
            {"$set": {
                "enabled": True,
                "auto_sync.products_enabled": True,
                "auto_sync.orders_enabled": True,
                "auto_sync.products_interval_min": 2,
                "auto_sync.orders_interval_min": 2,
            }},
        )
        await db.settings.update_one(
            {"id": "hb_sync_2min_v1"},
            {"$set": {"id": "hb_sync_2min_v1", "applied_at": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
        logger.info("[scheduler] Hepsiburada senkron 2 dk'ya ayarlandı (stok + sipariş)")
    except Exception as e:
        logger.warning(f"[scheduler] _ensure_hb_2min_sync failed: {e}")


async def _run_trendyol_auto_products_sync():
    """Scheduler tarafından Trendyol fiyat/stok senkronunu tetikler.
    HTTPException'ı yutar, log bırakır. Trendyol konfigürasyonu yoksa sessizce atlar.
    """
    from routes.deps import db
    from routes.marketplace_hub import log_integration_event
    try:
        from routes.integrations import _sync_inventory_to_trendyol, get_trendyol_config
        cfg = await get_trendyol_config()
        if not cfg.get("is_active"):
            return
        products = await db.products.find({"is_active": True}, {"_id": 0}).to_list(length=None)
        res = await _sync_inventory_to_trendyol(products)
        await log_integration_event(
            marketplace="trendyol", action="stock_update",
            status=("success" if res.get("success") else "failed"),
            direction="outbound",
            message=f"[cron] Trendyol stok/fiyat senkronu: {res.get('message', '')}"
        )
    except Exception as e:
        try:
            await log_integration_event(
                marketplace="trendyol", action="stock_update", status="failed",
                direction="outbound",
                message=f"[cron] Trendyol ürün senkron hatası: {e}"
            )
        except Exception:
            pass


async def _update_existing_trendyol_order(_db, existing, data, number, restock_source):
    """Mevcut Trendyol siparişini günceller: status DIŞI alanları (isim/adres/kalem)
    tazeler VE terminal pazaryeri durumlarını (iptal/iade) YANSITIR.

    Eksik iptal kök nedeni: statussuz genel çekiş TÜM durumları döndürür (Cancelled
    dahil) ama eskiden status hariç tutulduğu için satıcı/stok iptalleri (claim
    ÜRETMEZ) 'confirmed' kalıp İptaller'e hiç düşmüyordu. Artık terminal durum
    yansıtılır; confirmed→cancelled geçişinde idempotent stok iadesi yapılır
    (status pass ile AYNI guard: stock_movements type=order_cancelled).

    Döner: flipped_to_cancelled (bool).
    """
    _set = {k: v for k, v in data.items() if k != "status"}
    _new_status = data.get("status")
    _prev_status = existing.get("status")
    if _new_status in ("cancelled", "returned") and _prev_status != _new_status:
        _set["status"] = _new_status
        if _new_status == "cancelled":
            _set.setdefault("cancel_source", "trendyol")
    await _db.orders.update_one({"_id": existing["_id"]}, {"$set": _set})
    _flipped = (_new_status == "cancelled" and _prev_status != "cancelled")
    if _flipped and existing.get("id"):
        try:
            from routes.orders import _stock_delta_for_order, _RESTORE_MOVE_TYPES
            # B4: guard'ı TÜM restore hareketlerine genişlet. Yalnız 'order_cancelled' aramak,
            # sipariş önceden kısmi iade (return_restock/order_returned) ile geri stoklanmışsa
            # bunu göremeyip TÜM siparişi İKİNCİ kez +stokluyordu.
            _already = await _db.stock_movements.find_one(
                {"order_id": existing.get("id"), "type": {"$in": _RESTORE_MOVE_TYPES}}, {"_id": 1}
            )
            if not _already:
                _moves = await _stock_delta_for_order(existing, +1)
                await _db.stock_movements.insert_one({
                    "id": str(uuid.uuid4()), "type": "order_cancelled",
                    "order_id": existing.get("id"), "order_number": number,
                    "items": _moves, "source": restock_source,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
        except Exception as _re:
            logger.error(f"[cron] iptal restock {number}: {_re}")
    return _flipped


async def _reconcile_trendyol_terminal(client, days_back=30, slice_days=15):
    """STATUSSUZ uzlaştırma: statussuz çekiş tüm durumları getirir; terminal duruma
    (iptal/iade) geçmiş ama Facette'te güncellenmemiş siparişleri İptaller/İadeler'e
    yansıtır/ekler. Satıcı/stok iptalleri (claim üretmeyen, status=Cancelled list
    sorgusunda güvenilir gelmeyen) buradan GARANTİ yakalanır. Volume için tarih
    dilimlerine bölünür. TEK SEFERLİK geniş backfill için days_back büyük verilir."""
    from datetime import datetime as _dt, timedelta as _td
    from routes.deps import db as _db, generate_id
    from routes.integrations import map_trendyol_order, _ms_to_iso
    import asyncio as _aio
    now_ = _dt.now()
    flipped = 0
    inserted = 0
    elapsed = 0
    slice_end = now_
    while elapsed < days_back:
        this_slice = min(slice_days, days_back - elapsed)
        slice_start = slice_end - _td(days=this_slice)
        start_ms = int(slice_start.timestamp() * 1000)
        end_ms = int(slice_end.timestamp() * 1000)
        page = 0
        while page < 50:
            try:
                resp = await client.get_orders(start_date_ms=start_ms, end_date_ms=end_ms, size=200, page=page)
            except Exception as _qe:
                logger.error(f"[cron] terminal reconcile sorgu hatası: {_qe}")
                break
            chunk = resp.get("content", []) or []
            for t_order in chunk:
                try:
                    number = str(t_order.get("orderNumber"))
                    data = map_trendyol_order(t_order)
                    existing = await _db.orders.find_one({"order_number": number, "platform": "trendyol"})
                    if existing:
                        if await _update_existing_trendyol_order(_db, existing, data, number, "trendyol_terminal_reconcile"):
                            flipped += 1
                    elif data.get("status") in ("cancelled", "returned"):
                        # Hiç içe aktarılmamış ESKİ iptal/iade → İptaller'e indir.
                        # Stok DÜŞÜLMEZ (hiç rezerve edilmemişti). created_at = gerçek
                        # sipariş tarihi (orderDate) → İptaller'de tarih sıralaması doğru.
                        data["id"] = generate_id()
                        data["created_at"] = _ms_to_iso(t_order.get("orderDate")) or datetime.now(timezone.utc).isoformat()
                        await _db.orders.insert_one(data)
                        inserted += 1
                except Exception as _e:
                    logger.error(f"[cron] terminal reconcile {t_order.get('orderNumber')}: {_e}")
            total_pages = resp.get("totalPages") or 0
            page += 1
            if not chunk or page >= total_pages:
                break
        slice_end = slice_start
        elapsed += this_slice
        await _aio.sleep(0.3)  # Trendyol rate limit + sunucuyu yormama
    if flipped or inserted:
        logger.info(f"[cron] terminal uzlaştırma {days_back}g: {flipped} iptal yansıtıldı / {inserted} eklendi")
    return flipped, inserted


async def _run_trendyol_auto_orders_pull():
    """Scheduler tarafından Trendyol sipariş çekmeyi tetikler."""
    from routes.marketplace_hub import log_integration_event
    try:
        from routes.integrations import get_trendyol_config
        cfg = await get_trendyol_config()
        if not cfg.get("is_active"):
            return
        # Doğrudan route fonksiyonunu çağırmıyoruz (require_admin için);
        # onun yerine mantığı burada çoğaltmadan, küçük bir internal job tetikliyoruz.
        import sys, os
        from datetime import datetime as _dt, timedelta as _td
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from trendyol_client import TrendyolClient
        from routes.deps import db as _db, generate_id
        from routes.integrations import map_trendyol_order, _sync_trendyol_status_passes

        client = TrendyolClient(
            supplier_id=cfg["supplier_id"],
            api_key=cfg["api_key"],
            api_secret=cfg["api_secret"],
            mode=cfg["mode"],
        )
        now_ = _dt.now()
        start = now_ - _td(days=14)
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(now_.timestamp() * 1000)
        # Trendyol tek sayfada en fazla 200 paket dondurur; TUM sayfalari dolas.
        # (Sayfalama yokken 200'den fazla siparis olunca 201+ hep atlaniyordu.)
        content = []
        _page = 0
        _MAX_PAGES = 50
        while _page < _MAX_PAGES:
            _resp = await client.get_orders(
                start_date_ms=start_ms, end_date_ms=end_ms, size=200, page=_page
            )
            _chunk = _resp.get("content", []) or []
            content.extend(_chunk)
            _total_pages = _resp.get("totalPages") or 0
            _page += 1
            if not _chunk or _page >= _total_pages:
                break
        imported = 0
        updated = 0
        for t_order in content:
            try:
                number = str(t_order.get("orderNumber"))
                existing = await _db.orders.find_one({"order_number": number, "platform": "trendyol"})
                data = map_trendyol_order(t_order)
                if existing:
                    await _update_existing_trendyol_order(_db, existing, data, number, "trendyol_auto_pull_cancel")
                    updated += 1
                else:
                    data["id"] = generate_id()
                    data["created_at"] = datetime.now(timezone.utc).isoformat()
                    await _db.orders.insert_one(data)
                    imported += 1
                    # Zaten iptal/iade durumunda gelen YENİ sipariş için stok DÜŞÜLMEZ
                    # (hiç rezerve edilmemişti → -1 yanlış olurdu).
                    if data.get("status") not in ("cancelled", "returned"):
                        from routes.integrations import _decrement_stock_for_imported_order
                        await _decrement_stock_for_imported_order(data, "trendyol")
            except Exception as _ex:
                logger.error(f"[cron] Trendyol order import hata: {_ex}")
        try:
            await _sync_trendyol_status_passes(client, start_ms, end_ms)
        except Exception as _ex2:
            logger.error(f"[cron] Trendyol status pass hata: {_ex2}")
        await log_integration_event(
            marketplace="trendyol", action="order_pull", status="success",
            direction="inbound",
            message=f"[cron] Trendyol sipariş çekildi: +{imported} yeni / {updated} güncellendi"
        )
    except Exception as e:
        try:
            await log_integration_event(
                marketplace="trendyol", action="order_pull", status="failed",
                direction="inbound",
                message=f"[cron] Trendyol sipariş çekme hatası: {e}"
            )
        except Exception:
            pass


async def _run_trendyol_cancel_pass():
    """HAFİF + SIK iptal taraması (5 dk). Yalnızca Cancelled, son 14 gün, dar pencere →
    yeni iptaller İptaller'e hızlı düşer ama sunucuyu yormaz. Eski/geç iptaller ve
    iade/teslim-edilemedi ayrı SAATLİK geniş tarama (_run_trendyol_status_wide_pass) ile
    kapanır; derin geçmiş manuel backfill butonuyla çekilir."""
    try:
        from routes.integrations import get_trendyol_config
        cfg = await get_trendyol_config()
        if not cfg.get("is_active"):
            return
        from datetime import datetime as _dt, timedelta as _td
        from trendyol_client import TrendyolClient
        from routes.integrations import _sync_trendyol_status_passes
        client = TrendyolClient(
            supplier_id=cfg["supplier_id"], api_key=cfg["api_key"],
            api_secret=cfg["api_secret"], mode=cfg["mode"],
        )
        now_ = _dt.now()
        start_ms = int((now_ - _td(days=14)).timestamp() * 1000)
        end_ms = int(now_.timestamp() * 1000)
        # Sadece Cancelled + dar 14g pencere (widen_cancel=False) → hafif.
        await _sync_trendyol_status_passes(client, start_ms, end_ms, widen_cancel=False, statuses=("Cancelled",))
    except Exception as e:
        logger.error(f"[cron] Trendyol hızlı iptal taraması hatası: {e}")


async def _run_trendyol_status_wide_pass():
    """GENİŞ durum taraması — SAATTE BİR (sık değil → hafif). Cancelled 45 güne kadar
    (geç iptaller), Returned/UnDelivered 30 gün. Sık 5 dk'lık taramanın kaçırdığı
    eski/geç durum değişikliklerini kapatır."""
    try:
        from routes.integrations import get_trendyol_config
        cfg = await get_trendyol_config()
        if not cfg.get("is_active"):
            return
        from datetime import datetime as _dt, timedelta as _td
        from trendyol_client import TrendyolClient
        from routes.integrations import _sync_trendyol_status_passes
        client = TrendyolClient(
            supplier_id=cfg["supplier_id"], api_key=cfg["api_key"],
            api_secret=cfg["api_secret"], mode=cfg["mode"],
        )
        now_ = _dt.now()
        start_ms = int((now_ - _td(days=30)).timestamp() * 1000)
        end_ms = int(now_.timestamp() * 1000)
        await _sync_trendyol_status_passes(client, start_ms, end_ms)  # 3 durum, Cancelled 45g widen
    except Exception as e:
        logger.error(f"[cron] Trendyol geniş durum taraması hatası: {e}")


async def _run_trendyol_deep_backfill_once():
    """TEK SEFERLİK derin uzlaştırma — geçmişteki TÜM Trendyol iptallerini (satıcı/stok
    dahil) sisteme taşır: 365 güne kadar STATUSSUZ tarama, terminal duruma geçmiş ama
    Facette'te güncellenmemiş siparişleri İptaller'e indirir. Yeni flag
    (trendyol_terminal_backfill) ile YALNIZCA BİR KEZ çalışır; sonra no-op. Sonrasında
    günlük iptalleri 5 dk'lık anlık auto pull (14 gün, terminal flip) yakalar."""
    try:
        from routes.deps import db
        flag = await db.settings.find_one({"id": "trendyol_terminal_backfill"}, {"_id": 0})
        if flag and flag.get("done"):
            return
        from routes.integrations import get_trendyol_config, sync_trendyol_claims
        cfg = await get_trendyol_config()
        if not cfg.get("is_active"):
            return
        from trendyol_client import TrendyolClient
        client = TrendyolClient(
            supplier_id=cfg["supplier_id"], api_key=cfg["api_key"],
            api_secret=cfg["api_secret"], mode=cfg["mode"],
        )
        await db.settings.update_one(
            {"id": "trendyol_terminal_backfill"},
            {"$set": {"id": "trendyol_terminal_backfill", "running": True,
                      "started_at": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
        # STATUSSUZ 365 günlük uzlaştırma (15 günlük dilimler) → satıcı/stok iptalleri
        # dahil TÜM takılı iptaller bir kerede İptaller'e iner.
        flipped, inserted = await _reconcile_trendyol_terminal(client, days_back=365, slice_days=15)
        # Müşteri iptal/iade sebeplerini de tazele (claim → cancel_reason).
        try:
            await sync_trendyol_claims(days_back=180, current_user={"is_admin": True})
        except Exception as _cl:
            logger.error(f"[terminal backfill claims] {_cl}")
        await db.settings.update_one(
            {"id": "trendyol_terminal_backfill"},
            {"$set": {"id": "trendyol_terminal_backfill", "done": True, "running": False,
                      "flipped": flipped, "inserted": inserted,
                      "finished_at": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
        logger.info(f"[scheduler] Trendyol TEK SEFERLİK terminal backfill tamamlandı: "
                    f"{flipped} iptal yansıtıldı / {inserted} eklendi")
    except Exception as e:
        logger.error(f"[cron] Trendyol terminal backfill hatası: {e}")


async def _run_trendyol_open_claims_refresh():
    """AÇIK iade kovalarını (talep/kargoda/aksiyon) TY canlı verisiyle eşitler — dakikada bir."""
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(__file__))
        from routes.integrations_trendyol import _refresh_open_claims_core
        await _refresh_open_claims_core()
    except Exception as e:
        logger.error(f"[cron] açık iade canlı eşitleme hatası: {e}")


async def _run_trendyol_claims_sync():
    """Trendyol iade/iptal (claims) senkronu — periyodik. Müşterinin Trendyol'da seçtiği
    GERÇEK iptal sebebini çeker ve CANCEL claim'leri eşleşen iptal siparişlerine bağlar
    (İptaller'de "Trendyol iptali" yerine gerçek sebep görünür)."""
    try:
        from routes.integrations import get_trendyol_config
        cfg = await get_trendyol_config()
        if not cfg.get("is_active"):
            return
        # Çekirdek senkron (require_admin bypass) — periyodik kısa pencere; durum
        # geçişleri (Created→Accepted/Rejected) her turda canlı tazelenir.
        from routes.integrations import _sync_trendyol_claims_core
        await _sync_trendyol_claims_core(days_back=60)
    except Exception as e:
        logger.error(f"[cron] Trendyol claims senkron hatası: {e}")


async def _run_hepsiburada_claims_sync():
    """Hepsiburada iade (claims) senkronu — periyodik. HB iade talepleri Trendyol'la
    ortak iade/GP ekranına (db.trendyol_claims, platform=hepsiburada) yazılır."""
    try:
        from routes.integrations_hepsiburada import _sync_hepsiburada_claims_core
        await _sync_hepsiburada_claims_core(days_back=60)
    except Exception as e:
        logger.error(f"[cron] Hepsiburada claims senkron hatası: {e}")


async def _run_trendyol_claims_deep_backfill_once():
    """TEK SEFERLİK derin claims backfill — geçmişteki TÜM Trendyol iadelerini (3 yıl)
    çeker; eski onaylı/reddedilen claim'ler de sekme sayılarına yansısın. Ayrı flag
    (trendyol_claims_deep_backfill) ile YALNIZCA BİR KEZ çalışır; sonra no-op."""
    try:
        from routes.deps import db
        flag = await db.settings.find_one({"id": "trendyol_claims_deep_backfill"}, {"_id": 0})
        if flag and flag.get("done"):
            return
        from routes.integrations import get_trendyol_config, _sync_trendyol_claims_core
        cfg = await get_trendyol_config()
        if not cfg.get("is_active"):
            return
        await db.settings.update_one(
            {"id": "trendyol_claims_deep_backfill"},
            {"$set": {"id": "trendyol_claims_deep_backfill", "running": True,
                      "started_at": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
        res = await _sync_trendyol_claims_core(days_back=1095)
        await db.settings.update_one(
            {"id": "trendyol_claims_deep_backfill"},
            {"$set": {"id": "trendyol_claims_deep_backfill", "done": True, "running": False,
                      "total_synced": res.get("total_synced", 0),
                      "finished_at": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
        logger.info(f"[scheduler] Trendyol TEK SEFERLİK claims derin backfill tamamlandı: "
                    f"{res.get('total_synced', 0)} kayıt")
    except Exception as e:
        logger.error(f"[cron] Trendyol claims derin backfill hatası: {e}")


async def _run_hepsiburada_auto_orders_pull():
    """Scheduler tarafından Hepsiburada (OMS) sipariş çekmeyi tetikler.
    OMS kimliği yoksa _get_hb_client hata döndürür → sessizce no-op olur
    (Trendyol akışıyla aynı: tarih aralığı tara, upsert, yeni siparişte stok düş)."""
    from routes.marketplace_hub import log_integration_event
    try:
        import asyncio as _aio
        from datetime import datetime as _dt, timedelta as _td
        from routes.category_mapping import _get_hb_client
        from routes.integrations import (
            _hb_orders_from_response, _hb_enrich_items, map_hepsiburada_order,
            _hb_created_at, _decrement_stock_for_imported_order,
        )
        from routes.deps import db as _db, generate_id

        client, err = await _get_hb_client()
        if err:
            # Kimlik (özellikle OMS) yoksa gürültü yapma — sessiz geç.
            return

        # TARİHSİZ çağrı: HB OMS tüm AÇIK (Open/Unpacked) siparişleri döner — yeni
        # sipariş 2 dk'lık turda buradan yakalanır. (Tarihli sorgu HB'de yalnız
        # 24 saatlik pencere kabul eder ve format hatasında HTTP 400 üretiyordu.)
        try:
            resp = await _aio.to_thread(client.get_orders, None, None, 0, 200)
        except Exception as e:
            await log_integration_event(
                marketplace="hepsiburada", action="order_pull", status="failed",
                direction="inbound", message=f"[cron] HB sipariş çekme hatası: {e}")
            return

        grouped = _hb_orders_from_response(resp)
        imported = updated = 0
        for g in grouped:
            try:
                data = await _hb_enrich_items(map_hepsiburada_order(g))
                number = data["order_number"]
                existing = await _db.orders.find_one({"order_number": number, "platform": "hepsiburada"})
                if existing:
                    await _db.orders.update_one(
                        {"_id": existing["_id"]},
                        {"$set": {k: v for k, v in data.items() if k != "status"}})
                    updated += 1
                else:
                    data["id"] = generate_id()
                    data["created_at"] = _hb_created_at(data)
                    await _db.orders.insert_one(data)
                    imported += 1
                    # Zaten iptal/iade gelen YENİ sipariş için stok düşülmez (hiç rezerve edilmemişti).
                    if data.get("status") not in ("cancelled", "returned"):
                        await _decrement_stock_for_imported_order(data, "hepsiburada")
            except Exception as e:
                await log_integration_event(
                    marketplace="hepsiburada", action="import_order", status="error",
                    direction="inbound", message=f"[cron] HB sipariş aktarım hatası: {e}")
        if imported or updated:
            await log_integration_event(
                marketplace="hepsiburada", action="order_pull", status="success",
                direction="inbound",
                message=f"[cron] HB otomatik çekim: {imported} yeni, {updated} güncellendi")
    except Exception as e:
        logger.exception(f"[scheduler] hepsiburada auto orders pull failed: {e}")


async def _run_hepsiburada_auto_stock_sync():
    """Scheduler — Hepsiburada stok/fiyat senkronu (Trendyol akışıyla simetrik).
    Tüm aktif ürünlerin güncel stok+fiyatını HB listing'ine gönderir.
    HB kimliği yoksa _get_hb_client hata döndürür → sessizce no-op."""
    from routes.deps import db
    from routes.marketplace_hub import log_integration_event
    try:
        from routes.category_mapping import _get_hb_client
        from routes.integrations import (
            _hb_markup, _hb_price_source, _hb_sku_source,
            _hb_listing_items_from_product, _hb_push_stock_price,
        )
        client, err = await _get_hb_client()
        if err:
            return  # kimlik yoksa sessiz geç
        products = await db.products.find({"is_active": True}, {"_id": 0}).to_list(length=None)
        markup = await _hb_markup()
        price_source = await _hb_price_source()
        # ⚠️ KRİTİK: merchantSku kaynağı import ile AYNI olmalı. Aksi halde stok/fiyat
        # gönderimi import'taki SKU ile eşleşmez → HB listing'i bulamaz → fiyat/stok
        # sessizce hiç uygulanmaz ("aktardım ama fiyat/stok gitmedi"). sku_source'u oku.
        sku_source = await _hb_sku_source()
        items = []
        for prod in products:
            items.extend(_hb_listing_items_from_product(prod, markup, price_source, sku_source))
        if not items:
            return
        res = await _hb_push_stock_price(client, items, True, True)
        errs = res.get("errors") if isinstance(res, dict) else None
        await log_integration_event(
            marketplace="hepsiburada", action="stock_update",
            status=("success" if not errs else "failed"), direction="outbound",
            message=f"[cron] HB stok/fiyat senkronu: {len(items)} kalem"
                    + (f" — {'; '.join(errs)}" if errs else ""))
    except Exception as e:
        try:
            await log_integration_event(
                marketplace="hepsiburada", action="stock_update", status="failed",
                direction="outbound", message=f"[cron] HB stok senkron hatasi: {e}")
        except Exception:
            pass


async def _run_hb_invoice_autoheal():
    """KALICI ÇÖZÜM: HB'ye yüklenmemiş faturaları periyodik yeniden gönder (kendi-kendini
    iyileştirme). Anlık gönderim başarısız olsa bile bu döngü kurtarır. İdempotent, hafif."""
    try:
        from routes.orders import autoheal_hb_invoices
        # hours=None → TÜM eski yüklenmemiş HB faturaları taranır (uploaded!=True olduğu için
        # yüklenenler her turda kümeden düşer; 'eskileri yükle' kalıcı kapanır).
        res = await autoheal_hb_invoices(hours=None, limit=500)
        logger.info(f"[scheduler][hb-invoice] tarandı={res.get('checked')} yüklendi={res.get('uploaded')} "
                    f"{('hata=' + str(res.get('error'))) if res.get('error') else ''}")
    except Exception as e:
        logger.warning(f"[scheduler][hb-invoice] autoheal hatası: {e}")


# Y21: Fire-and-forget senkron task'ları için kilit + referans havuzu.
# Önceden create_task referanssız çağrılıyordu → (a) 2 dk aralıkta >2 dk süren pull ardılıyla
# ÇAKIŞIP çift sipariş insert + çift stok düşümü yapabiliyor, (b) referans tutulmadığı için GC
# task'ı yarıda öldürebiliyordu. Artık aynı iş bitmeden ikincisi başlamaz ve _last_*_sync
# zaman damgası spawn'da değil TAMAMLANINCA yazılır.
_RUNNING_SYNCS: set = set()
_SYNC_TASKS: set = set()


def _spawn_guarded_sync(coro_factory, lock_key: str, account_key: str, stamp_field: str):
    """Kilitli, referanslı arka plan senkron başlatır. Zaten çalışıyorsa atlar."""
    if lock_key in _RUNNING_SYNCS:
        logger.info(f"[scheduler] {lock_key} zaten çalışıyor — bu tur atlandı (çakışma önlendi)")
        return
    _RUNNING_SYNCS.add(lock_key)

    async def _wrapper():
        try:
            await coro_factory()
        except Exception as _e:
            logger.exception(f"[scheduler] {lock_key} senkron hata: {_e}")
        finally:
            try:
                from routes.deps import db as _db
                from datetime import datetime as _dt, timezone as _tz
                await _db.marketplace_accounts.update_one(
                    {"key": account_key}, {"$set": {stamp_field: _dt.now(_tz.utc).isoformat()}}
                )
            except Exception:
                pass
            _RUNNING_SYNCS.discard(lock_key)

    t = asyncio.create_task(_wrapper())
    _SYNC_TASKS.add(t)
    t.add_done_callback(_SYNC_TASKS.discard)


async def _marketplace_sync_tick():
    """
    Her dk'da bir çalışır; her marketplace_account'un auto_sync ayarlarına
    bakar, periyoda gelmişse ürün push ve sipariş pull tetikler.

    TASARIM:
      - `auto_sync.products_interval_min` dk geçtiyse → ürün push tetikle
      - `auto_sync.orders_interval_min` dk geçtiyse → sipariş pull tetikle
      - `marketplace_accounts.{_last_products_sync, _last_orders_sync}` alanlarına
        son çalışma zamanı yazılır (next check için kıyas).
      - Gerçek çağrı internal HTTP olarak yapılmaz; direkt servis fonksiyonu
        çağrılır. Şu an her pazaryeri için ortak "dummy push" ile log düşer;
        mevcut integrations.py servisleri ileride buraya bağlanacak.
      - Tüm deneme sonuçları integration_logs'a düşer (ayrı bir marker ile).
    """
    from routes.deps import db
    from routes.marketplace_hub import log_integration_event

    now = datetime.now(timezone.utc)
    try:
        async for acc in db.marketplace_accounts.find({"enabled": True}, {"_id": 0}):
            key = acc.get("key")
            sync = acc.get("auto_sync") or {}

            # --- Ürünler ---------------------------------------------------
            if sync.get("products_enabled"):
                last = acc.get("_last_products_sync")
                try:
                    last_dt = datetime.fromisoformat(last) if last else None
                except Exception:
                    last_dt = None
                interval = max(1, int(sync.get("products_interval_min") or 3))
                due = (not last_dt) or (now - last_dt) >= timedelta(minutes=interval)
                if due:
                    await log_integration_event(
                        marketplace=key, action="product_push", status="queued",
                        direction="outbound",
                        message=f"[cron] Otomatik ürün senkron tetiklendi (her {interval} dk)"
                    )
                    # Trendyol için gerçek push'u arka planda kuyruğa al (Y21: kilitli + damga bitişte)
                    if key == "trendyol":
                        _spawn_guarded_sync(_run_trendyol_auto_products_sync,
                                             f"products:{key}", key, "_last_products_sync")
                    elif key == "hepsiburada":
                        _spawn_guarded_sync(_run_hepsiburada_auto_stock_sync,
                                             f"products:{key}", key, "_last_products_sync")

            # --- Siparişler ------------------------------------------------
            if sync.get("orders_enabled"):
                last = acc.get("_last_orders_sync")
                try:
                    last_dt = datetime.fromisoformat(last) if last else None
                except Exception:
                    last_dt = None
                interval = max(1, int(sync.get("orders_interval_min") or 5))
                due = (not last_dt) or (now - last_dt) >= timedelta(minutes=interval)
                if due:
                    lookback = int(sync.get("orders_lookback_hours") or 100)
                    await log_integration_event(
                        marketplace=key, action="order_pull", status="queued",
                        direction="inbound",
                        message=f"[cron] Otomatik sipariş çek tetiklendi (her {interval} dk, son {lookback} saat)"
                    )
                    if key == "trendyol":
                        _spawn_guarded_sync(_run_trendyol_auto_orders_pull,
                                             f"orders:{key}", key, "_last_orders_sync")
                    elif key == "hepsiburada":
                        _spawn_guarded_sync(_run_hepsiburada_auto_orders_pull,
                                             f"orders:{key}", key, "_last_orders_sync")
    except Exception as e:
        logger.exception(f"[scheduler] marketplace sync tick failed: {e}")


async def _send_abandoned_cart_reminders():
    """Her gün 10:00 UTC'de, son 2-48 saat içinde aktif olup sipariş vermemiş
    e-posta sahibi sepetlere 1 kez hatırlatma maili gönderir. İşaretlenmiş
    sepetlere tekrar gönderilmez.
    """
    from routes.deps import db
    try:
        from routes.catalog_extras import _send_email_via_resend  # lazy
    except Exception as e:
        logger.warning(f"[scheduler] abandoned cart mail skip (import): {e}")
        return
    from email_smtp import get_smtp_config, is_configured
    if not is_configured(await get_smtp_config(db)):
        return  # E-posta (SMTP/Zoho) yapılandırılmamış → sessizce atla
    now = datetime.now(timezone.utc)
    # Terkedilmiş sepet penceresi — İşletme Kuralları'ndan (varsayılan 2-48 saat).
    try:
        from business_rules import get_rule as _get_rule
        _min_h = int(await _get_rule(db, "marketing.abandoned_cart_min_hours", 2))
        _max_h = int(await _get_rule(db, "marketing.abandoned_cart_max_hours", 48))
    except Exception:
        _min_h, _max_h = 2, 48
    if _max_h <= _min_h:
        _max_h = _min_h + 1
    start = (now - timedelta(hours=_max_h)).isoformat()
    end = (now - timedelta(hours=_min_h)).isoformat()
    q = {
        "updated_at": {"$gte": start, "$lte": end},
        "total": {"$gt": 0},
        "email": {"$ne": ""},
        "abandoned_reminder_sent": {"$ne": True},
    }
    try:
        carts = await db.cart_sessions.find(q, {"_id": 0}).to_list(500)
        if not carts:
            return
        recipients = list({c.get("email") for c in carts if c.get("email")})
        if not recipients:
            return
        subject = "Sepetinizi unutmayın ✨ Favori ürünleriniz sizi bekliyor"
        html = (
            "<h2>Sepetinizdeki ürünler tükeniyor!</h2>"
            "<p>Seçtiğiniz ürünleri tamamlamak için hazır bir alışveriş sepetiniz var.</p>"
            "<p><a href=\"https://facette.com.tr\" style=\"background:#000;color:#fff;padding:12px 20px;border-radius:8px;text-decoration:none\">Sepete Dön</a></p>"
            "<p style=\"font-size:12px;color:#888;margin-top:24px\">Bu e-posta otomatik gönderilmiştir.</p>"
        )
        ok, failed, errs = await _send_email_via_resend(recipients, subject, html)
        # O13: Gönderim BAŞARISIZ olduysa sepetleri "hatırlatıldı" işaretleme — aksi halde
        # SMTP hatasında bu sepetler bir daha ASLA hatırlatılmıyordu. Yalnızca hiç hata yoksa işaretle.
        if failed == 0 and ok > 0:
            for c in carts:
                await db.cart_sessions.update_one(
                    {"session_id": c.get("session_id")},
                    {"$set": {"abandoned_reminder_sent": True, "abandoned_reminder_at": now.isoformat()}},
                )
        else:
            logger.warning(f"[scheduler] Abandoned cart mail kismen/tamamen basarisiz (sent={ok} failed={failed}) — işaretlenmedi, sonraki turda tekrar denenecek")
        logger.info(f"[scheduler] Abandoned cart reminders: sent={ok} failed={failed} errs={errs[:1]}")
    except Exception as e:
        logger.exception(f"[scheduler] abandoned cart reminders failed: {e}")


async def _send_daily_stock_alert(threshold: int = None):
    """Her gün, stoğu `threshold` veya altına düşmüş ürün-varyant kombinasyonlarını
    bulup admin kullanıcılara (is_admin=True) özet e-posta gönderir. Eşik verilmezse
    İşletme Kuralları'ndan okunur (stock.low_stock_alert_threshold, varsayılan 3).

    2026-07-02: notification_service.py'de 'stock_alert' bildirim tipi tanımlıydı
    ('Stok Uyarısı (Admin)') ama hiçbir yerde tetiklenmiyordu. Sorgu mantığı
    routes/bulk_ops.py:GET /stock-alerts ile aynı (kopyalandı, davranış korunuyor).
    """
    from routes.deps import db
    if threshold is None:
        try:
            from business_rules import get_rule as _get_rule
            threshold = int(await _get_rule(db, "stock.low_stock_alert_threshold", 3))
        except Exception:
            threshold = 3
    try:
        from routes.catalog_extras import _send_email_via_resend  # lazy
    except Exception as e:
        logger.warning(f"[scheduler] stock alert mail skip (import): {e}")
        return
    from email_smtp import get_smtp_config, is_configured
    if not is_configured(await get_smtp_config(db)):
        return  # E-posta (SMTP/Zoho) yapılandırılmamış → sessizce atla
    try:
        alerts = []
        async for p in db.products.find(
            {"status": {"$ne": "archived"}},
            {"_id": 0, "id": 1, "name": 1, "stock_code": 1, "variants": 1, "stock": 1},
        ):
            variants = p.get("variants") or []
            if variants:
                for v in variants:
                    s = v.get("stock")
                    if s is not None and s <= threshold:
                        alerts.append({
                            "name": p.get("name", ""),
                            "variant": f"{v.get('size','')} {v.get('color','')}".strip(),
                            "stock_code": v.get("stock_code") or p.get("stock_code"),
                            "stock": s,
                        })
            else:
                s = p.get("stock")
                if s is not None and s <= threshold:
                    alerts.append({
                        "name": p.get("name", ""), "variant": "—",
                        "stock_code": p.get("stock_code"), "stock": s,
                    })
        if not alerts:
            return
        admins = await db.users.find(
            {"is_admin": True, "is_active": {"$ne": False}}, {"_id": 0, "email": 1}
        ).to_list(50)
        recipients = list({a.get("email") for a in admins if a.get("email")})
        if not recipients:
            return
        rows = "".join(
            f"<tr><td>{a['name']}</td><td>{a['variant']}</td>"
            f"<td>{a['stock_code'] or ''}</td><td>{a['stock']}</td></tr>"
            for a in alerts[:200]  # e-posta boyutu için sınırla
        )
        subject = f"Stok Uyarısı: {len(alerts)} ürün-varyant eşik altında (≤{threshold})"
        html = (
            f"<h2>Düşük Stok Uyarısı</h2>"
            f"<p>{len(alerts)} ürün-varyant kombinasyonu {threshold} veya altında stoğa sahip.</p>"
            f"<table border='1' cellpadding='6' style='border-collapse:collapse'>"
            f"<tr><th>Ürün</th><th>Varyant</th><th>Stok Kodu</th><th>Stok</th></tr>{rows}</table>"
            f"<p style='font-size:12px;color:#888;margin-top:24px'>Bu e-posta otomatik gönderilmiştir.</p>"
        )
        ok, failed, errs = await _send_email_via_resend(recipients, subject, html)
        logger.info(f"[scheduler] Stock alert: {len(alerts)} items, sent={ok} failed={failed} errs={errs[:1]}")
    except Exception as e:
        logger.exception(f"[scheduler] stock alert failed: {e}")




async def _ticimax_sync_stock():
    """Ticimax SelectUrun ile canlı stok senkronu — 2 saatte bir tetiklenir.
    routes/ticimax_stock_sync içindeki gerçek implementasyonu çağırır.
    """
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(__file__))
        from routes.ticimax_stock_sync import sync_ticimax_stock  # type: ignore
        result = await sync_ticimax_stock(
            max_products=2000, aktif=None, page_size=50,
            current_user={"role": "admin", "id": "scheduler"},
        )
        logger.info(f"[scheduler][ticimax_stock] {result.get('message','done')}")
    except Exception as e:
        logger.exception(f"[scheduler] ticimax_stock_sync failed: {e}")


async def _ticimax_sync_orders():
    """Periyodik olarak Ticimax'tan site siparişlerini çek (idempotent).
    Son 30 günün siparişleri, 5 sayfa × 100. Yeni site siparişlerini DB'ye yazar.
    """
    try:
        import sys, os, uuid
        sys.path.insert(0, os.path.dirname(__file__))
        from ticimax_client import get_orders as tc_get_orders
        from routes.deps import db  # lazy import
        from routes.marketplace_hub import log_integration_event  # lazy import
        s = await db.settings.find_one({"id": "ticimax"}) or {}
        api_key = s.get("api_key") or "AKG0M8DTRSEBAIA898JA6HW22EDIU3"
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=30)
        start_str = start_dt.strftime("%d.%m.%Y")
        end_str = end_dt.strftime("%d.%m.%Y")
        new_count = 0
        skipped_mp = 0
        seen_pages = 0

        # Ortak parser (KargoAdresi/FaturaAdresi nested + UrunListesi item)
        from ticimax_order_parser import parse_ticimax_order, is_marketplace_order

        updated_count = 0
        for page in range(1, 6):
            try:
                orders = tc_get_orders(
                    page=page, page_size=100, wscode=api_key,
                    start_date=start_str, end_date=end_str,
                    exclude_marketplace=False, only_with_phone=False,
                )
            except Exception as e:
                logger.warning(f"[cron][ticimax] page {page} error: {e}")
                break
            if not orders:
                break
            seen_pages += 1
            for o in orders:
                if not o:
                    continue
                if is_marketplace_order(o):
                    skipped_mp += 1
                    continue
                doc = parse_ticimax_order(o, api_key=api_key)
                if not doc:
                    continue
                tc_id = doc["ticimax_order_id"]
                tc_no = doc["order_number"]
                # Idempotent: varsa update, yoksa insert
                exist = await db.orders.find_one(
                    {"$or": [{"order_number": tc_no}, {"ticimax_order_id": tc_id}]},
                    {"_id": 0, "id": 1}
                )
                try:
                    if exist:
                        # Sadece güncelleme (created_at korunur, id korunur, user_id korunur)
                        await db.orders.update_one(
                            {"id": exist["id"]},
                            {"$set": {**{k: v for k, v in doc.items() if k != "created_at"},
                                      "updated_at": datetime.now(timezone.utc).isoformat()}}
                        )
                        updated_count += 1
                    else:
                        doc["id"] = str(uuid.uuid4())[:8]
                        doc["user_id"] = None
                        doc["imported_from"] = "ticimax_cron"
                        doc["imported_at"] = datetime.now(timezone.utc).isoformat()
                        await db.orders.insert_one(doc)
                        new_count += 1
                        from routes.integrations import _decrement_stock_for_imported_order
                        await _decrement_stock_for_imported_order(doc, "ticimax")
                except Exception as ie:
                    logger.warning(f"[cron][ticimax] upsert err: {ie}")
        if new_count > 0 or updated_count > 0:
            logger.info(f"[cron][ticimax] +{new_count} yeni / ~{updated_count} güncellendi (skipped MP={skipped_mp}, pages={seen_pages})")
            await log_integration_event(
                marketplace="ticimax", action="order_pull", status="success",
                direction="inbound",
                message=f"[cron] {new_count} yeni + {updated_count} güncellendi"
            )
    except Exception as e:
        logger.exception(f"[cron][ticimax] sync fatal: {e}")



DEFAULT_PII_RETENTION_DAYS = 30
PII_RETENTION_PLATFORMS = ["amazon"]  # varsayılan kapsam: Amazon SP-API kaynaklı siparişler


async def _pii_retention_purge():
    """Amazon DPP uyumu — PII saklama süresi (varsayılan 30 gün) dolan siparişlerde
    kişisel verileri (isim, telefon, e-posta, adres) anonimleştirir.
    Muhasebe için sipariş no/tutar/ürün korunur; sadece kişisel tanımlayıcılar silinir.
    Config: db.settings id='pii_retention' { enabled, days, platforms }.
    """
    from routes.deps import db
    try:
        cfg = await db.settings.find_one({"id": "pii_retention"}) or {}
        if cfg.get("enabled") is False:
            return
        days = int(cfg.get("days") or DEFAULT_PII_RETENTION_DAYS)
        platforms = cfg.get("platforms") or PII_RETENTION_PLATFORMS
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        query = {
            "platform": {"$in": platforms},
            "status": {"$in": ["shipped", "delivered", "completed"]},
            "pii_redacted": {"$ne": True},
            "$or": [
                {"shipped_at": {"$lt": cutoff}},
                {"delivered_at": {"$lt": cutoff}},
                {"updated_at": {"$lt": cutoff}},
            ],
        }
        redacted = 0
        async for order in db.orders.find(query, {"_id": 0, "id": 1}):
            await db.orders.update_one(
                {"id": order["id"]},
                {"$set": {
                    "shipping_address.first_name": "[silindi]",
                    "shipping_address.last_name": "",
                    "shipping_address.full_name": "[silindi]",
                    "shipping_address.phone": "[silindi]",
                    "shipping_address.email": "[silindi]",
                    "shipping_address.address": "[silindi]",
                    "shipping_address.address_line": "[silindi]",
                    "billing_address.first_name": "[silindi]",
                    "billing_address.phone": "[silindi]",
                    "billing_address.email": "[silindi]",
                    "billing_address.address": "[silindi]",
                    "customer_email": "[silindi]",
                    "customer_phone": "[silindi]",
                    "customer_name": "[silindi]",
                    "buyer_email": "[silindi]",
                    "user_data": None,
                    "pii_redacted": True,
                    "pii_redacted_at": datetime.now(timezone.utc).isoformat(),
                }},
            )
            redacted += 1
        if redacted:
            logger.info(f"[scheduler][pii] {redacted} siparişte PII anonimleştirildi (>{days} gün, {platforms})")
            await db.audit_logs.insert_one({
                "id": str(uuid.uuid4()),
                "action": "pii_retention_purge",
                "category": "compliance",
                "details": {"redacted": redacted, "days": days, "platforms": platforms},
                "actor": "system_scheduler",
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
    except Exception as e:
        logger.exception(f"[scheduler] pii_retention_purge failed: {e}")


def _parse_tr_dt(s: str):
    """MNG teslim tarihini ISO'ya cevirir; basarisizsa None."""
    s = (s or "").strip()
    if not s:
        return None
    for f in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
              "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, f).replace(tzinfo=timezone.utc).isoformat()
        except Exception:
            continue
    return None


async def _dhl_cargo_poll_tick():
    """Her 5 dk: site (facette) siparislerini MNG/DHL e-Commerce API'sinden sorgula.
      - GONDERI_NO / takip linki olustuysa -> 'shipped' (Kargoya Verildi) + bildirim
      - Teslim edildiyse -> 'delivered' (Teslim Edildi) + delivered_at + bildirim
    Bildirimler Ayarlar > Siparis Durumlari (sms/email) secimine gore gonderilir.
    SOAP cagrilari thread executor'da calisir (event loop bloklanmaz).
    """
    from routes.deps import db  # lazy
    try:
        from routes.orders import _get_mng_settings
        from order_statuses import get_status_config, customer_label_for
        from notification_service import send_notification
        from mng_kargo_client import get_mng_shipment_status
    except Exception as e:
        logger.warning(f"[scheduler][dhl] import skip: {e}")
        return

    # DENETİM FIX (#10): İzleme paneli 'dhl_poll_health' dokümanını okuyor ama hiç yazılmıyordu.
    # Başlangıç/bitiş + sayaçlar (matched/processed/shipped/delivered/errors/duration) yazılır.
    _run_started = datetime.now(timezone.utc)

    async def _write_health(status: str, **extra):
        try:
            doc = {"status": status, "updated_at": datetime.now(timezone.utc).isoformat()}
            doc.update(extra)
            await db.settings.update_one({"id": "dhl_poll_health"}, {"$set": doc}, upsert=True)
        except Exception as _he:
            logger.warning(f"[scheduler][dhl] health write err: {_he}")

    s = await _get_mng_settings()
    if not s.get("is_active") or not s.get("username"):
        logger.info("[scheduler][dhl] atlandi: MNG/DHL ayarlari aktif degil ya da kullanici adi yok")
        await _write_health("inactive", last_run_at=_run_started.isoformat(),
                            last_finish_at=datetime.now(timezone.utc).isoformat(),
                            matched=0, processed=0, shipped=0, delivered=0, errors=0,
                            duration_ms=0, interval_min=5,
                            note="MNG/DHL ayarları aktif değil ya da kullanıcı adı yok")
        return
    user, pw = s["username"], s["password"]

    cutoff = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    q = {
        "platform": {"$in": ["facette", "site", None]},
        "status": {"$in": ["confirmed", "processing", "preparing", "ready_to_ship",
                            "shipped", "in_transit", "out_for_delivery"]},
        "$or": [
            {"cargo_tracking_number": {"$nin": [None, ""]}},
            {"cargo_provider_name": {"$nin": [None, ""]}},
            {"cargo_barcode_created": True},
        ],
        "created_at": {"$gt": cutoff},
    }
    try:
        cfg = await get_status_config(db)
    except Exception:
        cfg = {"notify": {}}
    notify = cfg.get("notify") or {}

    try:
        matched = await db.orders.count_documents(q)
    except Exception:
        matched = 0
    await _write_health("running", last_run_at=_run_started.isoformat(),
                        matched=matched, interval_min=5)

    processed = 0
    n_shipped = 0
    n_delivered = 0
    n_errors = 0
    try:
        async for order in db.orders.find(q, {"_id": 0}).limit(120):
            siparis_no = str(order.get("order_number") or order.get("id") or "").strip()
            if not siparis_no:
                continue
            try:
                info = await asyncio.to_thread(
                    get_mng_shipment_status, username=user, password=pw, siparis_no=siparis_no
                )
            except Exception as e:
                logger.warning(f"[scheduler][dhl] status err {siparis_no}: {e}")
                n_errors += 1
                await asyncio.sleep(0.2)
                continue
            if not info or not info.get("ok"):
                await asyncio.sleep(0.2)
                continue

            gonderi = (info.get("gonderi_no") or "").strip()
            url = (info.get("kargo_takip_url") or "").strip()
            # DHL eCommerce: takip no varsa müşteri direkt takip deep-link'i (genel /gonderitakip yerine)
            _track_no = gonderi or order.get("cargo_tracking_number", "")
            track_link = f"https://kargotakip.dhlecommerce.com.tr/?takipNo={_track_no}" if _track_no else url
            teslim = (info.get("teslim_tarihi") or "").strip()
            statu = (info.get("kargo_statu") or "0").strip()
            aciklama = (info.get("kargo_statu_aciklama") or "")
            cur = order.get("status")

            # Y20: "teslim alın(dı)" / "şubeden teslim" = ŞUBE/KABUL hareketi, TESLİMAT DEĞİL.
            # "teslim" substring'i bunları da eşleyip siparişi ilk taramada yanlış 'delivered'
            # yapıyor, müşteriye erken "Teslim Edildi" bildirimi gidiyordu.
            _dl_aç = aciklama.lower()
            _pickup = any(k in _dl_aç for k in ("teslim alın", "teslim alin", "şubeden teslim",
                                                "subeden teslim", "şubede teslim", "subede teslim"))
            delivered = (not _pickup) and (bool(teslim) or ("teslim" in _dl_aç and "edilemedi" not in _dl_aç))
            # İLK OKUTMA tespiti — sadece kargocu/şube siparişi fiilen okuttuğunda "Kargoya Verildi".
            # Barkod oluşturulurken gönderi_no/takip url'i dolabildiği için onlar TEK BAŞINA tetik DEĞİL;
            # yalnızca MNG/DHL bir HAREKET statüsü (kargo_statu ≠ 0) ya da kabul/şube/okutma açıklaması
            # döndürdüğünde durumu çeviriyoruz. (kargo_statu: 0=İşlem Yok, 1+/100+=şubeye girdi/işleniyor)
            _alow = aciklama.lower()
            _acc_kw = ("kabul", "şube", "sube", "okut", "teslim alın", "teslim alin",
                       "işleme", "isleme", "çıkış", "cikis", "girdi", "transfer",
                       "dağıt", "dagit", "yola")
            shipped = (statu not in ("", "0")) or any(k in _alow for k in _acc_kw)
            now_iso = datetime.now(timezone.utc).isoformat()

            new_status = None
            upd = {}
            if delivered and cur != "delivered":
                new_status = "delivered"
                upd["delivered_at"] = _parse_tr_dt(teslim) or now_iso
            elif shipped and _track_no and cur in ("confirmed", "processing", "preparing", "ready_to_ship"):
                # Takip no oluşmadan "Kargoya Verildi" YAPMA — yoksa müşteriye giden SMS'teki
                # {tracking_url} no'suz genel /gonderitakip sayfasını açar. No gelene kadar bekle;
                # sonraki taramada gönderi no dolunca durum çevrilir + deep-link'li SMS gider.
                new_status = "shipped"
                upd["shipped_at"] = now_iso

            if gonderi:
                upd["cargo_gonderi_no"] = gonderi
                upd["cargo_tracking_number"] = gonderi
            if track_link:
                upd["cargo_tracking_url"] = track_link
                upd["cargo_tracking_link"] = track_link
            if aciklama:
                upd["cargo_status_text"] = aciklama
            if new_status:
                upd["status"] = new_status
            if upd:
                upd["updated_at"] = now_iso
                await db.orders.update_one({"id": order["id"]}, {"$set": upd})

            if new_status == "shipped":
                n_shipped += 1
            elif new_status == "delivered":
                n_delivered += 1

            if new_status in ("shipped", "delivered"):
                ev = "order_shipped" if new_status == "shipped" else "order_delivered"
                nz = notify.get(new_status) or {}
                ch = [c for c in ("sms", "email") if nz.get(c)]
                if ch:
                    addr = order.get("shipping_address") or {}
                    try:
                        await send_notification(
                            db, ev,
                            to_phone=addr.get("phone") or order.get("phone"),
                            to_email=addr.get("email") or order.get("email"),
                            variables={
                                "customer_name": addr.get("full_name") or addr.get("first_name") or "Müşterimiz",
                                "order_number": siparis_no,
                                "amount": f"{order.get('total', 0):.2f} TL",
                                "tracking_number": gonderi or order.get("cargo_tracking_number", ""),
                                "tracking_url": track_link or order.get("cargo_tracking_url", ""),
                                "tracking_link": track_link or order.get("cargo_tracking_url", ""),
                                "status_label": customer_label_for(new_status),
                            },
                            channels=ch,
                        )
                    except Exception as e:
                        logger.warning(f"[scheduler][dhl] notif {siparis_no}: {e}")
                logger.info(f"[scheduler][dhl] {siparis_no} -> {new_status}")

            processed += 1
            await asyncio.sleep(0.25)
        _final_status = "ok"
    except Exception as e:
        logger.exception(f"[scheduler][dhl] poll tick failed: {e}")
        n_errors += 1
        _final_status = "error"
    _fin = datetime.now(timezone.utc)
    _dur_ms = int((_fin - _run_started).total_seconds() * 1000)
    await _write_health(
        _final_status,
        last_run_at=_run_started.isoformat(),
        last_finish_at=_fin.isoformat(),
        matched=matched, processed=processed,
        shipped=n_shipped, delivered=n_delivered, errors=n_errors,
        duration_ms=_dur_ms, interval_min=5,
    )
    logger.info(f"[scheduler][dhl] tick bitti — {processed} site siparisi sorgulandi "
                f"(shipped={n_shipped} delivered={n_delivered} err={n_errors})")


async def _return_cargo_poll_tick():
    """Her 5 dk: müşteriden depoya gelen İADE kargolarını MNG'den sorgula.
      - Kargo hareketi (ilk okutma) -> 'return_in_transit' (İade Kargoda) + bildirim
      - Depoya teslim -> 'returned' (Teslim Alındı; Aksiyon'a düşer) + returned_at + bildirim
      - Barkod 3 gün geçerli: süre dolmuş + hâlâ kargoya verilmemiş -> 'expired'
    customer_returns.status: created -> in_transit -> received | expired
    orders.status:           return_requested -> return_in_transit -> returned
    SOAP çağrıları thread executor'da çalışır (event loop bloklanmaz).
    """
    from routes.deps import db  # lazy
    try:
        from routes.orders import _get_mng_settings
        from order_statuses import get_status_config
        from notification_service import send_notification
        from mng_kargo_client import get_mng_shipment_status
    except Exception as e:
        logger.warning(f"[scheduler][return] import skip: {e}")
        return

    s = await _get_mng_settings()
    if not s.get("is_active") or not s.get("username"):
        return
    user, pw = s["username"], s["password"]

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    cutoff = (now - timedelta(days=45)).isoformat()
    q = {
        "status": {"$in": ["created", "in_transit"]},
        "created_at": {"$gt": cutoff},
        "$or": [
            {"mng_ref": {"$nin": [None, ""]}},
            {"return_code": {"$nin": [None, ""]}},
        ],
    }
    try:
        cfg = await get_status_config(db)
    except Exception:
        cfg = {"notify": {}}
    notify = cfg.get("notify") or {}

    def _expired(vu):
        if not vu:
            return False
        try:
            d = datetime.fromisoformat(str(vu).replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
        except Exception:
            return False
        return now > d

    processed = 0
    try:
        async for rec in db.customer_returns.find(q, {"_id": 0, "barcode_png_b64": 0}).limit(120):
            ref = str(rec.get("mng_ref") or rec.get("return_code") or "").strip()
            if not ref:
                continue
            cur = rec.get("status")  # created | in_transit

            try:
                info = await asyncio.to_thread(
                    get_mng_shipment_status, username=user, password=pw, siparis_no=ref
                )
            except Exception as e:
                logger.warning(f"[scheduler][return] status err {ref}: {e}")
                await asyncio.sleep(0.2)
                continue

            moved = delivered = False
            teslim = aciklama = ""
            if info and info.get("ok"):
                teslim = (info.get("teslim_tarihi") or "").strip()
                statu = (info.get("kargo_statu") or "0").strip()
                aciklama = (info.get("kargo_statu_aciklama") or "")
                _alow = aciklama.lower()
                # Y20: şube/kabul "teslim alındı" hareketini teslimat sayma (iade akışını erken tetikler).
                _pickup2 = any(k in _alow for k in ("teslim alın", "teslim alin", "şubeden teslim",
                                                    "subeden teslim", "şubede teslim", "subede teslim"))
                delivered = (not _pickup2) and (bool(teslim) or ("teslim" in _alow and "edilemedi" not in _alow))
                _acc_kw = ("kabul", "şube", "sube", "okut", "teslim alın", "teslim alin",
                           "işleme", "isleme", "çıkış", "cikis", "girdi", "transfer",
                           "dağıt", "dagit", "yola")
                moved = (statu not in ("", "0")) or any(k in _alow for k in _acc_kw)

            new_ret = new_order = ev = None
            ostamp = {}
            if delivered:
                # B1 fix: iade kargosu depoya ULAŞTI → durum içeride 'returned' (aksiyon/inceleme)
                # olur AMA müşteriye "İadeniz Tamamlandı" SMS'i GÖNDERİLMEZ — para henüz iade
                # edilmedi (erken/yanlış bildirim olurdu). Müşteri, iade bedeli ödenince
                # 'order_refunded' ("İade Bedeliniz Ödendi") bildirimini alır (refund_pay adımı).
                new_ret, new_order, ev = "received", "returned", None
                ostamp["returned_at"] = _parse_tr_dt(teslim) or now_iso
            elif moved and cur == "created":
                new_ret, new_order, ev = "in_transit", "return_in_transit", "order_return_in_transit"
                ostamp["return_shipped_at"] = now_iso

            # Hareket yok / hâlâ created ve 3 günlük barkod süresi dolmuş -> expired
            if new_ret is None:
                if cur == "created" and _expired(rec.get("valid_until")):
                    await db.customer_returns.update_one({"id": rec["id"]},
                        {"$set": {"status": "expired", "updated_at": now_iso}})
                    logger.info(f"[scheduler][return] {ref} -> expired (barkod 3g doldu)")
                await asyncio.sleep(0.2)
                continue

            if new_ret == cur:  # idempotent
                await asyncio.sleep(0.2)
                continue

            await db.customer_returns.update_one({"id": rec["id"]},
                {"$set": {"status": new_ret, "cargo_status_text": aciklama, "updated_at": now_iso}})
            order = await db.orders.find_one(
                {"id": rec.get("order_id")},
                {"_id": 0, "shipping_address": 1, "phone": 1, "email": 1, "total": 1}) or {}
            await db.orders.update_one({"id": rec.get("order_id")}, {"$set": {
                "status": new_order, "return_request.status": new_ret,
                "updated_at": now_iso, **ostamp,
            }})

            nz = notify.get(new_order) or notify.get(new_ret) or {}
            ch = [c for c in ("sms", "email") if nz.get(c)]
            if ch and ev:
                addr = order.get("shipping_address") or {}
                try:
                    await send_notification(
                        db, ev,
                        to_phone=addr.get("phone") or order.get("phone"),
                        to_email=addr.get("email") or order.get("email"),
                        variables={
                            "customer_name": addr.get("full_name") or addr.get("first_name") or "Müşterimiz",
                            "order_number": rec.get("order_number", ""),
                            "tracking_number": ref,
                        },
                        channels=ch,
                    )
                except Exception as e:
                    logger.warning(f"[scheduler][return] notif {ref}: {e}")

            logger.info(f"[scheduler][return] {ref} {cur} -> {new_ret}")
            processed += 1
            await asyncio.sleep(0.25)
    except Exception as e:
        logger.exception(f"[scheduler][return] poll tick failed: {e}")
    if processed:
        logger.info(f"[scheduler][return] polled {processed} return cargo(s)")


async def _notify_back_in_stock():
    """Favorilenen ürünlerden stoğu 'yok' → 'var' geçenleri tespit edip, o ürünü
    favorileyen kullanıcılara 'tekrar stokta' e-postası gönderir.

    db.product_stock_flags ile her favorilenen ürünün stok durumu izlenir; bildirim
    YALNIZCA 0→var geçişinde (ve restok döngüsü başına bir kez) atılır. İlk görülmede
    (flag yok → prev=None) bildirim ATILMAZ; bu deploy sonrası toplu spam'i önler.
    Stok master'ı Facette olduğundan (sipariş/iptal/iade/admin/pazaryeri fark etmez)
    bu periyodik kontrol tüm stok-giriş yollarını tek noktadan yakalar.
    """
    import os
    from routes.deps import db
    try:
        from notification_service import send_notification
    except Exception as e:
        logger.warning(f"[scheduler] back-in-stock skip (import): {e}")
        return
    now_iso = datetime.now(timezone.utc).isoformat()
    site = os.environ.get("SITE_URL", "https://facette.com.tr").rstrip("/")
    try:
        fav_pids = await db.favorites.distinct("product_id")
        if not fav_pids:
            return
        notified = 0
        for pid in fav_pids:
            if not pid:
                continue
            prod = await db.products.find_one(
                {"id": pid},
                {"_id": 0, "id": 1, "name": 1, "slug": 1, "stock": 1, "variants": 1, "is_active": 1},
            )
            if not prod:
                continue
            variants = prod.get("variants") or []
            if variants:
                total = sum(int(v.get("stock") or 0) for v in variants if isinstance(v, dict))
            else:
                total = int(prod.get("stock") or 0)
            in_stock = (total > 0) and (prod.get("is_active", True) is not False)
            flag = await db.product_stock_flags.find_one({"product_id": pid}, {"_id": 0, "in_stock": 1})
            prev = flag.get("in_stock") if flag else None
            await db.product_stock_flags.update_one(
                {"product_id": pid},
                {"$set": {"product_id": pid, "in_stock": in_stock, "updated_at": now_iso}},
                upsert=True,
            )
            # Sadece 'yok' → 'var' geçişinde bildir (ilk görülmede prev=None → atla)
            if prev is False and in_stock:
                favs = await db.favorites.find(
                    {"product_id": pid}, {"_id": 0, "user_id": 1}
                ).to_list(10000)
                uids = list({f.get("user_id") for f in favs if f.get("user_id")})
                if not uids:
                    continue
                users = await db.users.find(
                    {"id": {"$in": uids}}, {"_id": 0, "email": 1, "first_name": 1}
                ).to_list(10000)
                link = f"{site}/urun/{prod.get('slug') or prod.get('id')}"
                for u in users:
                    email = (u.get("email") or "").strip()
                    if not email or "@" not in email:
                        continue
                    try:
                        await send_notification(
                            db, "wishlist_back_in_stock",
                            to_email=email,
                            variables={
                                "customer_name": (u.get("first_name") or "").strip() or "değerli müşterimiz",
                                "product_name": prod.get("name") or "Favori ürünün",
                                "product_link": link,
                            },
                            channels=["email"],
                        )
                        notified += 1
                    except Exception as ie:
                        logger.warning(f"[scheduler] back-in-stock send failed ({email}): {ie}")

        # DENETİM FIX (#23): "Gelince Haber Ver" (db.stock_notifications) talepleri hiç
        # bildirilmiyordu (orphaned collection). notified=False kayıtları taranır; ürün+beden
        # stoğa girmişse e-posta gönderilir ve notified=True + notified_at yazılır (beden bazlı
        # eşleşme: varyant 'size' ile). Aynı ürün+beden için stok cache'lenir (tek DB okuması).
        try:
            pending = await db.stock_notifications.find(
                {"notified": {"$ne": True}}, {"_id": 0}
            ).to_list(5000)
        except Exception:
            pending = []
        _prod_cache: dict = {}
        for req in pending:
            pid = req.get("product_id")
            size = (req.get("size") or "").strip()
            email = (req.get("email") or "").strip()
            if not pid or not email or "@" not in email:
                continue
            prod = _prod_cache.get(pid)
            if prod is None:
                prod = await db.products.find_one(
                    {"id": pid}, {"_id": 0, "id": 1, "name": 1, "slug": 1, "stock": 1,
                                  "variants": 1, "is_active": 1})
                _prod_cache[pid] = prod or {}
            if not prod:
                continue
            if prod.get("is_active", True) is False:
                continue
            variants = prod.get("variants") or []
            if size and variants:
                # Beden bazlı: yalnız o bedene ait varyant(lar)ın stoğuna bak
                sz_stock = 0
                for v in variants:
                    if not isinstance(v, dict):
                        continue
                    vsize = (v.get("size") or v.get("name") or "").strip()
                    if vsize.lower() == size.lower():
                        sz_stock += int(v.get("stock") or 0)
                available = sz_stock > 0
            elif variants:
                available = sum(int(v.get("stock") or 0) for v in variants if isinstance(v, dict)) > 0
            else:
                available = int(prod.get("stock") or 0) > 0
            if not available:
                continue
            link = f"{site}/urun/{prod.get('slug') or prod.get('id')}"
            _pname = prod.get("name") or "Ürün"
            if size:
                _pname = f"{_pname} ({size} beden)"
            try:
                await send_notification(
                    db, "wishlist_back_in_stock",
                    to_email=email,
                    variables={
                        "customer_name": "değerli müşterimiz",
                        "product_name": _pname,
                        "product_link": link,
                    },
                    channels=["email"],
                )
                await db.stock_notifications.update_one(
                    {"id": req.get("id")},
                    {"$set": {"notified": True, "notified_at": now_iso}},
                )
                notified += 1
            except Exception as ie:
                logger.warning(f"[scheduler] stock-notify send failed ({email}): {ie}")

        if notified:
            logger.info(f"[scheduler] back-in-stock notified={notified}")
    except Exception as e:
        logger.exception(f"[scheduler] back_in_stock failed: {e}")


async def _run_award_referrals():
    """Bölüm C: davet edilenin ilk siparişi oluştuysa referans ödüllerini ver (idempotent)."""
    try:
        from routes.referrals import award_pending_referrals
        await award_pending_referrals()
    except Exception as e:
        logger.exception(f"[scheduler] referans ödül job hata: {e}")


async def _run_award_loyalty():
    """C3: ödemesi onaylanmış üye siparişlerine sadakat puanı yaz + iptal/iadede geri al (idempotent)."""
    try:
        from routes.loyalty import award_loyalty_points
        await award_loyalty_points()
    except Exception as e:
        logger.exception(f"[scheduler] sadakat puanı job hata: {e}")


async def _run_birthday_coupons():
    """Bölüm C: bugün doğum günü olan müşterilere kupon gönder (yıl-başına idempotent)."""
    try:
        from routes.referrals import send_birthday_coupons
        await send_birthday_coupons()
    except Exception as e:
        logger.exception(f"[scheduler] doğum günü job hata: {e}")


# ==================== A2.8: TEK-LİDER (distributed leader lease) ====================
# APScheduler her PROCESS'te çalışır; Railway yatay ölçeklenirse (>1 instance) tüm zamanlı
# işler HER instance'ta tekrar koşar → çift iptal / çift reconcile / çift bildirim. Mongo
# tabanlı kısa süreli lease ile aynı anda YALNIZ bir instance "lider" olur ve işleri o koşar.
# Tek-instance dağıtımda (varsayılan) davranış değişmez. Lease okunamazsa (Mongo hatası)
# tek-instance varsayımıyla iş yine koşar (regresyon yok).
_INSTANCE_ID = str(uuid.uuid4())
_LEASE_TTL_SEC = 120


async def _acquire_or_renew_leadership() -> bool:
    from routes.deps import db  # lazy
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    exp_iso = (now + timedelta(seconds=_LEASE_TTL_SEC)).isoformat()
    # 1) Bize aitse yenile, süresi dolmuşsa devral — tek-döküman atomik koşullu update.
    res = await db.scheduler_leader.update_one(
        {"_id": "scheduler_leader",
         "$or": [{"holder": _INSTANCE_ID}, {"expires_at": {"$lt": now_iso}}]},
        {"$set": {"holder": _INSTANCE_ID, "expires_at": exp_iso, "renewed_at": now_iso}},
    )
    if res.matched_count > 0:
        return True
    # 2) Henüz kayıt yok → atomik oluştur (benzersiz _id iki liderliği engeller).
    try:
        await db.scheduler_leader.insert_one(
            {"_id": "scheduler_leader", "holder": _INSTANCE_ID,
             "expires_at": exp_iso, "renewed_at": now_iso})
        return True
    except Exception:
        return False  # başka instance kaydı oluşturdu/tutuyor


def _lead(fn):
    """İşi yalnız lider instance koşsun diye sarmalar. Lease hatasında tek-instance
    varsayımıyla koşar (regresyon önleme)."""
    async def _w(*a, **k):
        try:
            if not await _acquire_or_renew_leadership():
                return None
        except Exception as _e:
            logger.warning(f"[scheduler] liderlik kontrolü atlandı ({_e}) — iş yine koşuyor")
        return await fn(*a, **k)
    _w.__name__ = getattr(fn, "__name__", "job")
    _w.__qualname__ = _w.__name__
    return _w


async def alert_critical_stock_for_rpt():
    """RPT (tekrar üretim) uyarısı — GÜNDE BİR: son 30 günün satış hızına göre kalan stoğun
    kapsaması eşiğin (varsayılan 4 hafta = 21 gün üretim + 1 hafta güvenlik payı) altına
    düşen AKTİF ürünler için admin push atar. Eşikler İşletme Kuralları'ndan ayarlanır:
      kritik stok = haftalık hız × report.reorder_cover_weeks
    Yalnız hız ≥ report.velocity_yellow_min olan (RPT'ye değer) ürünler uyarılır;
    ürün başına en fazla 7 günde bir tekrarlanır (rpt_alerted_at)."""
    from routes.deps import db
    from routes.reports import _EXCLUDED_STATUSES
    from business_rules import get_rule
    try:
        cover_weeks = float(await get_rule(db, "report.reorder_cover_weeks", 4) or 4)
        min_rate = float(await get_rule(db, "report.velocity_yellow_min", 5) or 5)
    except Exception:
        cover_weeks, min_rate = 4.0, 5.0
    now = datetime.now(timezone.utc)
    s = (now - timedelta(days=30)).isoformat()

    # Son 30 gün satışları — kalem bazında (barkod + pid)
    pipe = [
        {"$match": {"created_at": {"$gte": s}, "status": {"$nin": _EXCLUDED_STATUSES}}},
        {"$unwind": {"path": "$items", "preserveNullAndEmptyArrays": False}},
        {"$group": {"_id": {"bc": {"$toString": {"$ifNull": ["$items.barcode", ""]}},
                            "pid": {"$toString": {"$ifNull": ["$items.product_id", ""]}}},
                    "qty": {"$sum": {"$ifNull": ["$items.quantity", 1]}}}},
    ]
    rows = [r async for r in db.orders.aggregate(pipe)]
    bcs = [r["_id"]["bc"] for r in rows if r["_id"].get("bc")]
    pids = [r["_id"]["pid"] for r in rows if r["_id"].get("pid")]

    by_bc, by_id = {}, {}
    q = {"$or": []}
    if pids:
        q["$or"].append({"id": {"$in": pids}})
    if bcs:
        q["$or"] += [{"barcode": {"$in": bcs}}, {"variants.barcode": {"$in": bcs}}]
    if not q["$or"]:
        return
    async for p in db.products.find(
            {**q, "is_active": True, "is_deleted": {"$ne": True}},
            {"_id": 0, "id": 1, "name": 1, "stock": 1, "variants": 1, "barcode": 1,
             "rpt_alerted_at": 1}):
        variants = p.get("variants") or []
        stock = sum(int(v.get("stock") or 0) for v in variants) if variants else int(p.get("stock") or 0)
        info = {"id": str(p.get("id")), "name": p.get("name") or "", "stock": stock,
                "rpt_alerted_at": p.get("rpt_alerted_at")}
        by_id[info["id"]] = info
        if p.get("barcode"):
            by_bc[str(p["barcode"])] = info
        for v in variants:
            if v.get("barcode"):
                by_bc[str(v["barcode"])] = info

    # Ürün bazında 30 günlük adet topla
    qty_by_pid = {}
    for r in rows:
        info = by_bc.get(r["_id"].get("bc") or "") or by_id.get(r["_id"].get("pid") or "")
        if info:
            qty_by_pid[info["id"]] = qty_by_pid.get(info["id"], 0) + int(r.get("qty") or 0)

    critical = []
    for pid, qty30 in qty_by_pid.items():
        info = by_id.get(pid)
        if not info:
            continue
        rate = qty30 / (30.0 / 7.0)   # haftalık hız
        if rate < min_rate:
            continue                   # yavaş ürün — RPT uyarısına değmez
        threshold = rate * cover_weeks
        if info["stock"] > threshold:
            continue
        # 7 günde birden sık tekrarlama
        try:
            if info.get("rpt_alerted_at") and (now - datetime.fromisoformat(str(info["rpt_alerted_at"]))).days < 7:
                continue
        except Exception:
            pass
        critical.append({"id": pid, "name": info["name"], "stock": info["stock"],
                         "rate": round(rate, 1), "threshold": int(threshold)})

    if not critical:
        return
    critical.sort(key=lambda x: x["stock"] / max(x["rate"], 0.1))  # en acil (en az hafta kalan) önce
    lines = [f"• {c['name'][:40]}: stok {c['stock']} (hız {c['rate']}/hf, eşik ~{c['threshold']})"
             for c in critical[:4]]
    if len(critical) > 4:
        lines.append(f"…ve {len(critical) - 4} ürün daha")
    try:
        from routes.push import send_push_to_admins
        await send_push_to_admins(
            f"🧵 RPT zamanı: {len(critical)} ürün kritik stokta",
            "\n".join(lines),
            {"type": "rpt_alert"})
    except Exception as e:
        logger.warning("[rpt-alert] push gönderilemedi: %s", e)
    for c in critical:
        await db.products.update_one({"id": c["id"]}, {"$set": {"rpt_alerted_at": now.isoformat()}})
    logger.info("[rpt-alert] %d ürün için RPT uyarısı gönderildi", len(critical))


async def _refresh_instagram_token():
    """SINIRSIZ Instagram feed: 60 günlük kullanıcı token'ı ~45 günde bir otomatik yeniden
    exchange edilir (fb_exchange_token → taze 60 gün). Kullanıcı bir daha dokunmaz.
    token_obtained_at 45 günden yeniyse hiçbir şey yapmaz; app_id sayısal değilse (bozuk kayıt) atlar.
    Ödeme/sipariş akışıyla ilgisi yoktur — tamamen bağımsız görev."""
    from routes.deps import db
    try:
        from security.crypto import encrypt, decrypt
    except Exception:
        def encrypt(x): return x
        def decrypt(x): return x
    try:
        s = await db.settings.find_one({"id": "instagram"}, {"_id": 0}) or {}
        tok_enc = s.get("access_token")
        app_id = str(s.get("app_id") or "").strip()
        app_secret_enc = s.get("app_secret")
        obtained = s.get("token_obtained_at")
        if not (tok_enc and app_id and app_secret_enc and obtained) or not app_id.isdigit():
            return
        try:
            _dt = datetime.fromisoformat(obtained)
            if _dt.tzinfo is None:
                _dt = _dt.replace(tzinfo=timezone.utc)
        except Exception:
            return
        if (datetime.now(timezone.utc) - _dt).days < 45:
            return  # henüz erken
        cur_token = decrypt(tok_enc)
        app_secret = decrypt(app_secret_enc)
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get("https://graph.facebook.com/v23.0/oauth/access_token", params={
                "grant_type": "fb_exchange_token", "client_id": app_id,
                "client_secret": app_secret, "fb_exchange_token": cur_token})
        if r.status_code == 200 and (r.json() or {}).get("access_token"):
            new_tok = r.json()["access_token"]
            await db.settings.update_one({"id": "instagram"}, {"$set": {
                "access_token": encrypt(new_tok),
                "token_obtained_at": datetime.now(timezone.utc).isoformat(),
                "token_refreshed_at": datetime.now(timezone.utc).isoformat(),
                "last_error": ""}})
            logger.info("[instagram] uzun ömürlü token otomatik yenilendi (+60 gün)")
        else:
            _t = r.text[:200] if hasattr(r, "text") else str(r.status_code)
            logger.warning(f"[instagram] token yenilenemedi: {_t}")
    except Exception as e:
        logger.warning(f"[instagram] token refresh hata: {e}")


async def _run_visual_index_refresh():
    """WhatsApp görselden-ürün-tanıma hafızasını OTOMATİK doldurur/günceller.
    Yalnız EKSİK (indekslenmemiş) aktif ürünleri işler → ilk çalışmada tümünü kurar,
    sonraki çalışmalarda yeni eklenenleri tamamlar (token/manuel tetik gerekmez)."""
    try:
        from routes.whatsapp_webhook import _run_visual_index_build
        await _run_visual_index_build(force=False)
    except Exception as e:
        logger.error(f"[cron] görsel indeks yenileme hatası: {e}")


def start_scheduler():
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = AsyncIOScheduler(timezone="UTC")

    def _add(fn, *a, **k):
        # A2.8: her iş lider-sarmalıyla eklenir (çok-instance'ta çift çalışmayı önler).
        return _scheduler.add_job(_lead(fn), *a, **k)
    # SINIRSIZ Instagram feed: token'ı günde bir kontrol et, 45 günü geçince otomatik yenile.
    _add(
        _refresh_instagram_token,
        "interval",
        hours=24,
        id="instagram_token_refresh",
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=120),
        max_instances=1,
        coalesce=True,
    )
    # WhatsApp görsel hafızası (görselden ürün tanıma) — OTOMATİK doldur/güncelle.
    # İlk çalışma boot+3dk'da tüm eksikleri kurar; 12 saatte bir yeni ürünleri tamamlar
    # (yalnız indekslenmemişleri işler → tekrar tarama yapmaz, maliyet düşük).
    _add(
        _run_visual_index_refresh,
        "interval",
        hours=12,
        id="wa_visual_index_refresh",
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=180),
        max_instances=1,
        coalesce=True,
    )
    # Run every 30 minutes; catches orders promptly as they cross the 48h mark
    _add(
        auto_cancel_unpaid_havale_orders,
        "interval",
        minutes=30,
        id="auto_cancel_havale_48h",
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=30),
        max_instances=1,
        coalesce=True,
    )
    # KRİTİK: başarısız/ödenmemiş KART siparişleri — 3 saat sonra iptal + stok iadesi.
    # Önceden HİÇ zamanlanmamıştı → başarısız kart ödemeleri stoğu kalıcı sızdırıyordu.
    _add(
        auto_cancel_unpaid_card_orders,
        "interval",
        minutes=30,
        id="auto_cancel_card_24h",
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=45),
        max_instances=1,
        coalesce=True,
    )
    # iyzico OTOMATİK reconcile: para çekilmiş ama 'ödendi' işaretlenmemiş kart siparişlerini
    # her 15 dk'da iyzico'dan doğrulayıp finalize eder (elle recover-charged gerekmez).
    _add(
        reconcile_charged_but_unrecorded_orders,
        "interval",
        minutes=15,
        id="iyzico_reconcile_charged",
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=90),
        max_instances=1,
        coalesce=True,
    )
    # İYS: bildirilmemiş izinleri her 30 dk'da NetGSM'e yeniden gönder. NetGSM modülü
    # aktif olur olmaz (yansıma/aktivasyon gecikmesi sonrası) bekleyenler otomatik geçer.
    _add(
        retry_pending_iys_consents,
        "interval",
        minutes=30,
        id="iys_retry_pending",
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=60),
        max_instances=1,
        coalesce=True,
    )
    # Instagram akışı — auto_sync açık + token varsa her 30 dk @facette gönderilerini tazeler.
    try:
        from routes.instagram import auto_sync_instagram
        _add(
            auto_sync_instagram,
            "interval",
            minutes=30,
            id="instagram_auto_sync",
            next_run_time=datetime.now(timezone.utc) + timedelta(seconds=90),
            max_instances=1,
            coalesce=True,
        )
    except Exception as _e:
        logging.getLogger("scheduler").warning("[scheduler] instagram job eklenemedi: %s", _e)
    # Trendyol yorumları — HAFTADA BİR 4-5 yıldız yorumları otomatik çeker (worker/proxy varsa).
    try:
        from routes.integrations_trendyol_qna import weekly_trendyol_review_sync
        _add(
            weekly_trendyol_review_sync,
            "interval",
            days=7,
            id="trendyol_reviews_weekly",
            next_run_time=datetime.now(timezone.utc) + timedelta(minutes=10),
            max_instances=1,
            coalesce=True,
        )
    except Exception as _e:
        logging.getLogger("scheduler").warning("[scheduler] trendyol yorum job eklenemedi: %s", _e)
    # RPT kritik stok uyarısı — her sabah 05:00 UTC (08:00 TR): kapsaması eşiğin altına
    # düşen ürünler için admin push (21 gün üretim + güvenlik payı; İşletme Kuralları'ndan ayarlı).
    _add(
        alert_critical_stock_for_rpt,
        "cron",
        hour=5, minute=0,
        id="rpt_critical_stock_alert",
        max_instances=1,
        coalesce=True,
    )
    # Marketplace auto-sync tick — her 1 dk'da çalışır, sonra tek tek
    # account'lara ait interval'lere göre ürün/sipariş senkronu planlar.
    # Bu sayede "3 dk'da bir ürün gönder" gibi ince ayarlar çalışır.
    _add(
        _marketplace_sync_tick,
        "interval",
        minutes=1,
        id="marketplace_auto_sync_tick",
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=45),
        max_instances=1,
        coalesce=True,
    )
    # Tek seferlik: Hepsiburada senkronunu 2 dk'ya çek (stok + sipariş). Bayrakla bir kez çalışır.
    _add(
        _ensure_hb_2min_sync,
        "date",
        run_date=datetime.now(timezone.utc) + timedelta(seconds=20),
        id="ensure_hb_2min_sync_once",
    )
    # Trendyol İPTAL HIZLI tarama — her 5 DK (eskiden 60 sn idi; site yavaşlamasının
    # sebebi buydu). Sadece Cancelled + 14g dar pencere → hafif. Yeni iptaller ~5 dk'da düşer.
    _add(
        _run_trendyol_cancel_pass,
        "interval",
        minutes=5,
        id="trendyol_cancel_pass_5m",
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=30),
        max_instances=1,
        coalesce=True,
    )
    # Hepsiburada fatura KALICI oto-yükleme — her 2 DK. Faturası kesilmiş ama HB'ye gitmemiş
    # siparişleri (tüm paketlere) yeniden gönderir → fatura kesildikten en geç ~2 dk sonra gider.
    # İdempotent + hafif (uploaded!=True kümesi sürekli küçülür).
    _add(
        _run_hb_invoice_autoheal,
        "interval",
        minutes=2,
        id="hb_invoice_autoheal_2m",
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=30),
        max_instances=1,
        coalesce=True,
    )
    # Trendyol GENİŞ durum taraması — SAATTE BİR. Cancelled 45g + Returned/UnDelivered 30g.
    # Sık taramanın kaçırdığı eski/geç durum değişikliklerini kapatır (sık değil → hafif).
    _add(
        _run_trendyol_status_wide_pass,
        "interval",
        minutes=60,
        id="trendyol_status_wide_60m",
        next_run_time=datetime.now(timezone.utc) + timedelta(minutes=3),
        max_instances=1,
        coalesce=True,
    )
    # Trendyol claims (iade/iptal) senkronu — her 30 DK. Müşterinin seçtiği GERÇEK iptal/iade
    # sebebini çeker ve claim'leri eşleşen iptal siparişlerine bağlar.
    _add(
        _run_trendyol_claims_sync,
        "interval",
        minutes=30,
        id="trendyol_claims_sync_30m",
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=120),
        max_instances=1,
        coalesce=True,
    )
    # AÇIK iade kovaları CANLI eşitleme — DAKİKADA BİR (kullanıcı isteği): Talep Oluşturulan /
    # Kargoya Verilen / Aksiyon Bekleyen sayıları TY panelle aynı; kapanan claim Onaylanan/
    # Reddedilen'e anında taşınır. Hafif iş: statü filtreli birkaç sayfa + artık başına tekil sorgu.
    _add(
        _run_trendyol_open_claims_refresh,
        "interval",
        minutes=1,
        id="trendyol_open_claims_1m",
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=90),
        max_instances=1,
        coalesce=True,
    )
    # Hepsiburada claims (iade) senkronu — her 30 DK (Trendyol ile simetrik).
    _add(
        _run_hepsiburada_claims_sync,
        "interval",
        minutes=30,
        id="hepsiburada_claims_sync_30m",
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=150),
        max_instances=1,
        coalesce=True,
    )
    # Trendyol claims TEK SEFERLİK derin backfill (3 yıl) — geçmiş onaylı/reddedilen iadeler
    # de sekme sayılarına insin. Flag korumalı (trendyol_claims_deep_backfill.done) → bir kez.
    _add(
        _run_trendyol_claims_deep_backfill_once,
        "interval",
        hours=6,
        id="trendyol_claims_deep_backfill_once",
        next_run_time=datetime.now(timezone.utc) + timedelta(minutes=6),
        max_instances=1,
        coalesce=True,
    )
    # Trendyol TEK SEFERLİK terminal backfill — geçmişteki TÜM iptalleri (satıcı dahil) taşır.
    # Flag korumalı (db.settings.trendyol_terminal_backfill.done) → bir kez çalışır, sonra no-op.
    # 3 saatte bir tetiklenir ama done ise hiçbir şey yapmaz (sadece ucuz flag kontrolü).
    _add(
        _run_trendyol_deep_backfill_once,
        "interval",
        hours=3,
        id="trendyol_deep_backfill_once",
        next_run_time=datetime.now(timezone.utc) + timedelta(minutes=4),
        max_instances=1,
        coalesce=True,
    )
    # O12: "Günde bir" işleri SABİT SAATLİ cron ile çalıştır (07:00 UTC ≈ 10:00 İstanbul).
    # Önceki `interval hours=24 + next_run_time=now+2dk` her PROCESS RESTART'ında çalışıyor ve
    # düşük-stok e-postası sent-flag'i olmadığından her deploy'da MÜKERRER mail gidiyordu; ayrıca
    # "günlük" saat her restart'ta kayıyordu. cron ile gün içinde tam olarak bir kez tetiklenir.
    _add(
        _send_abandoned_cart_reminders,
        "cron",
        hour=7, minute=0,
        id="abandoned_cart_reminders",
        max_instances=1,
        coalesce=True,
    )
    _add(
        _send_daily_stock_alert,
        "cron",
        hour=7, minute=10,
        id="daily_stock_alert",
        max_instances=1,
        coalesce=True,
    )
    # Favori ürün tekrar stokta — her 30 dk favorilenen ürünlerin stoğunu kontrol eder;
    # 'yok'→'var' geçişinde o ürünü favorileyenlere markalı e-posta atar (döngü başına 1 kez).
    _add(
        _notify_back_in_stock,
        "interval",
        minutes=30,
        id="wishlist_back_in_stock",
        next_run_time=datetime.now(timezone.utc) + timedelta(minutes=2),
        max_instances=1,
        coalesce=True,
    )
    # Ticimax site siparişleri periyodik çekme — KAPALI (kullanıcı kararı: Ticimax ile
    # aktif senkron yok; Ticimax'tan sipariş çekilmez/stok güncellenmez). Stok senkronu
    # zaten kapalıydı; sipariş çekme senkronu da bu sistemi etkilemesin diye kapatıldı.
    # Gerekirse tekrar açmak için aşağıdaki _add(...) bloğunu geri yorumdan çıkarın.
    # _add(
    #     _ticimax_sync_orders,
    #     "interval",
    #     hours=6,
    #     id="ticimax_orders_sync",
    #     next_run_time=datetime.now(timezone.utc) + timedelta(minutes=5),
    #     max_instances=1,
    #     coalesce=True,
    # )
    # Ticimax canlı stok senkronu — KAPALI (Facette stok master'ı; Ticimax stoğu
    # bu sistemi EZMESİN). Kullanıcı kararı: stok yalnızca sipariş/iptal/iade ile
    # Facette içinde yönetilir. Gerekirse settings.ticimax_stock_sync_enabled=true
    # yapıp elle tetiklenebilir, ama otomatik job artık çalışmaz.
    # _scheduler.add_job(
    #     _ticimax_sync_stock, "interval", hours=2, id="ticimax_stock_sync",
    #     next_run_time=datetime.now(timezone.utc) + timedelta(minutes=10),
    #     max_instances=1, coalesce=True,
    # )
    # Iter 43 — Günlük stok tükenme uyarısı (her gün sabah 9:00 UTC, ~12:00 TR)
    async def _daily_stockout_alert():
        try:
            from routes.production_hooks import send_stockout_alert_email
            class _SystemAdmin:
                def get(self, k, *a): return "system@facette.com" if k == "email" else None
            await send_stockout_alert_email(admin=_SystemAdmin())
            logger.info("[scheduler] daily stockout alert sent")
        except Exception as e:
            logger.warning(f"[scheduler] stockout alert failed: {e}")
    from apscheduler.triggers.cron import CronTrigger
    _add(
        _daily_stockout_alert,
        CronTrigger(hour=9, minute=0),
        id="daily_stockout_alert",
        max_instances=1, coalesce=True,
    )
    # Amazon DPP — PII saklama süresi dolan siparişlerde kişisel verileri anonimleştir
    # (her gün 03:00 UTC). Amazon "Restricted" rol uyumu için kritik kontrol.
    _add(
        _pii_retention_purge,
        CronTrigger(hour=3, minute=0),
        id="pii_retention_purge",
        next_run_time=datetime.now(timezone.utc) + timedelta(minutes=3),
        max_instances=1, coalesce=True,
    )
    # DHL/MNG kargo durum taramasi — her 30 dk (site siparisleri; takip linki -> Kargoya Verildi, teslim -> Teslim Edildi)
    _add(
        _dhl_cargo_poll_tick,
        "interval",
        minutes=30,
        id="dhl_cargo_poll",
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=60),
        max_instances=1,
        coalesce=True,
    )
    _add(
        _return_cargo_poll_tick,
        "interval",
        minutes=5,
        id="return_cargo_poll",
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=90),
        max_instances=1,
        coalesce=True,
    )
    # Bölüm C — Referans ödülleri (davet edilenin ilk siparişi geldiyse) her 20 dk.
    _add(
        _run_award_referrals,
        "interval",
        minutes=20,
        id="referral_award",
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=120),
        max_instances=1,
        coalesce=True,
    )
    # C3 — Sadakat puanları: ödenmiş siparişlere puan + iptalde geri alma, her 30 dk.
    _add(
        _run_award_loyalty,
        "interval",
        minutes=30,
        id="loyalty_award",
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=180),
        max_instances=1,
        coalesce=True,
    )
    # Bölüm C — Doğum günü kuponları: her gün 07:00 UTC (~10:00 TR).
    _add(
        _run_birthday_coupons,
        "cron",
        hour=7,
        minute=0,
        id="birthday_coupons",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    logger.info("[scheduler] Background scheduler started (auto-cancel every 30 min + marketplace auto-sync every 1 min + abandoned cart reminders daily + Ticimax orders every 6h)")
    return _scheduler


def shutdown_scheduler():
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            pass
        _scheduler = None
