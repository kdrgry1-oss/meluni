"""
=============================================================================
rooftr_returns.py — Site / iade & kısmi iade SİPARİŞLERİ (İadeler sayfası)
=============================================================================
Ticimax sipariş import'u (integrations.py /ticimax/orders/import) iade ve kısmi
iade durumundaki siparişleri `orders` koleksiyonuna `platform="ticimax"` +
status ∈ {returned, partial_refunded, refunded, return_*} olarak yazıyor.

Bu modül o siparişleri İadeler sayfasında göstermek için listeler — ödeme tipi,
durum ve tüm detayla. Durum değiştirme mevcut `PUT /api/orders/{id}/status`
endpoint'i ile yapılır (bildirim de oradan gider), burada ayrıca tanımlanmaz.

Endpoint'ler (full path):
  GET  /api/admin/rooftr/return-orders     → iade siparişleri listesi + istatistik
=============================================================================
"""
from fastapi import APIRouter, Query, Depends, HTTPException, Request
from typing import Optional
import re

from .deps import db, logger, require_admin, require_permission, generate_id, _search_tr_regex, tr_range_to_utc
from activity_audit import record_admin_audit
from .orders import _order_vade_farki, _order_is_efatura, _compute_refund_breakdown
from .orders import present_refund_bank_info_in

router = APIRouter(prefix="/admin/rooftr", tags=["rooftr-returns"])

# İade sürecindeki tüm sipariş durumları (order_statuses.py "İade" grubu)
RETURN_STATUSES = [
    "return_requested", "return_approved", "return_rejected",
    "return_in_transit", "returned", "refunded", "partial_refunded",
]

# Ödeme tipi kodu → okunabilir Türkçe etiket
PAYMENT_LABELS = {
    "bank_transfer": "Havale / EFT",
    "credit_card": "Kredi Kartı",
    "cash_on_delivery": "Kapıda Ödeme",
    "cod_card": "Kapıda Kredi Kartı",
    "ticimax": "Diğer",
}


def _payment_label(method: str, raw: str = "") -> str:
    lbl = PAYMENT_LABELS.get(method or "", "")
    if lbl and lbl != "Diğer":
        return lbl
    # Bilinmeyen ama ham metin varsa onu göster
    return (raw or "").strip() or lbl or "Bilinmiyor"



def _iade_tarih_filtresi(start_date: Optional[str], end_date: Optional[str]) -> dict:
    """Üstteki 'İADE ONAY BAŞ./BİT.' filtresini Mongo koşuluna çevirir.

    Panelde tarih seçildiğinde alttaki liste DEĞİŞMİYORDU: filtre yalnız toplu gider
    pusulası bölümünü besliyor, liste ucu tarih parametresi hiç almıyordu. Bu yüzden
    ekranda "Toplam 1776 iade siparişi" gibi dönemden bağımsız bir sayı duruyordu.

    Ölçüt ekrandaki 'İade Onay/Ret' sütunuyla AYNI alandır: return_approved_at.
    Onay damgası olmayan (henüz karar verilmemiş) iadeler tarih aralığı verildiğinde
    listeye GİRMEZ — aksi halde "şu tarihte onaylananlar" sorusu cevaplanamaz.
    Tarih verilmezse davranış eskisi gibi: hiçbir tarih süzgeci uygulanmaz.
    """
    if not start_date and not end_date:
        return {}
    try:
        s_iso, e_iso = tr_range_to_utc(start_date or end_date, end_date or start_date)
    except Exception:
        return {}
    # İŞLEM TARİHİ = kararın verildiği an; karar yoksa talebin açıldığı an.
    # Yalnız onay tarihine bakılsaydı "Talep Oluşturulan" sekmesi tarih seçilince
    # tamamen boşalırdı (henüz onaylanmamış iadenin onay tarihi yoktur).
    _eff = {"$ifNull": ["$return_approved_at",
                        {"$ifNull": ["$return_request.created_at", "$updated_at"]}]}
    return {"$expr": {"$and": [{"$gte": [_eff, s_iso]}, {"$lte": [_eff, e_iso]}]}}

@router.get("/return-orders")
async def list_rooftr_return_orders(
    status: Optional[str] = Query(None, description="Tek durum filtresi (örn. partial_refunded). Boş = tüm iade durumları"),
    payment: Optional[str] = Query(None, description="Ödeme tipi filtresi (bank_transfer/credit_card/cash_on_delivery)"),
    search: Optional[str] = Query(None, description="Sipariş no / müşteri adı / telefon araması"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=10000),  # frontend TÜMÜNÜ çeker (client-side sırala+sayfala)
    start_date: Optional[str] = Query(None, description="İade ONAY tarihi başlangıcı (TR günü, YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="İade ONAY tarihi bitişi (TR günü, YYYY-MM-DD)"),
    current_user: dict = Depends(require_permission("returns.view")),
):
    """Ticimax kaynaklı iade / kısmi iade siparişlerini listeler.

    Veri kaynağı: orders koleksiyonu, platform/source = ticimax ve
    status ∈ İade grubu. Her satır ödeme tipi (kod + okunabilir etiket) ve
    durumla döner; durumu değiştirmek için frontend PUT /api/orders/{id}/status
    çağırır.
    """
    # "Web Sitesi" iadeleri = pazaryeri (Trendyol/Hepsiburada) DISI tum siparisler.
    # Eski hali yalnizca platform/source=ticimax idi -> yeni site siparisleri (platform=meluni)
    # iade/iptal edilince hicbir sekmede gorunmuyordu. Pazaryeri disi her kaynak (meluni + ticimax + bos) dahil.
    # Web Sitesi iadeleri = YALNIZCA pazaryeri (Trendyol/HB) DIŞI siparişler.
    # Pazaryeri siparişleri (elle iade durumuna çekilse bile) BURAYA DÜŞMEZ; kendi
    # pazaryeri sekmesinde (Trendyol/Hepsiburada) listelenir — 'ait olduğu panel' kuralı.
    base_filter = {
        "platform": {"$nin": ["trendyol", "hepsiburada"]},
    }
    base_filter.update(_iade_tarih_filtresi(start_date, end_date))

    # Durum filtresi: tek durum verilmişse onu, yoksa tüm iade grubunu kullan
    if status:
        # Tek veya virgülle ayrılmış çoklu durum (örn. "refunded,partial_refunded" = 5. İade Ödemeleri hanesi)
        _st = [s.strip() for s in status.split(",") if s.strip()]
        base_filter["status"] = {"$in": _st} if len(_st) > 1 else (_st[0] if _st else {"$in": RETURN_STATUSES})
    else:
        base_filter["status"] = {"$in": RETURN_STATUSES}

    if payment:
        _pm = [x.strip() for x in str(payment).split(",") if x.strip()]
        base_filter["payment_method"] = {"$in": _pm} if len(_pm) > 1 else (_pm[0] if _pm else payment)

    if search and search.strip():
        _fields = ["order_number", "order_code", "gider_pusulasi_no",
                   "shipping_address.first_name", "shipping_address.last_name",
                   "shipping_address.full_name", "shipping_address.name",
                   "shipping_address.phone", "shipping_address.email",
                   "customer_name", "full_name"]
        # GİDER PUSULASI NO ile arama (Kadir): girilen no display_number/no ile eşleşen
        # gider_pusulasi kayıtlarından order_number/return_id topla → aramaya OR olarak ekle.
        _gp_onums = set()
        _gp_rids = set()
        try:
            _gp_rx = {"$regex": re.escape(search.strip()), "$options": "i"}
            async for _g in db.gider_pusulasi.find(
                    {"$or": [{"display_number": _gp_rx}, {"number": _gp_rx}]},
                    {"_id": 0, "order_number": 1, "return_id": 1}):
                if _g.get("order_number"):
                    _gp_onums.add(str(_g["order_number"]))
                if _g.get("return_id"):
                    _gp_rids.add(str(_g["return_id"]))
        except Exception:
            pass
        # return_id → order_id (customer_returns köprüsü) → order_number havuzuna ekle.
        if _gp_rids:
            async for _cr in db.customer_returns.find(
                    {"id": {"$in": list(_gp_rids)}}, {"_id": 0, "order_number": 1}):
                if _cr.get("order_number"):
                    _gp_onums.add(str(_cr["order_number"]))
        _gp_clause = [{"order_number": {"$in": list(_gp_onums)}}] if _gp_onums else []
        _words = [w for w in search.strip().split() if w]
        if len(_words) <= 1:
            rx = {"$regex": _search_tr_regex(search.strip()), "$options": "i"}
            base_filter["$and"] = [{"$or": [{f: rx} for f in _fields] + _gp_clause}]
        else:
            # ÇOK KELİMELİ AD-SOYAD: her kelime herhangi bir alanda geçmeli (sıra önemsiz).
            # 'büşra çe' → 'büşra' first_name'de, 'çe' last_name'de eşleşir → bulunur.
            # (Eskiden tüm ifade tek regex'ti; ad ile soyad AYRI alanlarda olduğundan hiç
            #  eşleşmiyordu → arama bozuluyordu.) Türkçe İ/ı duyarsız (_search_tr_regex).
            base_filter["$and"] = [
                {"$or": [{f: {"$regex": _search_tr_regex(w), "$options": "i"}} for f in _fields]}
                for w in _words
            ]

    total = await db.orders.count_documents(base_filter)

    proj = {
        "_id": 0, "id": 1, "order_number": 1, "order_code": 1, "ticimax_order_id": 1,
        "status": 1, "payment_method": 1, "payment_method_raw": 1, "payment_status": 1,
        "total": 1, "paid_amount": 1, "subtotal": 1, "shipping_cost": 1, "discount": 1,
        "payment_discount": 1,  # havale/EFT ödeme indirimi — iade net hesabı için ŞART (yoksa 0 gelip 4001 kalır)
        "coupon_code": 1, "notes": 1, "shipping_address": 1, "billing_address": 1,
        "customer_name": 1, "full_name": 1, "items": 1,
        "created_at": 1, "updated_at": 1, "channel_source": 1, "invoice_number": 1,
        "return_approved_at": 1, "refund_paid_at": 1, "return_request": 1,
        "cargo_tracking_number": 1, "cargo_tracking_url": 1, "cargo_provider_name": 1,
        "iyzico_retrieve_response": 1, "installment": 1, "admin_notes": 1,
        "invoice_type": 1, "billing_info": 1, "free_shipping_threshold": 1,
    }

    cursor = (
        db.orders.find(base_filter, proj)
        # En güncel iade HAREKETİ en üstte: updated_at (son değişiklik) öncelikli, sonra created_at.
        .sort([("updated_at", -1), ("created_at", -1)])
        .skip((page - 1) * limit)
        .limit(limit)
    )

    rows = []
    async for o in cursor:
        addr = o.get("shipping_address") or {}
        bill = o.get("billing_address") or {}
        name = (" ".join([addr.get("first_name") or "", addr.get("last_name") or ""]).strip()
                or addr.get("full_name") or addr.get("name")
                or o.get("customer_name") or o.get("full_name")
                or " ".join([bill.get("first_name") or "", bill.get("last_name") or ""]).strip()
                or bill.get("name") or "—")
        items = o.get("items") or []
        # Brüt / iskonto order-seviyesinde yoksa kalemlerden türet (unit_price=brüt, price=net).
        _calc_gross = sum(round(float(i.get("unit_price") or i.get("list_price") or i.get("original_price") or i.get("price") or 0), 2) * int(i.get("quantity") or 1) for i in items)
        _calc_net = sum(round(float(i.get("price") or 0), 2) * int(i.get("quantity") or 1) for i in items)
        _calc_idisc = sum(round(float(i.get("discount_amount") or i.get("discount") or 0), 2) * int(i.get("quantity") or 1) for i in items)
        _o_sub = float(o.get("subtotal") or 0)
        _o_disc = float(o.get("discount") or 0)
        _o_total = float(o.get("total") or 0)
        _r_total = _o_total if _o_total > 0 else round(_calc_net, 2)
        _r_subtotal = _o_sub if _o_sub > 0 else round(_calc_gross, 2)
        _r_discount = _o_disc if _o_disc > 0 else (round(_calc_idisc, 2) if _calc_idisc > 0 else round(max(0.0, _r_subtotal - _r_total), 2))
        # Taksitli ödemede iyzico'ya gerçekte tahsil edilen tutar (vade farkı DAHİL) `total`'dan
        # yüksek olabilir — iade onayında baz alınan budur (bkz. orders.py _compute_refund_breakdown).
        # Burada da gösterip admin'in panelde önceden göreceği rakamla onay sırasında hesaplanan
        # rakam tutarlı olsun diye sunuyoruz (Kadir talebi: taksitli siparişte tüm tahsilat iade edilmeli).
        _vf, _charged, _inst = _order_vade_farki(o)
        # KDV-DAHİL TABAN DÜZELTMESİ (Ali Al 398MD4734D → tüm iadelere uygulanır): bazı içe-aktarılan
        # (Ticimax) siparişlerde item.price KDV-HARİÇ ama subtotal/total KDV-DAHİL → panelde kalem
        # KDV-hariç görünüp ara toplamla tutmuyordu. Siparişin KENDİ order-seviyesi KDV-dahil verisinden
        # faktör türet ve bir KDV oranına yakınsa (1.06–1.24) TÜM kalem fiyatlarını + ara toplamı
        # KDV-dahile ölçekle. İKİ aday: (a) subtotal−indirim, (b) total−kargo−vade farkı — hangisi
        # KDV oranı penceresine düşerse o kullanılır (subtotal boş olan mükerrer kayıtlarda bile çalışır).
        # Normal (zaten KDV-dahil item.price) siparişler etkilenmez; faktör 1.0 kalır.
        # KDV faktörü = KDV-DAHİL LİSTE (indirim ÖNCESİ) / KDV-HARİÇ LİSTE. İndirim AYRI bir
        # order-seviyesi kalemdir (item.price'a yansımaz) → faktörden ÇIKARILMAZ. Aksi halde
        # indirimli siparişte faktör bozuluyordu (Senem Birdal 952IF2376A: (985.15−98.52)/895.59
        # = 0.99 → grossing yapılmıyor, kalem 895.59 KDV-hariç kalıyordu; doğrusu 985.15/895.59=1.10).
        _paid_factor = 1.0
        if _calc_net > 0.5:
            _cands = []
            if _r_subtotal > 0.5:
                _cands.append(_r_subtotal / _calc_net)
            # total-tabanlı yedek: indirimi GERİ ekle → indirim-öncesi KDV-dahil taban.
            # HER İKİ indirim (kupon _r_discount + havale/EFT payment_discount) geri eklenir ki
            # taban gerçek subtotal'a eşitlensin (orders.py 8104/7756 ile tutarlı).
            _tot_h = (_r_total - float(o.get("shipping_cost") or 0)
                      - (round(_vf, 2) if _vf > 0 else 0.0)
                      + _r_discount + float(o.get("payment_discount") or 0))
            if _tot_h > 0.5:
                _cands.append(_tot_h / _calc_net)
            for _pf in _cands:
                if 1.06 <= _pf <= 1.24:
                    _paid_factor = _pf
                    break
        # Ara toplam/indirim order-seviyesinde YOKSA kalemlerden türetilmişti (KDV-hariç);
        # faktör tetiklendiyse bunları da KDV-dahile ölçekle ki panelde "Ara toplam" kalem
        # fiyatlarıyla tutsun. order.subtotal MEVCUTSA (zaten KDV-dahil) dokunma.
        if _paid_factor != 1.0:
            if _o_sub <= 0.5:
                _r_subtotal = round(_calc_gross * _paid_factor, 2)
            if _o_disc <= 0.5 and _calc_idisc > 0.5:
                _r_discount = round(_calc_idisc * _paid_factor, 2)
            if _o_total <= 0.5:
                _r_total = round(_calc_net * _paid_factor, 2)
        # VADE FARKI YALNIZ GERÇEK TAKSİTTE (installment>1 VE _vf>0). Peşin/1-taksitte vade farkı
        # OLMAZ. Bozuk paid_amount (Doğa Altaş 754DL4121K: peşin ama paid_amount=7084=2×total) yüzünden
        # charged−total hayalet vade farkı üretmesin → inst<=1 ise charged=total (fark 0).
        _real_vade = (int(_inst or 1) > 1 and _vf > 0.01)
        _charged_total = round(_charged, 2) if (_real_vade and _charged > _r_total + 0.01) else _r_total
        _vade_farki_out = round(_vf, 2) if _real_vade else 0.0
        rows.append({
            "id": o.get("id"),
            "order_number": o.get("order_number"),
            "ticimax_order_id": o.get("ticimax_order_id"),
            "customer_name": name,
            "phone": addr.get("phone") or "",
            "email": addr.get("email") or "",
            "address": addr.get("address") or "",
            "city": addr.get("city") or "",
            "district": addr.get("district") or "",
            "status": o.get("status"),
            "payment_method": o.get("payment_method") or "",
            "payment_label": _payment_label(o.get("payment_method") or "", o.get("payment_method_raw") or ""),
            "payment_method_raw": o.get("payment_method_raw") or "",
            "payment_status": o.get("payment_status") or "",
            "total": _r_total,
            "paid_amount": o.get("paid_amount") or 0,
            "charged_total": _charged_total,
            "vade_farki": _vade_farki_out,
            "installment": int(_inst or 1),
            "subtotal": _r_subtotal,
            "shipping_cost": o.get("shipping_cost") or 0,
            "discount": _r_discount,
            # Havale/EFT (veya başka ödeme-yöntemi) indirimi — sipariş-seviyesi, `discount` tan
            # AYRI tutulur. İade net hesabı bunu da düşmeli (gider pusulası da discount+payment_discount
            # kullanıyor). Yoksa yalnız kupon düşülüp havale %5 kaçıyordu (4001 yerine 3801 olmalı).
            "payment_discount": float(o.get("payment_discount") or 0),
            "reason": (o.get("return_request") or {}).get("reason") or "",
            # Havale/EFT iadesinde müşterinin girdiği IBAN + ad soyad (para bu hesaba iade edilir).
            # Panel bu alanı okuyup sebep sütununun altında sarı kutuda gösterir — satıra eklenmiyordu.
            "refund_bank_info": (o.get("return_request") or {}).get("refund_bank_info") or o.get("refund_bank_info") or None,
            "coupon_code": o.get("coupon_code") or "",
            "notes": o.get("notes") or "",
            # Personel (admin) notlari — siparis ekranindan girilen notlar iade panelinde de gorunsun.
            "staff_notes": [
                {"text": (n or {}).get("text") or "", "by": (n or {}).get("by") or "", "at": (n or {}).get("at") or ""}
                for n in (o.get("admin_notes") or []) if (n or {}).get("text")
            ],
            "item_count": sum(int(i.get("quantity") or 1) for i in items),
            # Kalemlerde DONMUŞ per-ürün indirim (checkout) var mı? Varsa panel/gider-pusulası düz-
            # oransal dağıtım yerine bu değeri kullanır (W11214: kapsam-dışı ürüne hayali indirim yok).
            "frozen_item_discounts": bool(items) and all(isinstance(i, dict) and ("discount_amount" in i) for i in items),
            "items": [
                {
                    "name": i.get("product_name") or i.get("name") or "",
                    "qty": i.get("quantity") or 1,
                    "size": i.get("size") or "",
                    "color": i.get("color") or "",
                    "barcode": i.get("barcode") or "",
                    # KDV-dahil faktörüyle ölçekle → panel kalem fiyatları ara toplamla tutar.
                    "price": round(float(i.get("price") or 0) * _paid_factor, 2),
                    "unit_price": round(float(i.get("unit_price") or i.get("list_price") or i.get("price") or 0) * _paid_factor, 2),
                    "discount": round(float(i.get("discount_amount") or i.get("discount") or 0) * _paid_factor, 2),
                }
                for i in items
            ],
            "invoice_number": o.get("invoice_number") or "",
            "is_efatura": _order_is_efatura(o),
            "free_shipping_threshold": o.get("free_shipping_threshold"),
            "created_at": o.get("created_at") or "",
            "updated_at": o.get("updated_at") or "",
            "return_approved_at": o.get("return_approved_at") or "",
            "refund_paid_at": o.get("refund_paid_at") or "",
            "cargo_tracking_number": o.get("cargo_tracking_number") or "",
            "cargo_tracking_url": o.get("cargo_tracking_url") or "",
            "cargo_provider_name": o.get("cargo_provider_name") or "",
        })

    # KALEM BEDENİ: bazı site siparişlerinde item.size boş → barkodu ürün kataloğundaki
    # varyant bedeniyle zenginleştir (kullanıcı isteği: 'bedenleri gelmemiş, getir').
    _need_bc = list({str(it.get("barcode") or "").strip()
                     for r in rows for it in (r.get("items") or [])
                     if str(it.get("barcode") or "").strip() and not str(it.get("size") or "").strip()})
    if _need_bc:
        _bsize = {}
        async for _p in db.products.find({"variants.barcode": {"$in": _need_bc}}, {"_id": 0, "variants": 1}):
            for _v in (_p.get("variants") or []):
                _vbc = str(_v.get("barcode") or "").strip()
                if _vbc and _vbc not in _bsize:
                    _bsize[_vbc] = _v.get("size") or _v.get("beden") or ""
        for r in rows:
            for it in (r.get("items") or []):
                if not str(it.get("size") or "").strip():
                    _sz = _bsize.get(str(it.get("barcode") or "").strip())
                    if _sz:
                        it["size"] = _sz

    # İlgili customer_returns köprü kayıtları (kargo barkodu / iade kodu / reship / ödeme zamanı) — tek sorgu
    _oids = [r["id"] for r in rows if r.get("id")]
    _cr_map = {}
    if _oids:
        # MÜKERRER kayıt güvenliği: siparişe ait TÜM kayıtları topla, kanonik olanı seç
        # (_pick_return_record — /open ile AYNI kural). Eskiden 'son gelen kazanır' idi.
        _cr_all = {}
        async for cr in db.customer_returns.find(
            {"order_id": {"$in": _oids}},
            {"_id": 0, "order_id": 1, "return_code": 1, "barcode_url": 1, "cargo_provider_name": 1,
             "iade_no": 1, "gonderi_no": 1, "mng_ref": 1, "contract_no": 1,
             "reship_code": 1, "reshipped_at": 1, "refund_payment": 1, "reason": 1,
             "refund_bank_info": 1,
             "has_gider_pusulasi": 1, "gider_pusulasi_no": 1,
             "status": 1, "approval": 1, "rejection": 1, "approved_item_indexes": 1, "approved_items": 1,
             "refund_breakdown": 1, "items": 1, "created_at": 1, "updated_at": 1},
        ):
            _cr_all.setdefault(cr.get("order_id"), []).append(cr)
        for _k, _lst in _cr_all.items():
            _cr_map[_k] = _pick_return_record(_lst) or {}
    for r in rows:
        cr = _cr_map.get(r["id"]) or {}
        if cr.get("reason"):
            r["reason"] = cr["reason"]
        if cr.get("refund_bank_info"):
            r["refund_bank_info"] = cr["refund_bank_info"]
        r["return_code"] = cr.get("return_code") or ""
        r["iade_no"] = cr.get("iade_no") or cr.get("mng_ref") or ""
        r["gonderi_no"] = cr.get("gonderi_no") or ""
        r["contract_no"] = cr.get("contract_no") or "490059279"
        r["return_barcode_url"] = cr.get("barcode_url") or ""
        r["return_cargo_provider"] = cr.get("cargo_provider_name") or r.get("cargo_provider_name") or ""
        r["reship_code"] = cr.get("reship_code") or ""
        r["reshipped_at"] = cr.get("reshipped_at") or ""
        r["has_gider_pusulasi"] = bool(cr.get("has_gider_pusulasi"))
        r["gider_pusulasi_no"] = cr.get("gider_pusulasi_no") or ""
        # ONAY GEÇMİŞİ: hangi kalemler onaylanmış + kargo mahsubu — approved satırlarda
        # kutucuklar bu seçimle ÖNİŞARETLİ ve READ-ONLY gösterilir (yalnız muhasebe/admin
        # "Düzenle" ile açar). approved_item_indexes yoksa (tam onay) tüm kalemler onaylı sayılır.
        _cr_status = str(cr.get("status") or "")
        _appr = cr.get("approval") or {}
        _is_approved = bool(_appr) or _cr_status in ("approved", "return_approved", "refunded", "partial_refunded") \
            or r.get("status") in ("return_approved", "refunded", "partial_refunded")
        r["return_is_approved"] = _is_approved
        # KALEM ONAYI GERÇEĞİ (W11262 — "bu iade zaten onaylanmış" ama onaylanmamıştı): yalnız
        # customer_returns KAYDI esas alınır; sipariş durumu/damgası (sessiz durum düzeltmesi,
        # pazaryeri senkronu) tek başına kalem onayı SAYILMAZ. Panel "İade Onay" kilidini buna bağlar.
        r["record_status"] = _cr_status
        # KARAR GERÇEĞİ: yalnız gerçek karar nesnesi (approval/rejection) ya da kapanmış durum
        # (refunded/partial_refunded/cancelled) kilitler. ÇIPLAK "approved" bayrağı (köprü kaydı
        # sipariş durumundan türetilirken ya da durum menüsünden yazılmış, kalem onayı YOK) kilitlemez.
        _dec, _dkind, _, _ = _decision_of(cr)
        r["items_approved"] = bool(_dec and _dkind == "approved")
        # KRİTİK: panel r["items"] = SİPARİŞİN TÜM kalemleri; customer_returns.items = GERÇEKTE
        # İADE EDİLEN kalemler (alt küme olabilir). "Tam onay" = iadenin TÜM kalemleri, siparişin
        # tümü DEĞİL. W10205: 2 kalemli siparişte yalnız M iade edildi; eski kod range(2) yazıp
        # her ikisini onaylı gösteriyordu. Burada iade kalemlerini sipariş-index'ine eşliyoruz.
        _order_items = r.get("items") or []
        _ret_items = cr.get("items") or []

        def _match_order_idx(ri):
            _bc = str(ri.get("barcode") or ri.get("product_id") or "").strip()
            if _bc:
                for _i, _oi in enumerate(_order_items):
                    if str(_oi.get("barcode") or "").strip() == _bc:
                        return _i
            _nm = str(ri.get("name") or "").strip().lower()
            _sz = str(ri.get("size") or "").strip().lower()
            for _i, _oi in enumerate(_order_items):
                if str(_oi.get("name") or "").strip().lower() == _nm and \
                   str(_oi.get("size") or "").strip().lower() == _sz:
                    return _i
            # beden bilgisi yoksa yalnız ada göre (tek eşleşme varsa)
            _cands = [_i for _i, _oi in enumerate(_order_items)
                      if str(_oi.get("name") or "").strip().lower() == _nm]
            return _cands[0] if len(_cands) == 1 else None

        _ap_idx = cr.get("approved_item_indexes")
        if _is_approved:
            _partial_recorded = isinstance(_ap_idx, list) and bool(_ap_idx)
            # Onaylanan İADE kalemleri (kısmi onayda alt küme; yoksa iadenin tümü)
            if _partial_recorded:
                _appr_ret = [_ret_items[i] for i in _ap_idx if 0 <= i < len(_ret_items)]
            else:
                _appr_ret = _ret_items
            _idxs = []
            for _ri in _appr_ret:
                _mi = _match_order_idx(_ri)
                if _mi is not None and _mi not in _idxs:
                    _idxs.append(_mi)
            # DÜŞ: iade kaydı boş (cr.items yok) ya da eşlenemedi AMA iade ONAYLI ve KISMİ kaydı YOK
            # → TAM onay say, TÜM sipariş kalemlerini onaylı işaretle. Böylece geçmişte onaylanmış
            # (fakat köprü kalemleri boş) iadelerde "0 kalem onaylandı" yerine tikler görünür
            # (Senem Birdal 952IF2376A). Kısmi kaydı OLAN ama eşleşmeyen iadede yanlışlıkla
            # hepsini işaretlemeyiz (boş bırakılır).
            if not _idxs and not _partial_recorded and _order_items:
                _idxs = list(range(len(_order_items)))
            r["approved_item_indexes"] = _idxs
            r["approved_full"] = bool(_idxs) and len(_idxs) == len(_order_items)
        else:
            r["approved_item_indexes"] = []
            r["approved_full"] = False
        # Kargo mahsubu onayda uygulandı mı? refund_breakdown.cargo.mode=="deducted"
        _rb_cargo = (cr.get("refund_breakdown") or {}).get("cargo") or {}
        r["approved_cargo_deducted"] = bool(_is_approved and _rb_cargo.get("mode") == "deducted")
        r["approval_by"] = _appr.get("by") or ""
        r["approval_at"] = _appr.get("at") or r.get("return_approved_at") or ""
        r["approval_note"] = _appr.get("note") or ""
        # İade ödeme tarihi: sipariş damgası > köprü ödeme zamanı > (refunded ise) updated_at
        if not r.get("refund_paid_at"):
            _rp = (cr.get("refund_payment") or {}).get("at")
            r["refund_paid_at"] = _rp or (r.get("updated_at") if r.get("status") in ("refunded", "partial_refunded") else "")

    # İstatistik: tüm iade grubunda durum + ödeme dağılımı (mevcut filtreden bağımsız,
    # pazaryeri DISI tum site siparisleri) — sekmedeki rozetler için
    stat_filter = {"platform": {"$nin": ["trendyol", "hepsiburada"]},
                   "status": {"$in": RETURN_STATUSES}}
    status_counts = {}
    payment_counts = {}
    try:
        async for grp in db.orders.aggregate([
            {"$match": stat_filter},
            {"$group": {"_id": "$status", "n": {"$sum": 1}}},
        ]):
            status_counts[grp["_id"]] = grp["n"]
        async for grp in db.orders.aggregate([
            {"$match": stat_filter},
            {"$group": {"_id": "$payment_method", "n": {"$sum": 1}}},
        ]):
            payment_counts[grp["_id"] or "bilinmiyor"] = grp["n"]
    except Exception as e:
        logger.warning(f"[ticimax-returns] stats hatası: {e}")

    # Ücretsiz-kargo siparişlerinde kısmi iade mahsubu için standart kargo ücreti
    # (vitrin/checkout ile AYNI kaynak: settings.cargo_fees[default] → settings.shipping_fee)
    _free_ship_fee = 0.0
    _free_ship_threshold = 0.0
    try:
        _s = await db.settings.find_one(
            {"id": "main"},
            {"_id": 0, "cargo_fees": 1, "default_cargo_company": 1, "shipping_fee": 1,
             "free_shipping_threshold": 1},
        ) or {}
        _cf = _s.get("cargo_fees") or {}
        _dc = _s.get("default_cargo_company") or ""
        if _dc and isinstance(_cf, dict) and _cf.get(_dc) not in (None, ""):
            _free_ship_fee = float(_cf.get(_dc))
        elif _s.get("shipping_fee") not in (None, ""):
            _free_ship_fee = float(_s.get("shipping_fee"))
        if _s.get("free_shipping_threshold") not in (None, ""):
            _free_ship_threshold = float(_s.get("free_shipping_threshold"))
    except Exception:
        _free_ship_fee = 0.0

    # BL-040: iade IBAN'ı şifreli saklanır → iadeyi ödeyen yetkili personele (returns.refund_pay /
    # returns.iyzico_refund / orders.cancelled_tab / süper admin) çözülmüş, diğerlerine maskeli.
    await present_refund_bank_info_in(rows, current_user)

    return {
        "success": True,
        "orders": rows,
        "free_ship_fee": round(_free_ship_fee or 0, 2),
        "free_shipping_threshold": round(_free_ship_threshold or 0, 2),
        "total": total,
        "page": page,
        "limit": limit,
        "status_counts": status_counts,
        "payment_counts": payment_counts,
        "total_returns": sum(status_counts.values()),
    }


@router.post("/bulk-approve-returns")
async def bulk_approve_site_returns(
    until: str = Query(..., description="Bu tarihe (dahil) kadar oluşan iadeler — ISO YYYY-MM-DD"),
    confirm: bool = Query(False, description="true → gerçekten onayla; false → dry-run"),
    current_user: dict = Depends(require_permission("returns.manage")),
):
    """SESSİZ TOPLU ONAY (Kadir talebi: '1 Temmuz'a kadar site iadelerinin hepsi onaylansın').
    'İade Talebi Oluşturuldu' (return_requested) durumundaki SİTE (pazaryeri-dışı) iadelerini,
    `until` tarihine kadar oluşanları TAM onaylar → durum return_approved olur, GP ikonu (mevcut
    kural gereği) gelir. HİSTORİK backfill olduğundan: müşteriye BİLDİRİM GÖNDERİLMEZ ve STOK
    HAREKETİ YAPILMAZ (goods zaten geçmişte işlendi; restock çift-sayım yaratırdı). Yalnız onay
    kaydı + tutar dökümü yazılır. İDEMPOTENT: zaten onaylı olan atlanır."""
    from datetime import datetime, timezone
    _cut = str(until).strip()[:10] + "T23:59:59"  # gün sonuna kadar dahil
    base_filter = {
        "platform": {"$nin": ["trendyol", "hepsiburada"]},
        "status": "return_requested",
    }
    now_iso = datetime.now(timezone.utc).isoformat()
    scanned = 0
    approved = []
    skipped_late = 0
    async for order in db.orders.find(base_filter, {"_id": 0}):
        _ca = str(order.get("created_at") or "")
        # created_at boşsa yine de dahil et (eski Ticimax kayıtları tarihi eksik olabilir);
        # doluysa cutoff ile karşılaştır.
        if _ca and _ca[:19] > _cut:
            skipped_late += 1
            continue
        scanned += 1
        if not confirm:
            approved.append({"order_number": order.get("order_number"), "created_at": _ca})
            continue
        oid = order.get("id")
        # Köprü kaydını garanti et (idempotent open mantığı)
        rec = await db.customer_returns.find_one({"order_id": oid, "status": {"$ne": "expired"}}, {"_id": 0})
        if not rec:
            _src = order.get("items") or []
            _items = [{
                "name": it.get("product_name") or it.get("name") or "Ürün",
                "size": it.get("size", "") or "", "color": it.get("color", "") or "",
                "quantity": int(it.get("quantity", 1) or 1),
                "price": float(it.get("price") or it.get("unit_price") or 0),
                "unit_price": float(it.get("unit_price") or it.get("price") or 0),
                "product_id": it.get("barcode") or it.get("product_id") or it.get("sku") or "",
                "discount_amount": it.get("discount_amount"),  # donmuş per-ürün indirim (W11214)
            } for it in _src]
            rid = generate_id()
            rec = {"id": rid, "order_id": oid, "order_number": order.get("order_number", ""),
                   "user_id": order.get("user_id"), "items": _items, "reason": "",
                   "return_code": (order.get("return_request") or {}).get("return_code", "") or "",
                   "status": "created", "source": "bulk_approve_backfill",
                   "created_at": order.get("created_at") or now_iso}
            await db.customer_returns.insert_one({**rec})
            await db.orders.update_one({"id": oid}, {"$set": {"return_request.return_id": rid}})
        rid = rec.get("id")
        if rec.get("status") in ("approved", "refunded", "partial_refunded"):
            continue  # zaten onaylı/kapalı
        # TAM onay, mağaza kusuru (kargo düşülmez) — tutar dökümü hesaplanır.
        try:
            bd = await _compute_refund_breakdown(rec, order, "store")
            final_amount = bd.get("auto_refund")
        except Exception as _e:
            logger.warning(f"[bulk-approve] breakdown hata {oid}: {_e}")
            bd, final_amount = {}, None
        approval = {"by": current_user.get("email") or current_user.get("id"), "at": now_iso,
                    "fault": "store", "auto_refund": final_amount, "final_refund": final_amount,
                    "manual_override": False, "note": "Toplu geriye dönük onay (1 Temmuz'a kadar)"}
        await db.customer_returns.update_one({"id": rid}, {"$set": {
            "status": "approved", "fault": "store", "refund_breakdown": bd,
            "refund_amount": final_amount, "approval": approval, "updated_at": now_iso,
        }, "$unset": {"approved_item_indexes": "", "approved_items": ""}})
        await db.orders.update_one({"id": oid}, {"$set": {
            "status": "return_approved", "return_request.status": "approved",
            "return_approved_at": now_iso, "updated_at": now_iso,
        }})
        approved.append({"order_number": order.get("order_number"), "return_id": rid, "amount": final_amount})
    return {"success": True, "confirmed": confirm, "cutoff": _cut,
            "count": len(approved), "scanned": scanned, "skipped_after_cutoff": skipped_late,
            "items": approved[:60]}


@router.post("/set-return-status")
async def set_return_status_silent(
    payload: dict,
    current_user: dict = Depends(require_permission("returns.manage")),
):
    """SESSİZ durum düzeltme (Kadir: 'iade edilmiş ama iade sayfasında yoklar'). Verilen sipariş
    no'larının durumunu bir İADE durumuna çeker → iade sayfasında görünürler. Historik düzeltme
    olduğundan MÜŞTERİ BİLDİRİMİ ve STOK HAREKETİ YAPILMAZ. Ödeme durumuna dokunmaz.
    payload: {order_numbers: [..], status: 'returned'|'refunded'|...}"""
    onums = payload.get("order_numbers") or []
    status = str(payload.get("status") or "").strip()
    if not onums or not status:
        raise HTTPException(status_code=400, detail="order_numbers ve status gerekli")
    if status not in RETURN_STATUSES:
        raise HTTPException(status_code=400, detail=f"status bir iade durumu olmalı: {sorted(RETURN_STATUSES)}")
    if status in ("refunded", "partial_refunded"):
        # ACİL ANAHTAR (CHAIN08): sessiz "iade ödendi" işaretlemesi de para-iadesi kaydıdır →
        # freeze_refunds / restore_mode / geri yükleme sonrası iade kilidi açıkken 423.
        from security_switches import require_switch_off as _rso
        await _rso("freeze_refunds")
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    updated = []
    for onum in onums:
        o = await db.orders.find_one({"order_number": str(onum).strip(),
                                      "platform": {"$nin": ["trendyol", "hepsiburada"]}},
                                     {"_id": 0, "id": 1, "status": 1})
        if not o:
            continue
        _set = {"status": status, "updated_at": now_iso}
        if status in ("return_approved", "returned", "refunded", "partial_refunded"):
            _set["return_request.status"] = "approved"
            _set.setdefault("return_approved_at", now_iso)
        await db.orders.update_one({"id": o["id"]}, {"$set": _set})
        updated.append({"order_number": onum, "old": o.get("status"), "new": status})
    return {"success": True, "count": len(updated), "updated": updated}




@router.post("/flatten-order/{order_id}")
async def flatten_order_financials(
    order_id: str,
    amount: Optional[float] = Query(None, description="Hedef düz tutar (TL). Verilmezse Σ kalem fiyatı."),
    current_user: dict = Depends(require_permission("returns.manage")),
):
    """TEK SİPARİŞİ 'DÜMDÜZ' TUTARA NORMALLE (Kadir: Senem Birdal — Ticimax import'undan gelen
    hayalet indirim/kargo düzeltmesi). İndirim (discount+payment_discount) ve kargoyu SIFIRLAR,
    subtotal=total=amount yapar, kalem fiyatlarını amount'a ölçekler, kalem indirimlerini sıfırlar.
    Böylece panel/iade/gider pusulası hepsi 'amount' (ör. 985,15) olarak düz görünür.
    NOT: yalnız veri-kalitesi düzeltmesi; ödeme durumuna DOKUNMAZ. Loglanır."""
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    items = order.get("items") or []
    _cur_sum = sum(round(float(i.get("price") or 0), 2) * int(i.get("quantity") or 1) for i in items)
    target = round(float(amount), 2) if amount is not None else round(_cur_sum, 2)
    if target <= 0:
        raise HTTPException(status_code=400, detail="Geçersiz hedef tutar")
    # Kalem fiyatlarını hedefe ölçekle (tek kalemse doğrudan; çok kalemde mevcut orana göre).
    _scale = (target / _cur_sum) if _cur_sum > 0.5 else 1.0
    new_items = []
    for it in items:
        _q = int(it.get("quantity") or 1)
        _np = round(float(it.get("price") or 0) * _scale, 2)
        ni = dict(it)
        ni["price"] = _np
        ni["unit_price"] = _np
        ni["discount_amount"] = 0
        ni["discount"] = 0
        new_items.append(ni)
    _now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    _set = {
        "items": new_items,
        "subtotal": target, "total": target, "total_amount": target,
        "discount": 0, "discount_amount": 0, "payment_discount": 0,
        "shipping_cost": 0,
        # Tahsilat/charged tabanını da hedefe çek → gider pusulası charged>total farkından
        # eski tutarı (985,63) kullanmasın; düz 'amount' baz alsın.
        "paid_amount": target,
        "financials_flattened_by": current_user.get("email") or current_user.get("id"),
        "financials_flattened_at": _now,
        "updated_at": _now,
    }
    _iyz = order.get("iyzico_retrieve_response")
    if isinstance(_iyz, dict) and _iyz.get("paidPrice") is not None:
        _set["iyzico_retrieve_response.paidPrice"] = target
    await db.orders.update_one({"id": order_id}, {"$set": _set})
    # customer_returns köprü kalemlerini de eşle (iade/gp aynı görünsün)
    _cr_items = [{
        "name": it.get("product_name") or it.get("name") or "Ürün",
        "size": it.get("size", "") or "", "color": it.get("color", "") or "",
        "quantity": int(it.get("quantity", 1) or 1),
        "price": float(it.get("price") or 0), "unit_price": float(it.get("unit_price") or 0),
        "product_id": it.get("barcode") or it.get("product_id") or it.get("sku") or "",
        "discount_amount": it.get("discount_amount"),  # donmuş per-ürün indirim (W11214)
    } for it in new_items]
    await db.customer_returns.update_many(
        {"order_id": order_id}, {"$set": {"items": _cr_items}})
    # Bu iadeye ait gider pusulası varsa yeni tutarla yeniden hesaplansın (idempotent).
    _cr = await db.customer_returns.find_one({"order_id": order_id}, {"_id": 0, "id": 1})
    logger.warning(f"[flatten-order] order={order_id} → {target} TL, admin={current_user.get('email')}")
    return {"success": True, "order_id": order_id, "flat_amount": target,
            "items": len(new_items), "return_id": (_cr or {}).get("id")}


@router.post("/orders/refresh-dates")
async def refresh_order_dates(
    page: int = Query(1, ge=1),
    per_pages: int = Query(5, ge=1, le=15),
    current_user: dict = Depends(require_permission("returns.manage")),
):
    # [ticimax-off 2026-06-22] Ticimax SOAP entegrasyonu kapatildi; bu uc devre disi.
    return {"success": False, "message": "Ticimax tarih senkronu kapatildi."}


# ============================================================================
# MÜKERRER İADE TEMİZLİĞİ — sistem geneli (Kadir talebi: "site siparişlerinde
# başka çift olan iade varsa sil sistem genelinde")
# ----------------------------------------------------------------------------
# Ticimax import'u bazı siparişleri hem BARE ticimax kaydı (telefon YOK, iade
# köprüsü/pusula YOK) hem de gerçek meluni kaydı (telefon + return_request +
# customer_returns + gider_pusulası VAR) olarak iki kez oluşturdu → iade
# panelinde AYNI sipariş no iki satır. Bu uç aynı order_number'lı iade
# kayıtlarını gruplar, hangisi BARE-junk hangisi GERÇEK tespit eder; confirm=false
# yalnız rapor (dry-run), confirm=true junk olanı arşivleyip siler (geri
# alınabilir: orders_deleted).
# ============================================================================
async def _return_record_signals(o: dict) -> dict:
    """Bir iade kaydının 'zenginlik' sinyalleri — hangisini tutup hangisini sileceğimize karar için."""
    oid = o.get("id")
    onum = o.get("order_number")
    addr = o.get("shipping_address") or {}
    has_phone = bool(str(addr.get("phone") or "").strip())
    rr = o.get("return_request") or {}
    has_return_request = bool(rr.get("items") or rr.get("reason") or rr.get("requested_at"))
    has_cr = False
    if oid:
        has_cr = bool(await db.customer_returns.find_one(
            {"$or": [{"order_id": oid}, {"order_number": onum}]}, {"_id": 1}))
    has_gp = False
    if onum:
        has_gp = bool(await db.gider_pusulasi.find_one({"order_number": onum}, {"_id": 1})) \
                 or bool(o.get("gider_pusulasi_no"))
    items = o.get("items") or []
    score = (10 if has_phone else 0) + (8 if has_cr else 0) + (6 if has_gp else 0) \
            + (5 if has_return_request else 0) + (1 if (o.get("subtotal") or 0) > 0 else 0) \
            + (2 if items else 0)
    return {
        "id": oid, "order_number": onum,
        "platform": o.get("platform") or "", "source": o.get("source") or "",
        "status": o.get("status") or "",
        "has_phone": has_phone, "has_return_request": has_return_request,
        "has_customer_returns": has_cr, "has_gider_pusulasi": has_gp,
        "item_count": sum(int(i.get("quantity") or 1) for i in items),
        "subtotal": float(o.get("subtotal") or 0), "total": float(o.get("total") or 0),
        "created_at": o.get("created_at") or "", "updated_at": o.get("updated_at") or "",
        "score": score,
    }


@router.get("/duplicate-returns")
async def scan_duplicate_returns(
    confirm: bool = Query(False, description="true → junk mükerrer kayıtları arşivle+sil"),
    current_user: dict = Depends(require_permission("returns.view")),
):
    """Aynı order_number'a sahip birden fazla İADE kaydını (site/pazaryeri-dışı) bul.
    Her grupta EN ZENGİN kayıt tutulur; yalnız telefon+iade köprüsü+pusula+iade-talebi
    HİÇBİRİ olmayan BARE-junk kayıt(lar) silinmeye aday gösterilir. confirm=true olunca
    silinir (orders_deleted'e arşivlenir; geri alınabilir)."""
    base_filter = {
        "platform": {"$nin": ["trendyol", "hepsiburada"]},
        "status": {"$in": RETURN_STATUSES},
    }
    proj = {"_id": 0, "id": 1, "order_number": 1, "platform": 1, "source": 1, "status": 1,
            "shipping_address": 1, "return_request": 1, "gider_pusulasi_no": 1,
            "items": 1, "subtotal": 1, "total": 1, "created_at": 1, "updated_at": 1}
    by_num = {}
    async for o in db.orders.find(base_filter, proj):
        onum = str(o.get("order_number") or "").strip()
        if not onum:
            continue
        by_num.setdefault(onum, []).append(o)

    groups, delete_ids = [], []
    for onum, recs in by_num.items():
        if len(recs) < 2:
            continue
        sigs = [await _return_record_signals(o) for o in recs]
        sigs.sort(key=lambda s: (s["score"], s["updated_at"], s["created_at"]), reverse=True)
        keep = sigs[0]
        # Silme adayları: yalnız GERÇEKTEN bare (telefon+CR+GP+iade-talebi hiçbiri yok) olanlar.
        # Belirsiz (birden fazla zengin kayıt) grupları SİLME — raporla, elle bakılsın.
        cand = [s for s in sigs[1:]
                if not (s["has_phone"] or s["has_customer_returns"]
                        or s["has_gider_pusulasi"] or s["has_return_request"])]
        ambiguous = [s for s in sigs[1:] if s not in cand]
        for s in cand:
            delete_ids.append(s["id"])
        groups.append({
            "order_number": onum, "count": len(recs),
            "keep": keep, "delete_candidates": cand, "ambiguous": ambiguous,
        })

    deleted = []
    if confirm and delete_ids:
        for did in delete_ids:
            o = await db.orders.find_one({"id": did}, {"_id": 0})
            if not o:
                continue
            import datetime as _dt
            o["deleted_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
            o["deleted_by"] = current_user.get("email", "")
            o["deleted_reason"] = "mükerrer iade temizliği"
            try:
                await db.orders_deleted.replace_one({"id": did}, o, upsert=True)
            except Exception as _e:
                logger.warning(f"[dup-returns] archive fail {did}: {_e}")
            await db.orders.delete_one({"id": did})
            deleted.append(did)

    return {
        "success": True,
        "duplicate_group_count": len(groups),
        "delete_candidate_count": len(delete_ids),
        "deleted_count": len(deleted),
        "confirmed": confirm,
        "groups": groups,
        "deleted_ids": deleted,
    }


@router.post("/duplicate-returns/merge-ambiguous")
async def merge_ambiguous_duplicate_returns(
    confirm: bool = Query(False, description="true → köprüyü taşı + twin'i sil"),
    current_user: dict = Depends(require_permission("returns.manage")),
):
    """BELİRSİZ mükerrer gruplar: aynı order_number'da iki kayıt da iade köprüsü (customer_returns)
    taşıyor. GERÇEK kayıt telefonlu meluni kaydıdır; twin (telefonsuz ticimax backfill) silinmeden
    ÖNCE köprü (customer_returns) + varsa gider_pusulası order_id/return_id'si KEEP kaydına
    TAŞINIR (order_id ile çözülen panel köprüsü kopmasın). Sonra twin arşivlenip silinir."""
    base_filter = {
        "platform": {"$nin": ["trendyol", "hepsiburada"]},
        "status": {"$in": RETURN_STATUSES},
    }
    proj = {"_id": 0, "id": 1, "order_number": 1, "platform": 1, "source": 1, "status": 1,
            "shipping_address": 1, "return_request": 1, "gider_pusulasi_no": 1,
            "items": 1, "subtotal": 1, "total": 1, "created_at": 1, "updated_at": 1}
    by_num = {}
    async for o in db.orders.find(base_filter, proj):
        onum = str(o.get("order_number") or "").strip()
        if onum:
            by_num.setdefault(onum, []).append(o)

    actions = []
    import datetime as _dt
    for onum, recs in by_num.items():
        if len(recs) < 2:
            continue
        sigs = [await _return_record_signals(o) for o in recs]
        sigs.sort(key=lambda s: (s["score"], s["updated_at"], s["created_at"]), reverse=True)
        keep = sigs[0]
        twins = [s for s in sigs[1:]
                 if not s["has_phone"] and (s["platform"] == "ticimax" or "backfill" in (s["source"] or ""))]
        # Yalnız GERÇEKTEN belirsiz olanlar: bare-junk taramasında SİLİNMEMİŞ (yani köprü/pusula taşıyan) twin.
        twins = [s for s in twins
                 if s["has_customer_returns"] or s["has_gider_pusulasi"] or s["has_return_request"]]
        if not keep.get("has_phone") or not twins:
            continue
        keep_id = keep["id"]
        for t in twins:
            tid = t["id"]
            moved_cr = moved_gp = 0
            if confirm:
                # KEEP zaten köprü taşımıyorsa twin'in köprüsünü KEEP'e taşı (order_id).
                if not await db.customer_returns.find_one({"order_id": keep_id}, {"_id": 1}):
                    _r = await db.customer_returns.update_many({"order_id": tid}, {"$set": {"order_id": keep_id}})
                    moved_cr = _r.modified_count
                _r2 = await db.gider_pusulasi.update_many({"return_id": tid}, {"$set": {"return_id": keep_id}})
                moved_gp = _r2.modified_count
                # twin'i arşivle + sil
                o = await db.orders.find_one({"id": tid}, {"_id": 0})
                if o:
                    o["deleted_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
                    o["deleted_by"] = current_user.get("email", "")
                    o["deleted_reason"] = "mükerrer iade (belirsiz) — köprü KEEP'e taşındı"
                    try:
                        await db.orders_deleted.replace_one({"id": tid}, o, upsert=True)
                    except Exception as _e:
                        logger.warning(f"[dup-merge] archive fail {tid}: {_e}")
                    await db.orders.delete_one({"id": tid})
            actions.append({
                "order_number": onum, "keep_id": keep_id, "twin_id": tid,
                "moved_customer_returns": moved_cr, "moved_gider_pusulasi": moved_gp,
                "keep_name": None,
            })
    return {"success": True, "confirmed": confirm, "action_count": len(actions), "actions": actions}


# ============================================================================
# EXPORT — İade siparişlerini Excel (.xlsx) indir (görseldeki kolon düzeni)
# ============================================================================
@router.get("/return-orders/export")
async def export_rooftr_return_orders(
    status: Optional[str] = Query(None),
    payment: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None, description="İade ONAY tarihi başlangıcı (TR günü)"),
    end_date: Optional[str] = Query(None, description="İade ONAY tarihi bitişi (TR günü)"),
    request: Request = None,
    # GÜVENLİK (denetim 2026-10): toplu kişisel veri (ad soyad + sipariş) dışa aktarımı →
    # 'Dışa Aktar' yetkisi (reports.export) + denetim kaydı. Süper admin ('*') geçer.
    current_user: dict = Depends(require_permission("reports.export")),
):
    """İade siparişlerini listelemeyle AYNI filtrelerle Excel'e aktarır.
    Kolonlar: Ürün Adı | Tutar | Sipariş Tarihi | Sipariş ID | Sipariş No |
    Ad Soyad | Durum | Kaynak | Ödeme Tipi."""
    # ACİL ANAHTAR: freeze_exports açıkken toplu kişisel veri dışa aktarımı yapılmaz (423).
    from security_switches import require_switch_off, emit_security_event
    await require_switch_off("freeze_exports")
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from fastapi.responses import StreamingResponse
    try:
        from order_statuses import ORDER_STATUS_CATALOG
        status_label = {s["key"]: (s.get("label") or s["key"]) for s in ORDER_STATUS_CATALOG}
    except Exception:
        status_label = {}

    # Excel, EKRANDAKİ listeyle aynı nüfusu vermeli. Eski hâli yalnız ticimax
    # kaynaklı siparişleri alıyordu; liste ucu ise pazaryeri DIŞI her kaynağı
    # (meluni + ticimax + boş) gösteriyor → Excel ekrandan eksik çıkıyordu.
    base_filter = {"platform": {"$nin": ["trendyol", "hepsiburada"]}}
    base_filter.update(_iade_tarih_filtresi(start_date, end_date))
    if status:
        _st = [s.strip() for s in status.split(",") if s.strip()]
        base_filter["status"] = {"$in": _st} if len(_st) > 1 else (_st[0] if _st else {"$in": RETURN_STATUSES})
    else:
        base_filter["status"] = {"$in": RETURN_STATUSES}
    if payment:
        _pm = [x.strip() for x in str(payment).split(",") if x.strip()]
        base_filter["payment_method"] = {"$in": _pm} if len(_pm) > 1 else (_pm[0] if _pm else payment)
    if search:
        s = re.escape(search.strip())
        rx = {"$regex": s, "$options": "i"}
        base_filter["$and"] = [{
            "$or": [
                {"order_number": rx}, {"order_code": rx},
                {"shipping_address.first_name": rx}, {"shipping_address.last_name": rx},
                {"shipping_address.phone": rx}, {"shipping_address.email": rx},
            ]
        }]

    proj = {
        "_id": 0, "id": 1, "order_number": 1, "ticimax_order_id": 1, "status": 1,
        "payment_method": 1, "payment_method_raw": 1, "total": 1,
        "shipping_address": 1, "billing_address": 1, "customer_name": 1, "full_name": 1,
        "items": 1, "created_at": 1, "channel_source": 1,
    }

    wb = Workbook()
    ws = wb.active
    ws.title = "İade Siparişleri"
    headers = ["Ürün Adı", "Tutar", "Sipariş Tarihi", "Sipariş ID", "Sipariş No",
               "Ad Soyad", "Durum", "Kaynak", "Ödeme Tipi"]
    ws.append(headers)
    hfill = PatternFill("solid", fgColor="FCE4B6")
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = hfill
        c.alignment = Alignment(horizontal="center", vertical="center")

    _row_count = 0
    cursor = db.orders.find(base_filter, proj).sort("created_at", -1)
    async for o in cursor:
        _row_count += 1
        addr = o.get("shipping_address") or {}
        bill = o.get("billing_address") or {}
        name = (" ".join([addr.get("first_name") or "", addr.get("last_name") or ""]).strip()
                or addr.get("full_name") or addr.get("name")
                or o.get("customer_name") or o.get("full_name")
                or " ".join([bill.get("first_name") or "", bill.get("last_name") or ""]).strip()
                or bill.get("name") or "")
        items = o.get("items") or []
        urun = ", ".join([
            (i.get("product_name") or i.get("name") or "").strip()
            for i in items if (i.get("product_name") or i.get("name"))
        ])
        st = o.get("status") or ""
        ws.append([
            urun,
            float(o.get("total") or 0),
            str(o.get("created_at") or "")[:10],
            o.get("id") or "",
            o.get("order_number") or "",
            name,
            status_label.get(st, st),
            o.get("channel_source") or "Ticimax",
            _payment_label(o.get("payment_method") or "", o.get("payment_method_raw") or ""),
        ])

    widths = [42, 12, 14, 16, 14, 24, 18, 12, 16]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + i)].width = w

    # GÜVENLİK: Excel/CSV formül injection — = + - @ TAB CR ile başlayan metin hücrelerini kaçışla.
    # Üye dışa aktarımıyla aynı kural: "+90 5xx ..." gibi SAF telefon numarası formül değildir → okunur kalır.
    _plain_phone = re.compile(r"^\+[0-9][0-9 ()]*$")
    for _ws in wb.worksheets:
        for _row in _ws.iter_rows(min_row=2):
            for _c in _row:
                if (isinstance(_c.value, str) and _c.value[:1] in ("=", "+", "-", "@", "\t", "\r")
                        and not _plain_phone.match(_c.value)):
                    _c.value = "'" + _c.value
    await record_admin_audit(
        db, action="returns.export_xlsx", entity_type="export", entity_id="rooftr_return_orders",
        current_user=current_user, request=request, source="admin.returns",
        metadata={"rows": _row_count, "status": (status or "")[:200], "payment": (payment or "")[:100],
                  "has_search": bool(search), "start_date": start_date or "", "end_date": end_date or ""})
    await emit_security_event("returns_export", current_user,
                              {"export": "returns.rooftr_return_orders", "rows": _row_count})
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=iade-siparisleri.xlsx"},
    )


# ============================================================================
# BRIDGE — Ticimax iade siparişini zengin iade akışına (customer_returns) bağlar
# ============================================================================
def _pick_return_record(recs):
    """MÜKERRER customer_returns kayıtlarında KANONİK kaydı seçer — liste, /open ve gider pusulası
    AYNI kaydı görsün (W11262: 3 kayıt; liste 'son gelen', /open 'ilk bulunan' alıp çelişiyordu).
      1) 'expired' olmayanlar (hepsi expired ise hepsi),
      2) gerçek KARAR nesnesi (approval/rejection) olan varsa → en son kararlı olan,
      3) yoksa → en son güncellenen/oluşturulan.
    Hiç yoksa None. Kayıt SİLMEZ/BİRLEŞTİRMEZ — yalnız seçer (veri kaybı yok)."""
    recs = [r for r in (recs or []) if r]
    if not recs:
        return None
    live = [r for r in recs if str(r.get("status") or "") != "expired"] or recs

    def _ts(r):
        return str(r.get("updated_at") or r.get("created_at") or "")

    decided = [r for r in live if (r.get("approval") or r.get("rejection"))]
    if decided:
        return max(decided, key=lambda r: str(((r.get("approval") or r.get("rejection") or {}).get("at")) or _ts(r)))
    return max(live, key=_ts)


def _decision_of(rec: dict):
    """Bir customer_returns kaydının GERÇEKTEN karara bağlanıp bağlanmadığı.
    Döner: (decided: bool, kind: 'approved'|'rejected'|'closed'|None, by, at).
    Karar = approve_return/bulk-approve'un yazdığı `approval` nesnesi VEYA reject'in `rejection`
    nesnesi VEYA kapanmış durum (refunded/partial_refunded/cancelled). Yalnız `status="approved"`
    (karar nesnesi YOK — köprü kaydı sipariş durumundan türetilmiş ya da durum menüsünden yazılmış)
    karar SAYILMAZ: kalem onayı yapılmamıştır, personel onaylayabilmelidir (W11262)."""
    rec = rec or {}
    _st = str(rec.get("status") or "")
    _ap = rec.get("approval") or {}
    _rj = rec.get("rejection") or {}
    if _ap:
        return True, "approved", _ap.get("by") or "", _ap.get("at") or ""
    if _rj or _st in ("rejected", "return_rejected"):
        return True, "rejected", _rj.get("by") or "", _rj.get("at") or ""
    if _st in ("refunded", "partial_refunded", "cancelled"):
        return True, "closed", "", (rec.get("refund_payment") or {}).get("at") or ""
    return False, None, "", ""


@router.get("/returns/diagnose")
async def diagnose_rooftr_return(order_number: str, current_user: dict = Depends(require_permission("returns.view"))):
    """TEŞHİS: bir siparişin iade durumunu tek bakışta — sipariş alanları + o siparişe ait TÜM
    customer_returns kayıtları (mükerrer var mı, hangisi karara bağlı, kim/ne zaman). Panel
    'zaten onaylanmış' derken kalem onayı yoksa nedeni buradan görülür."""
    onum = (order_number or "").strip()
    o = await db.orders.find_one({"order_number": onum}, {"_id": 0, "id": 1, "order_number": 1, "status": 1,
                                                          "return_approved_at": 1, "return_request": 1,
                                                          "refund_paid_at": 1, "platform": 1})
    if not o:
        raise HTTPException(status_code=404, detail=f"Sipariş bulunamadı: {onum}")
    recs = []
    async for cr in db.customer_returns.find({"order_id": o["id"]}, {"_id": 0}):
        _dec, _kind, _by, _at = _decision_of(cr)
        recs.append({
            "id": cr.get("id"), "status": cr.get("status"), "source": cr.get("source"),
            "created_at": cr.get("created_at"), "updated_at": cr.get("updated_at"),
            "decided": _dec, "decision_kind": _kind, "decision_by": _by, "decision_at": _at,
            "has_approval_obj": bool(cr.get("approval")), "has_rejection_obj": bool(cr.get("rejection")),
            "approved_item_indexes": cr.get("approved_item_indexes"),
            "approved_items": cr.get("approved_items"),
            "items_count": len(cr.get("items") or []),
            "has_gider_pusulasi": bool(cr.get("has_gider_pusulasi")), "gider_pusulasi_no": cr.get("gider_pusulasi_no"),
            "refund_amount": cr.get("refund_amount"), "restocked": bool(cr.get("restocked_at") or cr.get("stock_restored")),
        })
    _open_pick = next((r for r in recs if r.get("status") != "expired"), None)
    return {"order": o, "records": recs, "record_count": len(recs),
            "duplicate": len([r for r in recs if r.get("status") != "expired"]) > 1,
            "open_would_use": (_open_pick or {}).get("id"),
            "verdict": ("MÜKERRER kayıt — liste ve pencere farklı kaydı görüyor olabilir" if len(recs) > 1
                        else ("Karar nesnesi YOK ama status=approved → çıplak bayrak; artık onaylanabilir"
                              if recs and recs[0]["status"] == "approved" and not recs[0]["has_approval_obj"]
                              else ("Gerçek karar var" if recs and recs[0]["decided"] else "Kayıt karara bağlı değil / kayıt yok")))}


def _cr_summary(r: dict) -> dict:
    _dec, _kind, _by, _at = _decision_of(r)
    return {"id": r.get("id"), "status": r.get("status"), "source": r.get("source"),
            "created_at": r.get("created_at"), "updated_at": r.get("updated_at"),
            "decided": _dec, "decision_kind": _kind, "decision_by": _by, "decision_at": _at,
            "approved_item_indexes": r.get("approved_item_indexes"), "items_count": len(r.get("items") or []),
            "has_gider_pusulasi": bool(r.get("has_gider_pusulasi")), "gider_pusulasi_no": r.get("gider_pusulasi_no"),
            "refund_amount": r.get("refund_amount")}


@router.post("/returns/dedupe")
async def dedupe_rooftr_returns(
    order_number: str = Query(..., description="Sipariş no (ör. W11262)"),
    confirm: bool = Query(False, description="true → uygula; false → kuru çalıştırma (yalnız plan)"),
    keep: str = Query("", description="Tutulacak return_id (boş → otomatik kanonik seçim)"),
    current_user: dict = Depends(require_permission("returns.manage")),
):
    """MÜKERRER customer_returns TEMİZLİĞİ (soft): kanonik kayıt tutulur, diğerleri status='expired'
    yapılır — SİLİNMEZ (tüm sorgular expired'ı dışlar → panelde kaybolur; geri alınabilir:
    status_before_expire saklanır). Kanonik seçim: keep param > gider pusulası bağlı olan >
    _pick_return_record (gerçek karar > en yeni). Sipariş köprüsü kanonik kayda sabitlenir."""
    onum = (order_number or "").strip()
    o = await db.orders.find_one({"order_number": onum}, {"_id": 0, "id": 1, "order_number": 1, "status": 1})
    if not o:
        raise HTTPException(status_code=404, detail=f"Sipariş bulunamadı: {onum}")
    recs = [cr async for cr in db.customer_returns.find({"order_id": o["id"]}, {"_id": 0})]
    live = [r for r in recs if str(r.get("status") or "") != "expired"]
    if len(live) <= 1:
        return {"success": True, "dry_run": not confirm, "message": "Mükerrer canlı kayıt yok",
                "records": [_cr_summary(r) for r in recs]}
    if keep:
        canon = next((r for r in live if r.get("id") == keep), None)
        if not canon:
            raise HTTPException(status_code=400, detail=f"keep={keep} bu siparişin canlı kayıtları arasında yok")
    else:
        _gp = [r for r in live if r.get("has_gider_pusulasi") or r.get("gider_pusulasi_no")]
        canon = _pick_return_record(_gp) if _gp else _pick_return_record(live)
    to_expire = [r for r in live if r.get("id") != canon.get("id")]
    plan = {"order": o, "keep": _cr_summary(canon), "expire": [_cr_summary(r) for r in to_expire]}
    if not confirm:
        return {"success": True, "dry_run": True, **plan,
                "hint": "Uygulamak için confirm=true (isteğe bağlı keep=<return_id>)"}
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    for r in to_expire:
        await db.customer_returns.update_one({"id": r["id"]}, {"$set": {
            "status": "expired", "status_before_expire": r.get("status"),
            "expired_reason": f"dedupe→{canon.get('id')}",
            "expired_by": current_user.get("email") or current_user.get("id"), "expired_at": now_iso,
            "updated_at": now_iso}})
    await db.orders.update_one({"id": o["id"]}, {"$set": {"return_request.return_id": canon.get("id")}})
    logger.info(f"[dedupe-returns] {onum}: keep={canon.get('id')} expired={[r.get('id') for r in to_expire]} by={current_user.get('email')}")
    return {"success": True, "dry_run": False, **plan}


@router.post("/returns/{order_id}/open")
async def open_rooftr_return(order_id: str, current_user: dict = Depends(require_permission("returns.manage"))):
    """Ticimax iade siparişinden, zengin iade akışı (onayla/reddet/gider/öde) için bir
    `customer_returns` köprü kaydı üretir. İDEMPOTENT: zaten varsa mevcut return_id'yi döndürür.
    PARA/İŞLEM YAPMAZ — sadece köprü kaydını oluşturur. Sonraki adımlar mevcut
    /api/orders/returns/{return_id}/{refund-preview,approve,reject,gider-pusulasi,refund-pay}
    endpoint'leriyle yürür (RBAC + bildirim oralarda)."""
    from datetime import datetime, timezone
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")

    # İdempotent: bu siparişe ait köprü kaydı zaten varsa onu döndür.
    # ONARIM: eski kayıtta items hiç yazılmamışsa (boş) sipariş kalemleriyle doldur —
    # gider pusulası/iade akışları kalemleri bu kayıttan okur, boş kayıt kısmi seçimi bozar.
    # MÜKERRER kayıt güvenliği: tüm canlı kayıtları al, kanonik olanı seç (liste ile AYNI kural).
    _cands = []
    async for _c in db.customer_returns.find(
        {"order_id": order_id, "status": {"$ne": "expired"}},
        {"_id": 0, "id": 1, "status": 1, "items": 1, "approval": 1, "rejection": 1, "refund_payment": 1,
         "created_at": 1, "updated_at": 1}):
        _cands.append(_c)
    existing = _pick_return_record(_cands)
    if existing:
        if len(_cands) > 1:
            # Sipariş köprüsünü kanonik kayda sabitle → liste/pencere/gider pusulası aynı kaydı görür.
            await db.orders.update_one({"id": order_id},
                                       {"$set": {"return_request.return_id": existing.get("id")}})
        if not (existing.get("items") or []):
            _src = order.get("items") or []
            _fix = [{
                "name": it.get("product_name") or it.get("name") or "Ürün",
                "size": it.get("size", "") or "",
                "color": it.get("color", "") or "",
                "quantity": int(it.get("quantity", 1) or 1),
                "price": float(it.get("price") or it.get("unit_price") or 0),
                "unit_price": float(it.get("unit_price") or it.get("price") or 0),
                "product_id": it.get("barcode") or it.get("product_id") or it.get("sku") or "",
                "discount_amount": it.get("discount_amount"),  # donmuş per-ürün indirim (W11214)
            } for it in _src]
            if _fix:
                await db.customer_returns.update_one(
                    {"id": existing.get("id")}, {"$set": {"items": _fix}})
        # decided/decision: pencere kilidi bunu esas alır (çıplak "approved" bayrağı kilitlemez).
        _dec, _kind, _by, _at = _decision_of(existing)
        return {"success": True, "return_id": existing.get("id"),
                "status": existing.get("status"), "created": False,
                "decided": _dec, "decision": {"kind": _kind, "by": _by, "at": _at}}

    # Sipariş kalemlerini customer_returns şemasına eşle
    src = order.get("items") or []
    items = [{
        "name": it.get("product_name") or it.get("name") or "Ürün",
        "size": it.get("size", "") or "",
        "color": it.get("color", "") or "",
        "quantity": int(it.get("quantity", 1) or 1),
        "price": float(it.get("price") or it.get("unit_price") or 0),
        "unit_price": float(it.get("unit_price") or it.get("price") or 0),
        "product_id": it.get("barcode") or it.get("product_id") or it.get("sku") or "",
        "discount_amount": it.get("discount_amount"),  # donmuş per-ürün indirim (W11214)
    } for it in src]

    # Sipariş durumu → customer_returns durumu
    _map = {
        "return_requested": "created", "return_approved": "approved",
        "return_in_transit": "in_transit", "returned": "received",
        "refunded": "refunded", "partial_refunded": "refunded",
        "return_rejected": "rejected",
    }
    cr_status = _map.get(order.get("status"), "created")

    rid = generate_id()
    rr = order.get("return_request") or {}
    rec = {
        "id": rid, "order_id": order_id, "order_number": order.get("order_number", ""),
        "user_id": order.get("user_id"), "items": items, "reason": "",
        "return_code": rr.get("return_code", "") or "", "mng_ok": False,
        "cargo_provider_name": order.get("cargo_provider_name", "") or "",
        "status": cr_status, "source": "ticimax_bridge",
        "created_at": order.get("created_at") or datetime.now(timezone.utc).isoformat(),
    }
    await db.customer_returns.insert_one({**rec})
    await db.orders.update_one({"id": order_id},
                               {"$set": {"return_request.return_id": rid}})
    return {"success": True, "return_id": rid, "status": cr_status, "created": True,
            "decided": False, "decision": {"kind": None, "by": "", "at": ""}}
