"""
supplier_codes.py — Tedarikçiden gelen ürünlere BİZE ÖZEL kod üretir.

Neden: tedarikçinin barkodu/stok kodu bize ait değildir. Aynı barkod başka
bayilere de verilir, tedarikçi değişince kod değişir ve pazaryerlerinde
çakışma çıkar. Bu yüzden her ürün için kendi kodlarımızı üretip tedarikçinin
kodunu yalnız EŞLEME anahtarı olarak saklıyoruz.

Üretilen üç kod:
  model_code : MEL-M000123          (ürün/model bazında — varyantlar paylaşır)
  stock_code : MEL-M000123-01       (varyant bazında, satılabilir birim)
  barcode    : 2000001234567         (EAN-13, GS1 "dahili kullanım" ön eki)

Barkod ön eki 200-299 aralığı GS1 tarafından işletme-içi kullanıma ayrılmıştır;
kayıt gerektirmez ve küresel GTIN'lerle çakışmaz. Ürünleri GS1'e kayıtlı gerçek
barkodlarla satacaksanız BARCODE_PREFIX'i kendi GS1 firma ön ekinizle değiştirin.

Eşleme `supplier_code_map` koleksiyonunda kalıcıdır: aynı tedarikçi ürünü ikinci
kez içe aktarıldığında AYNI kodları alır (yeniden numaralandırma olmaz).
"""
from __future__ import annotations

import os
import re

# GS1 dahili-kullanım ön eki (200-299). Kendi GS1 firma ön ekiniz varsa değiştirin.
BARCODE_PREFIX = (os.environ.get("BARCODE_PREFIX") or "200").strip()
# Kod ön eki — markaya göre. MEL = meluni.
CODE_PREFIX = (os.environ.get("PRODUCT_CODE_PREFIX") or "MEL").strip().upper()

_COUNTER_ID = "supplier_codes"


def ean13_check_digit(body12: str) -> str:
    """EAN-13 kontrol hanesi: soldan tek konumlar x1, çift konumlar x3."""
    if len(body12) != 12 or not body12.isdigit():
        raise ValueError("EAN-13 gövdesi 12 haneli olmalı")
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(body12))
    return str((10 - (total % 10)) % 10)


def build_ean13(sequence: int) -> str:
    """Sıra numarasından geçerli EAN-13 üretir."""
    prefix = re.sub(r"\D", "", BARCODE_PREFIX) or "200"
    body = f"{prefix}{sequence:0{12 - len(prefix)}d}"
    if len(body) != 12:
        raise ValueError(f"Barkod gövdesi 12 hane olmalı, {len(body)} geldi (ön ek çok uzun)")
    return body + ean13_check_digit(body)


async def _next_sequence(db, field: str) -> int:
    """Atomik sayaç. find_one_and_update ile eşzamanlı içe aktarmada da tekilliği korur."""
    doc = await db.counters.find_one_and_update(
        {"id": _COUNTER_ID},
        {"$inc": {field: 1}},
        upsert=True,
        return_document=True,
    )
    # Motor sürümüne göre return_document bool/enum farkı olabilir; garanti altına al.
    if not doc or field not in doc:
        doc = await db.counters.find_one({"id": _COUNTER_ID}) or {}
    return int(doc.get(field) or 1)


def supplier_key(supplier: str, model_ref: str) -> str:
    """Tedarikçi + tedarikçinin model referansından kararlı eşleme anahtarı."""
    return f"{(supplier or 'xml').strip().lower()}::{(model_ref or '').strip().lower()}"


async def get_or_create_model_code(db, supplier: str, model_ref: str,
                                   group_ref: str = "") -> str:
    """Tedarikçi modeline karşılık gelen BİZİM model kodumuz. Idempotent.

    group_ref verilirse model kodu ONUN üzerinden üretilir. Tedarikçi aynı
    modelin her rengini AYRI ürün olarak veriyor (her birinin kendi
    UrunKartiID'si var) ama hepsi ortak bir grup kodu taşıyor. Model kodunu
    gruba bağlamak iki şeyi sağlar:
      • Vitrinde "diğer renkler" birbirine bağlanır.
      • Pazaryerinde renkler TEK KARTTA toplanır (Trendyol'da ortak kart,
        productMainId olarak bu kod gider) — her renk ayrı ürün olarak
        listelenmez.
    """
    key = supplier_key(supplier, group_ref or model_ref)
    existing = await db.supplier_code_map.find_one({"key": key}, {"_id": 0, "model_code": 1})
    if existing and existing.get("model_code"):
        return existing["model_code"]

    seq = await _next_sequence(db, "model_seq")
    code = f"{CODE_PREFIX}-M{seq:06d}"
    # upsert: iki eşzamanlı içe aktarma aynı anda gelirse ikincisi mevcut kaydı görür.
    await db.supplier_code_map.update_one(
        {"key": key},
        {"$setOnInsert": {
            "key": key, "supplier": supplier, "model_ref": model_ref,
            "group_ref": group_ref or "", "model_code": code, "variants": {},
        }},
        upsert=True,
    )
    doc = await db.supplier_code_map.find_one({"key": key}, {"_id": 0, "model_code": 1})
    return (doc or {}).get("model_code") or code


async def get_or_create_variant_codes(
    db, supplier: str, model_ref: str, variant_ref: str, group_ref: str = ""
) -> dict:
    """Varyant için bizim stok kodumuz + barkodumuz. Idempotent.

    variant_ref tedarikçinin varyant anahtarıdır (beden/renk kodu veya barkodu).
    Varyantsız üründe "0" verilebilir.

    DİKKAT — group_ref verildiğinde model kodu renkler arasında PAYLAŞILIR, bu
    yüzden varyant anahtarına ürünün kendi referansı da katılır. Aksi hâlde iki
    farklı rengin aynı bedeni ("S") aynı stok kodunu alır ve envanter birbirine
    karışır. Ortak olan MODEL kodudur; stok kodu ve barkod her zaman benzersiz.
    """
    key = supplier_key(supplier, group_ref or model_ref)
    model_code = await get_or_create_model_code(db, supplier, model_ref, group_ref)
    vref = (variant_ref or "0").strip().lower()
    if group_ref:
        vref = f"{(model_ref or '').strip().lower()}|{vref}"
    # Mongo alan adlarında nokta ve $ kullanılamaz.
    vkey = re.sub(r"[.$\x00]", "_", vref) or "0"

    doc = await db.supplier_code_map.find_one({"key": key}, {"_id": 0, "variants": 1})
    hit = ((doc or {}).get("variants") or {}).get(vkey)
    if hit and hit.get("stock_code") and hit.get("barcode"):
        return {"model_code": model_code, **hit}

    vseq = len((doc or {}).get("variants") or {}) + 1
    bseq = await _next_sequence(db, "barcode_seq")
    codes = {
        "stock_code": f"{model_code}-{vseq:02d}",
        "barcode": build_ean13(bseq),
        "supplier_variant_ref": variant_ref or "",
    }
    await db.supplier_code_map.update_one(
        {"key": key},
        {"$set": {f"variants.{vkey}": codes}},
    )
    return {"model_code": model_code, **codes}
