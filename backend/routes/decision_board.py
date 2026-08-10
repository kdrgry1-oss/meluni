"""
Karar Destek Kurulu — çok-personalı AI analiz raporu (panel içinden üretilir/yorumlanır).

Akış: 6 uzman persona (Üretim, Pazarlama/CRO, Reklam, Trend/Momentum, Operasyon/Stok,
Finans/Karlılık) canlı rapor verilerini paralel analiz eder → Şüpheci ajan zayıf önerileri
çürütür → Sentezci numaralı KARAR'lar üretir. Patron her kararın altına yorum + verdikt
(katılıyorum / reddediyorum / düzenle) yazar; hepsi sunucuda saklanır.

Rapor üretimi dakikalar sürer → arka plan görevi + durum yoklaması (status: running/done/failed).
LLM erişimi mevcut AI Chatbot ayarlarından gelir (ai_chatbot.llm_chat).
"""
import asyncio
import json
import re
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .deps import db, require_admin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/decision-board", tags=["admin-decision-board"])

_SYSTEM_USER = {"email": "system@decision-board", "role": "admin"}


# ---------------------------------------------------------------- veri toplama
async def _collect_data(period_days: int) -> dict:
    """Mevcut rapor uçlarının fonksiyonlarını doğrudan çağırıp LLM'e sığacak
    KOMPAKT bir veri paketi üretir (tam liste değil — özet + ilk N)."""
    from .reports import top_products, stock_report, cancel_return_products, sales

    now = datetime.now(timezone.utc)
    end = now.strftime("%Y-%m-%dT23:59:59")
    start = (now - timedelta(days=period_days)).strftime("%Y-%m-%d")
    start90 = (now - timedelta(days=90)).strftime("%Y-%m-%d")
    start365 = (now - timedelta(days=365)).strftime("%Y-%m-%d")

    top_p, top_90, stock, cr, weekly = await asyncio.gather(
        top_products(limit=2000, start_date=start, end_date=end, source=None, current_user=_SYSTEM_USER),
        top_products(limit=2000, start_date=start90, end_date=end, source=None, current_user=_SYSTEM_USER),
        stock_report(current_user=_SYSTEM_USER),
        cancel_return_products(start_date=start90, end_date=end, source=None, current_user=_SYSTEM_USER),
        sales(start_date=start365, end_date=end, group_by="week", source=None, current_user=_SYSTEM_USER),
    )

    items = top_p.get("items", [])
    items90 = {i.get("name"): i for i in top_90.get("items", [])}
    weeks = max(float(top_p.get("weeks") or 1), 0.1)
    weeks90 = max(float(top_90.get("weeks") or 1), 0.1)

    def _slim(p):
        n = p.get("name")
        p90 = items90.get(n) or {}
        rate_now = round((p.get("qty") or 0) / weeks, 2)
        rate_90 = round((p90.get("qty") or 0) / weeks90, 2)
        return {
            "ad": n,
            "sezon": p.get("collection") or "",
            "adet": p.get("qty"), "ciro": p.get("revenue"),
            "stok": p.get("current_stock"),
            "haftalik_hiz": rate_now,
            "haftalik_hiz_90g": rate_90,
            "ivme": round(rate_now - rate_90, 2),  # + ise ivmeleniyor, - ise sönüyor
            "hiz_kodu": (p.get("velocity") or {}).get("code"),
            "en_cok_beden": p.get("best_size"),
            "beden_dagilimi": {s.get("size"): s.get("qty") for s in (p.get("size_breakdown") or [])[:8]},
            "platformlar": {x.get("platform"): x.get("qty") for x in (p.get("platform_breakdown") or [])},
            "iptal": p.get("cancel_qty") or 0, "iade": p.get("return_qty") or 0,
        }

    sold = [p for p in items if (p.get("qty") or 0) > 0]
    sold.sort(key=lambda x: -(x.get("revenue") or 0))
    top40 = [_slim(p) for p in sold[:40]]

    # Hız dağılımı + toplamlar
    vel = {"green": 0, "yellow": 0, "red": 0}
    for p in items:
        c = (p.get("velocity") or {}).get("code")
        if c in vel:
            vel[c] += 1
    tot_rev = round(sum((p.get("revenue") or 0) for p in items), 2)
    tot_qty = sum((p.get("qty") or 0) for p in items)
    top5_rev = round(sum((p.get("revenue") or 0) for p in sold[:5]), 2)

    # Yavaş ürünlerde kilitli stok (kırmızı + stok>0)
    dead = [{"ad": p.get("name"), "stok": p.get("current_stock"), "adet": p.get("qty")}
            for p in items
            if (p.get("velocity") or {}).get("code") == "red" and (p.get("current_stock") or 0) > 20]
    dead.sort(key=lambda x: -(x.get("stok") or 0))

    # Tükenmiş ama satan ürünler (kayıp ciro)
    oos_names = {o.get("name") for o in stock.get("out_of_stock", [])}
    oos_selling = [{"ad": p.get("name"), "adet_90g": p.get("qty"), "ciro_90g": p.get("revenue")}
                   for n, p in items90.items() if n in oos_names and (p.get("qty") or 0) >= 5]
    oos_selling.sort(key=lambda x: -(x.get("adet_90g") or 0))

    # İade oranı yüksek ürünler (90g, min 10 satış)
    ret = []
    for n, p in items90.items():
        q = p.get("qty") or 0
        r = p.get("return_qty") or 0
        if q >= 10 and r > 0:
            ret.append({"ad": n, "adet": q, "iade": r, "iade_orani_pct": round(100 * r / q, 1)})
    ret.sort(key=lambda x: -x["iade_orani_pct"])

    return {
        "periyot_gun": period_days,
        "toplamlar": {"ciro": tot_rev, "adet": tot_qty, "urun_sayisi": len(items),
                      "ilk5_ciro": top5_rev,
                      "ilk5_ciro_pay_pct": round(100 * top5_rev / tot_rev, 1) if tot_rev else 0},
        "hiz_dagilimi": vel,
        "en_cok_satan_40": top40,
        "yavas_ve_stoklu_ilk15": dead[:15],
        "tukenmis_ama_satan": oos_selling[:15],
        "iade_orani_yuksek_ilk15": ret[:15],
        "kritik_stok_ilk15": (stock.get("low_stock") or [])[:15],
        "stok_toplam": stock.get("totals"),
        "haftalik_seri_52": (weekly.get("rows") or [])[-52:],
    }


# ---------------------------------------------------------------- LLM yardımcıları
async def _llm(system: str, user: str, fast: bool = True, max_tokens: int = 1500) -> str:
    from .ai_chatbot import get_ai_settings, _api_key_for, llm_chat
    settings = await get_ai_settings()
    api_key = _api_key_for(settings)
    if not api_key:
        raise RuntimeError("AI API anahtarı tanımlı değil (Ayarlar → AI Chatbot)")
    model = settings.get("fast_model", "claude-haiku-4-5") if fast else settings.get("model", "claude-sonnet-4-6")
    return await llm_chat(api_key, settings.get("provider", "anthropic"), model, system, user, max_tokens=max_tokens)


_PERSONAS = [
    ("Üretim Planlama Uzmanı",
     "İmalat/üretim planlama uzmanısın. Odağın: hangi ürünler ACİL yeniden üretime girmeli "
     "(hız yüksek + stok düşük — 'tukenmis_ama_satan' ve 'kritik_stok' listeleri), hangi bedenlerde açık var, "
     "FCSS(İlkbahar/Yaz)→FCFW(Sonbahar/Kış) sezon geçişi üretim takvimi (üretim ~21 gün sürer), "
     "hangi ürünlerin üretimi durdurulmalı."),
    ("Pazarlama & CRO Uzmanı",
     "Pazarlama ve dönüşüm optimizasyonu uzmanısın. Odağın: SARI (orta hız) ürünlerden hangileri küçük "
     "dokunuşla (görsel, fiyat, açıklama) yeşile çekilebilir; ciro yoğunlaşması riski (ilk5_ciro_pay_pct); "
     "kanal dengesizliği fırsatları (platformlar alanı — sitede zayıf/Trendyol'da güçlü ürünler)."),
    ("Performans Reklam Uzmanı",
     "Meta/Google performans reklamcısısın. Odağın: bütçe hangi ürünlere kaymalı (yeşil + stoğu derin; "
     "stoğu 20 altındaki ürüne reklam israf), kırmızı ürünlere tasfiye kampanyası, iade oranı yüksek ürünlere "
     "reklam riskli, kanal karması. ROAS mantığıyla düşün."),
    ("Satış Trendi & Momentum Analisti",
     "Zaman serisi analistisiniz. Odağın: haftalik_seri_52'de mevsimsellik (zirve/dip haftalar, şu an neredeyiz); "
     "'ivme' alanı pozitif olan yükselen yıldızlar ve negatif olan sönen ürünler; sezon (FCSS/FCFW) kırılımı."),
    ("Operasyon & Stok Uzmanı",
     "Depo/operasyon uzmanısın. Odağın: stok/hız oranı = kaç haftalık stok kaldı (stok ÷ haftalik_hiz) — "
     "4 haftadan az kalan yeşiller ACİL üretim, 26+ hafta yeten kırmızılar TASFİYE; yavas_ve_stoklu listesindeki "
     "ölü para; tukenmis_ama_satan = kayıp ciro."),
    ("Finans & Karlılık Analisti",
     "Finans analistisın. Odağın: iade_orani_yuksek ürünler gerçekte zarar ettiriyor olabilir (%15+ kritik); "
     "ciro kalitesi; stok değeri vs aylık ciro (kaç aylık ciro depoda yatıyor); ciro yoğunlaşması finansal riski."),
]

_SKEPTIC_SYS = (
    "Şüpheci karşı-uzman (devil's advocate) rolündesin. Türk kadın giyim e-ticareti bağlamında "
    "(sezonluk moda, hızlı trend, Trendyol komisyon baskısı) aşağıdaki uzman önerilerinin EN ZAYIFLARINI "
    "veriyle çürüt; ayakta kalan güçlü önerileri de açıkça belirt. En fazla 8 madde, Türkçe."
)

_SYNTH_SYS = (
    "Kurul sentezcisisin. 6 uzman analizi + şüpheci raporundan, teknik olmayan bir e-ticaret patronunun "
    "madde madde yorumlayacağı nihai karar raporunu SADECE GEÇERLİ JSON olarak üret (başka hiçbir şey yazma; "
    "kod bloğu işareti kullanma). Şema: {\"summary\": \"5-6 cümle durum özeti (sayılarla)\", "
    "\"methodology\": \"sabit eşik vs dinamik yüzdelik satış hızı tartışmasının kurul kararı, 3-4 cümle\", "
    "\"decisions\": [{\"title\": \"...\", \"urgency\": \"acil|bu_hafta|bu_ay|sezon_plani\", "
    "\"action\": \"tek cümle somut aksiyon\", \"rationale\": \"rakamlı gerekçe + hangi uzman(lar)\", "
    "\"products\": [\"en fazla 8 ürün adı\"], \"counterpoint\": \"şüphecinin itirazı ya da 'İtiraz yok — kurul hemfikir'\"}], "
    "\"conflicts\": \"uzlaşılamayan konular ve patrondan istenen yön kararı (markdown, kısa)\", "
    "\"rejected\": \"şüphecinin çürüttüğü, rapora girmeyen öneriler tek satırlık gerekçeyle (markdown)\"}. "
    "En fazla 12 karar; acil önce. Türkçe."
)


def _parse_json(text: str) -> dict:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S)
    try:
        return json.loads(t)
    except Exception:
        m = re.search(r"\{.*\}", t, flags=re.S)
        if m:
            return json.loads(m.group(0))
        raise


async def _set_progress(rid: str, msg: str):
    await db.decision_reports.update_one({"id": rid}, {"$set": {"progress": msg}})


async def _generate(rid: str, period_days: int):
    try:
        await _set_progress(rid, "Canlı rapor verileri toplanıyor…")
        data = await _collect_data(period_days)
        data_json = json.dumps(data, ensure_ascii=False, default=str)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        ctx = (f"Bugün: {today}. Firma: Facette — Türk kadın giyim; kendi sitesi + Trendyol + Hepsiburada. "
               f"FCSS=İlkbahar/Yaz, FCFW=Sonbahar/Kış. Mevcut satış hızı kodlaması SABİT eşik "
               f"(yeşil=haftada 5+, sarı=1-4, kırmızı=ayda 0-2); alternatif DİNAMİK yüzdelik (üst %30 / orta %40 / alt %30). "
               f"Bu metodoloji hakkında da net pozisyon al.\n\nVERİ PAKETİ (son {period_days} gün + 90 gün + 52 hafta):\n{data_json}")

        await _set_progress(rid, "6 uzman persona analiz ediyor…")
        persona_tasks = [
            _llm(f"{brief} Somut ve SAYISAL bulgular çıkar (ürün adı + rakam olmadan bulgu sayılmaz). "
                 f"En fazla 6 bulgu + 5 öneri (her öneri: aksiyon + aciliyet [acil/bu_hafta/bu_ay/sezon_plani] + gerekçe). Türkçe, markdown.",
                 ctx, fast=True, max_tokens=1400)
            for _, brief in _PERSONAS
        ]
        persona_out = await asyncio.gather(*persona_tasks, return_exceptions=True)
        analyses = []
        for (name, _), out in zip(_PERSONAS, persona_out):
            if isinstance(out, Exception):
                logger.warning("decision-board persona %s hata: %s", name, out)
            else:
                analyses.append(f"### {name}\n{out}")
        if len(analyses) < 3:
            raise RuntimeError("Uzman analizlerinin çoğu başarısız oldu")
        analyses_txt = "\n\n".join(analyses)

        await _set_progress(rid, "Şüpheci ajan önerileri çürütmeye çalışıyor…")
        skeptic = await _llm(_SKEPTIC_SYS, f"{ctx[:4000]}\n\nUZMAN ANALİZLERİ:\n{analyses_txt}",
                             fast=False, max_tokens=1500)

        await _set_progress(rid, "Sentezci nihai karar raporunu yazıyor…")
        synth_raw = await _llm(_SYNTH_SYS,
                               f"UZMAN ANALİZLERİ:\n{analyses_txt}\n\nŞÜPHECİ RAPORU:\n{skeptic}",
                               fast=False, max_tokens=4500)
        report = _parse_json(synth_raw)

        decisions = []
        for i, d in enumerate((report.get("decisions") or [])[:12], start=1):
            decisions.append({
                "id": f"KARAR-{i}",
                "title": str(d.get("title") or "")[:200],
                "urgency": d.get("urgency") if d.get("urgency") in ("acil", "bu_hafta", "bu_ay", "sezon_plani") else "bu_ay",
                "action": str(d.get("action") or ""),
                "rationale": str(d.get("rationale") or ""),
                "products": [str(x) for x in (d.get("products") or [])[:8]],
                "counterpoint": str(d.get("counterpoint") or ""),
                "user_verdict": "",   # katiliyorum | reddediyorum | duzenle
                "user_comment": "",
                "commented_at": None,
            })

        await db.decision_reports.update_one({"id": rid}, {"$set": {
            "status": "done", "progress": "Tamamlandı",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "summary": str(report.get("summary") or ""),
            "methodology": str(report.get("methodology") or ""),
            "decisions": decisions,
            "conflicts": str(report.get("conflicts") or ""),
            "rejected": str(report.get("rejected") or ""),
            "raw_analyses": analyses_txt[:120000],
            "raw_skeptic": skeptic[:30000],
        }})
    except Exception as e:
        logger.exception("decision-board üretim hatası")
        await db.decision_reports.update_one({"id": rid}, {"$set": {
            "status": "failed", "progress": f"Hata: {e}",
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }})


# ---------------------------------------------------------------- uçlar
class GenerateBody(BaseModel):
    period_days: int = 30


class VerdictBody(BaseModel):
    user_verdict: str = ""
    user_comment: str = ""


@router.post("/generate")
async def generate_report(body: GenerateBody, current_user: dict = Depends(require_admin)):
    period = body.period_days if body.period_days in (30, 60, 90) else 30
    # Aynı anda tek üretim: 15 dk'dan taze "running" varsa reddet
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    running = await db.decision_reports.find_one({"status": "running", "created_at": {"$gte": cutoff}})
    if running:
        raise HTTPException(409, "Zaten üretilen bir rapor var — bitmesini bekleyin")
    rid = str(uuid.uuid4())
    await db.decision_reports.insert_one({
        "id": rid, "status": "running", "progress": "Başlatıldı…",
        "period_days": period,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": current_user.get("email"),
        "decisions": [],
    })
    asyncio.create_task(_generate(rid, period))
    return {"id": rid, "status": "running"}


@router.post("/import")
async def import_report(payload: dict, current_user: dict = Depends(require_admin)):
    """Dışarıda üretilmiş (ör. derin analiz oturumu) hazır kurul raporunu panele ekler."""
    rid = str(uuid.uuid4())
    decisions = []
    for i, d in enumerate((payload.get("decisions") or [])[:20], start=1):
        decisions.append({
            "id": f"KARAR-{i}",
            "title": str(d.get("title") or "")[:200],
            "urgency": d.get("urgency") if d.get("urgency") in ("acil", "bu_hafta", "bu_ay", "sezon_plani") else "bu_ay",
            "action": str(d.get("action") or ""),
            "rationale": str(d.get("rationale") or ""),
            "products": [str(x) for x in (d.get("products") or [])[:8]],
            "counterpoint": str(d.get("counterpoint") or ""),
            "user_verdict": "", "user_comment": "", "commented_at": None,
        })
    now = datetime.now(timezone.utc).isoformat()
    await db.decision_reports.insert_one({
        "id": rid, "status": "done", "progress": "Tamamlandı (derin analiz — içe aktarıldı)",
        "period_days": int(payload.get("period_days") or 30),
        "created_at": now, "finished_at": now,
        "created_by": current_user.get("email"),
        "summary": str(payload.get("summary") or ""),
        "methodology": str(payload.get("methodology") or ""),
        "decisions": decisions,
        "conflicts": str(payload.get("conflicts") or ""),
        "rejected": str(payload.get("rejected") or ""),
    })
    return {"id": rid, "decisions": len(decisions)}


@router.get("")
async def list_reports(current_user: dict = Depends(require_admin)):
    rows = await db.decision_reports.find(
        {}, {"_id": 0, "id": 1, "status": 1, "progress": 1, "period_days": 1,
             "created_at": 1, "finished_at": 1, "summary": 1,
             "decisions": {"$slice": 0}}
    ).sort("created_at", -1).to_list(30)
    for r in rows:
        r.pop("decisions", None)
    return {"items": rows}


@router.get("/{rid}")
async def get_report(rid: str, current_user: dict = Depends(require_admin)):
    doc = await db.decision_reports.find_one({"id": rid}, {"_id": 0, "raw_analyses": 0, "raw_skeptic": 0})
    if not doc:
        raise HTTPException(404, "Rapor bulunamadı")
    return doc


@router.get("/{rid}/detay")
async def get_report_raw(rid: str, current_user: dict = Depends(require_admin)):
    """Uzman ham analizleri + şüpheci raporu (isteyen patron kurulun mutfağını görebilsin)."""
    doc = await db.decision_reports.find_one({"id": rid}, {"_id": 0, "raw_analyses": 1, "raw_skeptic": 1})
    if not doc:
        raise HTTPException(404, "Rapor bulunamadı")
    return doc


@router.put("/{rid}/decisions/{did}")
async def save_verdict(rid: str, did: str, body: VerdictBody, current_user: dict = Depends(require_admin)):
    verdict = body.user_verdict if body.user_verdict in ("", "katiliyorum", "reddediyorum", "duzenle") else ""
    res = await db.decision_reports.update_one(
        {"id": rid, "decisions.id": did},
        {"$set": {"decisions.$.user_verdict": verdict,
                  "decisions.$.user_comment": body.user_comment[:4000],
                  "decisions.$.commented_at": datetime.now(timezone.utc).isoformat()}})
    if res.matched_count == 0:
        raise HTTPException(404, "Karar bulunamadı")
    return {"ok": True}
