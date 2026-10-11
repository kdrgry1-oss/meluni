"""
Telefon / adres (müşteri + influencer) — KVKK koruma katmanı.

Sahip (2026-10): "Müşteri ve influencer adres ve telefonlarının güvenliği son derece önemli."
Bu modül TEK merkezdir; TCKN/VKN için security/pii.py ne ise telefon/adres için odur.

1) MASKELEME (yanıt / liste)
   Personel listeleri (sipariş, üye, influencer, iade, rapor, arama, panel) tam telefon / açık adres
   DÖNMEZ:
     telefon  → "+90 *** *** ** 33"   (yalnız son 2 hane)
     adres    → "••••••"               (açık adres satırı, posta kodu, mahalle; il/ilçe AÇIK kalır)
   Maskeli değer istemciden geri gelirse (düzenleme formu açılmadan kaydedilirse) "değişmedi" sayılır
   ve mevcut değer korunur (``carry_over_masked_contact``) — maske ASLA gerçek değer diye yazılmaz.

2) AÇMA (reveal) — tam değer yalnız TEK kaydın detayında / "Göster" ucunda, doğru izinle; her açma
   ``note_contact_reveal`` ile sayılır → security.events.note_pii_reveal (PRIV07 toplu çözme alarmı:
   saatte >50 → kritik uyarı + 1 saat engel) telefon/adresi de kapsar; ayrıca auth_audit_logs'a
   "pii_contact_view" yazılır (değerin kendisi YAZILMAZ).

3) AT-REST ŞİFRELEME (influencer, influencer_pr, adres defteri, üye profil telefonu)
     <alan>_enc    security.crypto.encrypt ("v1:<fernet>") — gerçek değer yalnız bundan çözülür
     <alan>_hash   telefon için ALAN-AYRIMLI HMAC ("kid:hex", tüm anahtarlarla arama) — "bu numara
                   kayıtlı mı / bu numaraya ait kayıt" sorguları düz değer olmadan çalışır
     <alan>_last2  telefon son 2 hane (maskeli gösterim) · IBAN için <alan>_last4
   Adres defterinde ayrıca ``address_hash`` (adres+il+ilçe, normalize) → tekilleştirme sorgusu.
   Mühürleme şifre çözümü DOĞRULANMADAN düz değeri silmez (``seal_value``).

4) SIZINTI SÜZGEÇLERİ
   ``scrub_contact`` (log / ham pazaryeri yükü / LLM bağlamı için KOPYA) ve ``scrub_contact_text``
   (serbest metinde TR telefon numarası + adres kalıbı) — log, uyarı e-postası, push, LLM.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import logging
import re
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

ADDRESS_MASK = "••••••"
MASK_CHARS = ("*", "•")

# ───────────────────────────── anahtar sınıflandırma ─────────────────────────────
# Anahtar adları küçük harfe çevrilip '_', '-', boşluk atılarak karşılaştırılır.
_PHONE_KEYS = {
    "phone", "phonenumber", "phoneno", "gsm", "gsmno", "gsmnumber", "mobile", "mobilephone", "mobileno",
    "cellphone", "cep", "ceptel", "ceptelefonu", "telefon", "telefonno", "tel", "telcep", "telno",
    "customerphone", "userphone", "buyerphone", "receiverphone", "recipientphone", "alicitel",
    "alicitelefon", "alicitelefonu", "phone2", "secondphone", "contactphone", "deliveryphone",
    "billingphone", "shippingphone", "phoneraw", "msisdn",
}
_ADDRESS_KEYS = {
    "address", "address1", "address2", "addressline", "addressline1", "addressline2", "fulladdress",
    "openaddress", "adres", "acikadres", "street", "streetaddress", "neighborhood", "neighbourhood",
    "mahalle", "building", "apartment", "doorno", "postalcode", "postcode", "zip", "zipcode",
    "addressdetail", "shippingaddressdetail", "customeraddress", "deliveryaddress", "receiveraddress",
    "aliciadres", "aliciadresi", "pickupaddress", "addresstext", "addressfull",
}
_SEALED_SUFFIXES = ("_enc", "_hash", "_last2", "_last4")

# Sipariş benzeri belgelerde müşteri iletişim bilgisi taşıyan kapsayıcılar (bilinen yollar).
# DİKKAT: gönderici/şirket adresi (sender, company, warehouse…) burada YOK — maskelenmez.
ORDER_CONTACT_CONTAINERS = (
    "shipping_address", "billing_address", "invoice_address", "delivery_address", "customer", "buyer",
    "shipmentAddress", "invoiceAddress", "receiver", "recipient", "customer_info", "user_data",
    "return_request", "pickup", "pickup_address", "contact",
)


def _nk(k: Any) -> str:
    return re.sub(r"[_\-\s]", "", str(k or "")).lower()


def is_phone_key(k: Any) -> bool:
    return _nk(k) in _PHONE_KEYS


def is_address_key(k: Any) -> bool:
    return _nk(k) in _ADDRESS_KEYS


def _is_present(v: Any) -> bool:
    return v is not None and not isinstance(v, (dict, list, bool)) and str(v).strip() != ""


def is_masked(v: Any) -> bool:
    return isinstance(v, str) and any(c in v for c in MASK_CHARS)


def is_masked_address(v: Any) -> bool:
    """Adres maskesi: '••••••' belirtecini içeren (ör. ekranın birleştirdiği '••••••, Kadıköy, İstanbul')
    ya da yalnız maske karakterlerinden oluşan değer. Gerçek adreste geçebilen tek bir '*' (ör. 'Blok *A')
    maske SAYILMAZ → kullanıcının yazdığı adres yutulmaz."""
    if not isinstance(v, str) or v.strip() == "":
        return False
    return ADDRESS_MASK in v or all(ch in "•*" for ch in v.strip())


def is_masked_for(kind: str, v: Any) -> bool:
    return is_masked_address(v) if kind == "address" else is_masked(v)


# ───────────────────────────── telefon ─────────────────────────────
def phone_digits(v: Any) -> str:
    """Yalnız rakamlar; uluslararası '00' öneki atılır."""
    d = re.sub(r"\D", "", str(v or ""))
    if d.startswith("00"):
        d = d[2:]
    return d


def normalize_phone(v: Any) -> str:
    """Arama/hash anahtarı: TR numarasında son 10 hane ('5xxxxxxxxx'); kısa numarada tüm rakamlar."""
    d = phone_digits(v)
    return d[-10:] if len(d) >= 10 else d


def mask_phone(v: Any = None, last2: Optional[str] = None) -> str:
    """'+90 *** *** ** 33' — değer ya da yalnız son 2 hane ile. Boş → ''."""
    if last2 is None:
        if not _is_present(v):
            return ""
        if is_masked(v):
            return str(v)
        d = normalize_phone(v)
        if not d:
            return ""
        last2 = d[-2:]
    last2 = str(last2 or "")
    if not last2:
        return ""
    # Düz (sipariş) ve mühürlü (üye/influencer) kayıtta AYNI görünüm: yalnız son 2 hane.
    return f"+90 *** *** ** {last2}"


def mask_address(v: Any) -> str:
    return ADDRESS_MASK if _is_present(v) else ""


# ───────────────────────────── hash (arama / tekilleştirme) ─────────────────────────────
_PHONE_LABEL = b"meluni:pii-phone:v1"
_ADDR_LABEL = b"meluni:pii-address:v1"


def _keys(label: bytes) -> list:
    from security.pii import derived_hash_keys
    return derived_hash_keys(label)


def _hmac_hex(key: bytes, label: bytes, norm: str) -> str:
    return hmac.new(key, label + b"|" + norm.encode("utf-8"), hashlib.sha256).hexdigest()


def phone_hash(v: Any) -> str:
    """Birincil anahtarla 'kid:hex' (boş değer / anahtar yok → '')."""
    norm = normalize_phone(v)
    ks = _keys(_PHONE_LABEL)
    if len(norm) < 7 or not ks:
        return ""
    kid, key = ks[0]
    return f"{kid}:{_hmac_hex(key, _PHONE_LABEL, norm)}"


def phone_hashes(v: Any) -> list:
    """Mevcut TÜM anahtarlarla 'kid:hex' listesi (arama: anahtar rotasyonunda eski kayıt bulunur)."""
    norm = normalize_phone(v)
    if len(norm) < 7:
        return []
    return [f"{kid}:{_hmac_hex(key, _PHONE_LABEL, norm)}" for kid, key in _keys(_PHONE_LABEL)]


def primary_kid() -> str:
    ks = _keys(_PHONE_LABEL)
    return ks[0][0] if ks else ""


def _norm_text(s: Any) -> str:
    s = str(s or "").strip().replace("İ", "i").replace("I", "ı").casefold()
    return " ".join(s.split())


def _address_norm(line: Any, city: Any = "", district: Any = "") -> str:
    ln = _norm_text(line)
    if not ln:
        return ""
    return f"{ln}|{_norm_text(city)}|{_norm_text(district)}"


def address_hash(line: Any, city: Any = "", district: Any = "") -> str:
    norm = _address_norm(line, city, district)
    ks = _keys(_ADDR_LABEL)
    if not norm or not ks:
        return ""
    kid, key = ks[0]
    return f"{kid}:{_hmac_hex(key, _ADDR_LABEL, norm)}"


def address_hashes(line: Any, city: Any = "", district: Any = "") -> list:
    norm = _address_norm(line, city, district)
    if not norm:
        return []
    return [f"{kid}:{_hmac_hex(key, _ADDR_LABEL, norm)}" for kid, key in _keys(_ADDR_LABEL)]


def legacy_phone_variants(v: Any) -> list:
    """Henüz mühürlenmemiş (backfill öncesi) düz kayıtlar için TAM eşleşme biçimleri (regex YOK)."""
    n = normalize_phone(v)
    if len(n) != 10:
        return [n] if n else []
    return [n, "0" + n, "90" + n, "+90" + n, "+90 " + n, "0 " + n]


def phone_lookup(field: str, v: Any) -> dict:
    """'Bu numaraya ait kayıt' sorgu koşulu: <field>_hash ∈ {tüm anahtarlar} + backfill öncesi düz
    kayıt için tam biçim eşleşmesi (regex YOK). Numara geçersizse HİÇBİR kayıtla eşleşmeyen koşul."""
    hs = phone_hashes(v)
    variants = legacy_phone_variants(v)
    if not hs and not variants:
        return {"$or": [{f"{field}_hash": "\x00yok"}]}
    ors = []
    if hs:
        ors.append({f"{field}_hash": {"$in": hs}})
    if variants:
        ors.append({field: {"$in": variants}})
    return {"$or": ors}


# ───────────────────────────── tekil değer mühürleme ─────────────────────────────
_IBAN_KEYS = {"iban", "ibanno", "ibannumber", "bankiban"}


def is_iban_key(k: Any) -> bool:
    return _nk(k) in _IBAN_KEYS


def seal_value(kind: str, value: Any) -> dict:
    """{suffix: değer} — boş/maskeli değerde {}. Şifre çözümü doğrulanır: çözülemeyen şifreli değer
    ASLA üretilmez (düz değer sessizce kaybolmaz)."""
    if not _is_present(value) or is_masked_for(kind, value):
        return {}
    if kind == "tax":
        from security.pii import seal_tax_id
        s = seal_tax_id(value)
        return {"_enc": s["enc"], "_hash": s["hash"], "_last2": s["last2"]} if s else {}
    from security.crypto import encrypt, decrypt
    plain = str(value).strip()
    enc = encrypt(plain)
    if not enc or decrypt(enc) != plain:
        raise RuntimeError("iletişim verisi şifreleme doğrulaması başarısız")
    out = {"_enc": enc}
    if kind == "phone":
        out["_hash"] = phone_hash(plain)
        out["_last2"] = normalize_phone(plain)[-2:]
    elif kind == "iban":
        out["_last4"] = re.sub(r"\s", "", plain)[-4:]
    return out


def _drop_sealed(c: dict, field: str) -> None:
    for s in _SEALED_SUFFIXES:
        c.pop(f"{field}{s}", None)


def _has_sealed(c: dict, field: str) -> bool:
    return any(f"{field}{s}" in c for s in _SEALED_SUFFIXES)


def seal_field(c: dict, field: str, kind: str) -> bool:
    """Kapsayıcıdaki düz değeri yerinde mühürler (pii.seal_field ile aynı sözleşme):
      dolu düz değer → <alan>_enc/_hash/_last2 yazılır, düz anahtar SİLİNİR
      maskeli değer  → düz anahtar atılır (mevcut mühür korunur)
      '' / None      → mühür varsa kaldırılır (kullanıcı alanı temizledi)."""
    if not isinstance(c, dict) or field not in c:
        return False
    v = c.get(field)
    if isinstance(v, (dict, list)):
        return False
    if is_masked_for(kind, v):
        c.pop(field, None)
        return True
    if not _is_present(v):
        if _has_sealed(c, field):
            _drop_sealed(c, field)
            return True
        return False
    sealed = seal_value(kind, v)
    _drop_sealed(c, field)
    for suf, val in sealed.items():
        c[f"{field}{suf}"] = val
    c.pop(field, None)
    return True


def _plain_usable(c: dict, field: str, v: Any) -> bool:
    """Düz değer gerçek veri mi? (maske artığı değil). Telefon maskesi ('*' içerir) gerçek numara olamaz;
    adres için yalnız '••••••' belirteci maske sayılır."""
    if not _is_present(v) or is_masked_address(v):
        return False
    if is_masked(v) and (is_phone_key(field) or f"{field}_enc" in c):
        return False
    return True


def open_field(c: Optional[dict], field: str) -> str:
    """Gerçek değer: önce (eski) düz değer, yoksa <alan>_enc çözülür. Bulunamazsa ''."""
    if not isinstance(c, dict):
        return ""
    v = c.get(field)
    if _plain_usable(c, field, v):
        return str(v)
    enc = c.get(f"{field}_enc")
    if enc:
        try:
            from security.crypto import decrypt
            return decrypt(enc) or ""
        except Exception:
            logger.error("iletişim verisi şifresi çözülemedi (anahtar?)")
    return ""


def has_value(c: Optional[dict], field: str) -> bool:
    if not isinstance(c, dict):
        return False
    return _plain_usable(c, field, c.get(field)) or bool(c.get(f"{field}_enc"))


def masked_view(c: Optional[dict], field: str, kind: str) -> str:
    """Ekran için maskeli değer ('' yoksa) — düz ya da mühürlü kayıttan."""
    if not isinstance(c, dict):
        return ""
    v = c.get(field)
    if kind == "phone":
        if c.get(f"{field}_last2"):
            return mask_phone(last2=c.get(f"{field}_last2"))
        if _is_present(v):
            return mask_phone(v)
        return mask_phone(open_field(c, field)) if c.get(f"{field}_enc") else ""
    if kind == "iban":
        l4 = c.get(f"{field}_last4") or re.sub(r"\s", "", str(v or ""))[-4:]
        return f"TR** **** **** **** **** **{l4[:2]} {l4[2:]}" if l4 else ""
    if kind == "tax":
        from security.pii import tax_id_last2_view
        return tax_id_last2_view(c, field)
    return ADDRESS_MASK if (_is_present(v) or c.get(f"{field}_enc")) else ""


# ───────────────────────────── belge türleri (at-rest) ─────────────────────────────
from security.pii import TAX_ID_KEYS as _TAX_KEYS  # noqa: E402

_INF_TOP = {"phone": "phone", "gsm": "phone", "mobile": "phone", "telefon": "phone",
            "adres": "address", "address": "address", "acik_adres": "address",
            "iban": "iban", "iban_no": "iban", "tc": "tax", **{k: "tax" for k in _TAX_KEYS}}
_INF_ADDR = {"phone": "phone", "gsm": "phone", "telefon": "phone", "adres": "address",
             "address": "address", "acik_adres": "address", "posta_kodu": "address", "postal_code": "address"}
_INF_BANK = {"iban": "iban", "iban_no": "iban", "tc": "tax", **{k: "tax" for k in _TAX_KEYS}}

# kind → (üst-seviye {alan: tür}, {kapsayıcı: {alan: tür}})
KIND_SPECS = {
    "influencer": (_INF_TOP, {"shipping_address": _INF_ADDR, "bank_info": _INF_BANK,
                              "payment_info": _INF_BANK, "billing_info": _INF_BANK}),
    "influencer_pr": ({"phone": "phone", "adres": "address", "address": "address"}, {}),
    "address": ({"phone": "phone", "address": "address"}, {}),
    "user": ({"phone": "phone"}, {}),
}


def kind_spec(kind: str):
    return KIND_SPECS[kind]


def _targets(doc: dict, kind: str):
    tops, conts = KIND_SPECS[kind]
    yield doc, tops, ""
    for cname, fields in conts.items():
        sub = doc.get(cname)
        if isinstance(sub, dict):
            yield sub, fields, cname


def is_staff_doc(doc: Optional[dict]) -> bool:
    d = doc or {}
    return d.get("is_admin") is True or d.get("is_super_admin") is True


def seal_doc(doc: Optional[dict], kind: str) -> Optional[dict]:
    """Belgedeki bilinen tüm iletişim alanlarını YERİNDE mühürler; belgeyi döndürür.
    'address' türünde address_hash da yazılır. 'user' türünde PERSONEL kaydına dokunulmaz
    (personel telefonu MFA'da mfa_phone_enc ile ayrıca şifreli tutulur)."""
    if not isinstance(doc, dict):
        return doc
    if kind == "user" and is_staff_doc(doc):
        return doc
    # NOT: address_hash, "address" alanının mühür son ekiyle (_hash) aynı ada sahiptir → mühürleme
    # SONRASI yazılır (seal_field eski mühür artıklarını temizlerken silmesin).
    _ah = None
    if kind == "address" and _is_present(doc.get("address")) and not is_masked_address(doc.get("address")):
        _ah = address_hash(doc.get("address"), doc.get("city"), doc.get("district"))
    for cont, fields, _ in _targets(doc, kind):
        for f, k in fields.items():
            if f in cont:
                seal_field(cont, f, k)
    if _ah:
        doc["address_hash"] = _ah
    return doc


def plain_paths(doc: Optional[dict], kind: str) -> list:
    """Düz (mühürlenmemiş) değer taşıyan nokta-yollar."""
    out = []
    if not isinstance(doc, dict):
        return out
    if kind == "user" and is_staff_doc(doc):
        return out
    for cont, fields, cname in _targets(doc, kind):
        for f in fields:
            v = cont.get(f)
            if _is_present(v):
                out.append(f"{cname}.{f}" if cname else f)
    return out


def iter_plain_query_paths(kind: str):
    """Düz değer taşıyabilecek tüm nokta-yollar (backfill / gece ağı sorgusu)."""
    tops, conts = KIND_SPECS[kind]
    for f in tops:
        yield f, tops[f]
    for c, fields in conts.items():
        for f in fields:
            yield f"{c}.{f}", fields[f]


def count_sealed(doc: Optional[dict], kind: str) -> int:
    n = 0
    if not isinstance(doc, dict):
        return 0
    for cont, fields, _ in _targets(doc, kind):
        for f in fields:
            if has_value(cont, f):
                n += 1
    return n


def reveal_doc(doc: Optional[dict], kind: str) -> Optional[dict]:
    """Gerçek değerleri düz anahtarlarına geri koyan KOPYA (girdi değişmez); mühür alanları atılır.
    Yalnız gerçek değere işlevsel/yasal ihtiyacı olan okuyucular içindir (kargo, irsaliye, SMS, sahibin
    kendi adres defteri, KVKK veri dökümü, yetkili 'Göster'). Dönen kopya ASLA DB'ye yazılmaz."""
    if not isinstance(doc, dict):
        return doc
    out = dict(doc)
    tops, conts = KIND_SPECS[kind]
    for cname in conts:
        if isinstance(out.get(cname), dict):
            out[cname] = dict(out[cname])
    for cont, fields, _ in _targets(out, kind):
        for f in fields:
            if f"{f}_enc" in cont or (f in cont and is_masked_for(fields[f], cont.get(f))):
                cont[f] = open_field(cont, f)
            _drop_sealed(cont, f)
    out.pop("address_hash", None)
    return out


def mask_doc(doc: Optional[dict], kind: str) -> Optional[dict]:
    """Yanıt için YERİNDE maskeler (düz ya da mühürlü): telefon '+90 5** *** ** 33', adres '••••••',
    IBAN 'TR** … 12 34', TCKN '*********12'; _enc/_hash/_last* ve address_hash çıkarılır."""
    if not isinstance(doc, dict):
        return doc
    for cont, fields, _ in _targets(doc, kind):
        for f, k in fields.items():
            if f in cont or _has_sealed(cont, f):
                view = masked_view(cont, f, k)
                _drop_sealed(cont, f)
                cont[f] = view
    doc.pop("address_hash", None)
    return doc


def mask_docs(docs: Iterable, kind: str):
    for d in docs or []:
        mask_doc(d, kind)
    return docs


def drop_client_sealed(doc: Optional[dict], kind: str) -> Optional[dict]:
    """İstemci girdisindeki <alan>_enc/_hash/_last* anahtarlarını atar (sahte şifreli değer / hash
    enjeksiyonu = arama zehirleme önlenir). Mühür alanlarını YALNIZ sunucu üretir."""
    if not isinstance(doc, dict):
        return doc
    for cont, fields, _ in _targets(doc, kind):
        for f in fields:
            _drop_sealed(cont, f)
    doc.pop("address_hash", None)
    return doc


def carry_over_masked(new_doc: dict, old_doc: Optional[dict], kind: str) -> dict:
    """Güncelleme gövdesinde MASKELİ (değiştirilmemiş) değer varsa eski değeri (mühürlü ya da düz) taşır.
    new_doc YERİNDE değişir."""
    if not isinstance(new_doc, dict):
        return new_doc
    old_doc = old_doc if isinstance(old_doc, dict) else {}
    tops, conts = KIND_SPECS[kind]
    pairs = [(new_doc, old_doc, tops)]
    for c, fields in conts.items():
        if isinstance(new_doc.get(c), dict):
            pairs.append((new_doc[c], old_doc.get(c) if isinstance(old_doc.get(c), dict) else {}, fields))
    for new_c, old_c, fields in pairs:
        for f, k in fields.items():
            if f in new_c and is_masked_for(k, new_c.get(f)):
                new_c.pop(f, None)
                if old_c.get(f"{f}_enc"):
                    for s in _SEALED_SUFFIXES:
                        if f"{f}{s}" in old_c:
                            new_c[f"{f}{s}"] = old_c[f"{f}{s}"]
                elif _is_present(old_c.get(f)) and not is_masked_for(k, old_c.get(f)):
                    new_c[f] = old_c.get(f)
    return new_doc


def sealed_set_update(set_doc: dict, kind: str, old_doc: Optional[dict] = None) -> dict:
    """{'$set': …, ['$unset': …]} — $set gövdesindeki iletişim verisini mühürler.
    Tam kapsayıcı ('shipping_address': {...}) ve üst-seviye alanlar desteklenir; maskeli değer gelirse
    eski değer korunur (old_doc verildiyse). Nokta-yollu ('shipping_address.phone') alanlar da."""
    tops, conts = KIND_SPECS[kind]
    s = dict(set_doc or {})
    for c in conts:
        if isinstance(s.get(c), dict):
            s[c] = dict(s[c])
    drop_client_sealed(s, kind)
    carry_over_masked(s, old_doc, kind)
    unset: dict = {}
    if kind == "address" and "address" not in s and ("city" in s or "district" in s) and old_doc:
        _old_line = open_field(old_doc, "address")
        if _old_line:
            s["address_hash"] = address_hash(_old_line, s.get("city", old_doc.get("city")),
                                             s.get("district", old_doc.get("district")))
    if kind == "address" and "address" in s:
        line = s.get("address")
        if is_masked_address(line):
            _old_line = open_field(old_doc, "address") if old_doc else ""
            if _old_line and ("city" in s or "district" in s):
                s["address_hash"] = address_hash(_old_line, s.get("city", (old_doc or {}).get("city")),
                                                 s.get("district", (old_doc or {}).get("district")))
        elif _is_present(line):
            city = s.get("city", (old_doc or {}).get("city"))
            dist = s.get("district", (old_doc or {}).get("district"))
            s["address_hash"] = address_hash(line, city, dist)
        else:
            unset["address_hash"] = ""
    # kapsayıcılar
    for c, fields in conts.items():
        if isinstance(s.get(c), dict):
            for f, k in fields.items():
                if f in s[c]:
                    seal_field(s[c], f, k)
    # üst-seviye alanlar
    for f, k in tops.items():
        if f not in s:
            continue
        v = s.get(f)
        if isinstance(v, (dict, list)):
            continue
        if _is_present(v):
            sealed = seal_value(k, v)
            s.pop(f)
            for suf, val in sealed.items():
                s[f"{f}{suf}"] = val
            for suf in _SEALED_SUFFIXES:
                if suf not in sealed and f"{f}{suf}" not in s:
                    unset[f"{f}{suf}"] = ""
            unset[f] = ""
        else:
            s[f] = ""
            for suf in _SEALED_SUFFIXES:
                unset[f"{f}{suf}"] = ""
    # nokta-yollu alanlar
    for key in list(s.keys()):
        if "." not in key:
            continue
        parent, _, leaf = key.rpartition(".")
        k = (conts.get(parent) or {}).get(leaf)
        if not k:
            continue
        v = s[key]
        if is_masked_for(k, v):
            s.pop(key)
            continue
        if _is_present(v):
            sealed = seal_value(k, v)
            s.pop(key)
            for suf, val in sealed.items():
                s[f"{key}{suf}"] = val
            unset[key] = ""
        else:
            for suf in _SEALED_SUFFIXES:
                unset[f"{key}{suf}"] = ""
    for k in list(unset):
        if k in s:
            unset.pop(k)
    out = {"$set": s}
    if unset:
        out["$unset"] = unset
    return out


# ───────────────────────────── düz belgeler (sipariş vb.) — maskeleme ─────────────────────────────
def _mask_container(c: dict, depth: int = 0) -> None:
    for k in list(c.keys()):
        v = c.get(k)
        ks = str(k)
        base = ks
        for suf in _SEALED_SUFFIXES:
            if ks.endswith(suf):
                base = ks[: -len(suf)]
                break
        if base != ks and (is_phone_key(base) or is_address_key(base)):
            c.pop(k, None)           # mühür artığı (yanıtta taşınmaz)
            continue
        if isinstance(v, dict):
            if depth < 3:
                _mask_container(v, depth + 1)
            continue
        if isinstance(v, list):
            if depth < 3:
                for x in v:
                    if isinstance(x, dict):
                        _mask_container(x, depth + 1)
            continue
        if is_phone_key(k):
            c[k] = mask_phone(v) if _is_present(v) else (v if v is not None else "")
        elif is_address_key(k):
            c[k] = mask_address(v) if _is_present(v) else (v if v is not None else "")


def mask_order_contact(order: Optional[dict]) -> Optional[dict]:
    """Sipariş/iade/silinen sipariş belgesinde müşteri telefonu ve açık adresini YERİNDE maskeler
    (il/ilçe, ad-soyad, e-posta korunur). Gönderici/şirket adresi alanlarına dokunulmaz."""
    if not isinstance(order, dict):
        return order
    for cname in ORDER_CONTACT_CONTAINERS:
        sub = order.get(cname)
        if isinstance(sub, dict):
            _mask_container(sub, 1)
    for k in list(order.keys()):
        v = order.get(k)
        if isinstance(v, (dict, list)):
            continue
        if is_phone_key(k):
            order[k] = mask_phone(v) if _is_present(v) else v
        elif is_address_key(k):
            order[k] = mask_address(v) if _is_present(v) else v
    return order


def mask_orders_contact(orders: Iterable) -> Iterable:
    for o in orders or []:
        mask_order_contact(o)
    return orders


def order_contact_view(order: Optional[dict]) -> dict:
    """'Göster' yanıtı için siparişin müşteri iletişim bilgisi (TAM değer) — yalnız ilgili alanlar."""
    o = order or {}
    out = {}
    for cname in ("shipping_address", "billing_address", "invoice_address", "delivery_address"):
        sub = o.get(cname)
        if isinstance(sub, dict):
            out[cname] = {k: v for k, v in sub.items()
                          if not isinstance(v, (dict, list)) and not str(k).endswith(_SEALED_SUFFIXES)}
    for k in ("phone", "customer_phone", "user_phone", "buyer_phone", "email", "customer_email"):
        if _is_present(o.get(k)):
            out[k] = o.get(k)
    return out


def count_order_contact(order: Optional[dict]) -> int:
    """PRIV07 sayacı için: bir siparişin iletişim bilgisini açmak = 1 kayıt (teslimat + fatura birlikte)."""
    return 1


def _masked_contact_value(k: Any, v: Any) -> bool:
    if is_phone_key(k):
        return is_masked(v)
    if is_address_key(k):
        return is_masked_address(v)
    return False


def carry_over_masked_order_contact(new_doc: dict, old_doc: Optional[dict]) -> dict:
    """Sipariş düzenleme gövdesindeki MASKELİ telefon/adres → mevcut değer korunur (YERİNDE).
    Liste verisiyle açılmış bir düzenleme formu maskeyi gerçek adresin üstüne yazamaz."""
    if not isinstance(new_doc, dict):
        return new_doc
    old_doc = old_doc if isinstance(old_doc, dict) else {}

    def _fix(new_c: dict, old_c: dict, depth: int = 0):
        for k in list(new_c.keys()):
            v = new_c.get(k)
            if isinstance(v, dict) and depth < 3:
                _fix(v, old_c.get(k) if isinstance(old_c.get(k), dict) else {}, depth + 1)
                continue
            if _masked_contact_value(k, v):
                if k in old_c:
                    new_c[k] = old_c.get(k)
                else:
                    new_c.pop(k, None)
    for cname in ORDER_CONTACT_CONTAINERS:
        if isinstance(new_doc.get(cname), dict):
            _fix(new_doc[cname], old_doc.get(cname) if isinstance(old_doc.get(cname), dict) else {}, 1)
    for k in list(new_doc.keys()):
        v = new_doc.get(k)
        if "." in str(k):
            parent, _, leaf = str(k).rpartition(".")
            if _masked_contact_value(leaf, v):
                cur = old_doc
                for part in parent.split("."):
                    cur = cur.get(part) if isinstance(cur, dict) else None
                if isinstance(cur, dict) and leaf in cur:
                    new_doc[k] = cur.get(leaf)
                else:
                    new_doc.pop(k, None)
            continue
        if _masked_contact_value(k, v):
            if k in old_doc:
                new_doc[k] = old_doc.get(k)
            else:
                new_doc.pop(k, None)
    return new_doc


# ───────────────────────────── log / LLM / uyarı süzgeçleri ─────────────────────────────
# TR cep (5xx) ve sabit hat (2xx-4xx) numaraları; +90 / 0090 / 0 önekli, boşluk/tire/nokta/parantezli.
# Sınırda rakam OLMAMALI: 11+ haneli sipariş no / barkod telefon sanılmasın.
# Sabit hat (2xx-4xx) yalnız önekliyse (+90 / 0090 / 90 / 0) eşleşir; cep (5xx) öneksiz de eşleşir.
PHONE_TEXT_RE = re.compile(
    r"(?<![\w+])(?:(?:(?:\+|00)?\s?90[\s.\-]?|0[\s.\-]?)\(?[2-5]\d{2}\)?|\(?5\d{2}\)?)"
    r"[\s.\-]?\d{3}[\s.\-]?\d{2}[\s.\-]?\d{2}(?!\w)")
# Adres kalıbı: "<ad> Mah./Mahallesi/Sok./Sk./Cad./Cd./Bulvarı/Blv." + satırın devamı (virgül/; /satır sonuna dek)
ADDRESS_TEXT_RE = re.compile(
    r"[^\s,;:|]+\s+(?:mah(?:alle(?:si)?)?\.?|mh\.|sok(?:ak|a[ğg][ıi])?\.?|sk\.|cad(?:de(?:si)?)?\.?|cd\.|"
    r"bulvar[ıi]?|blv\.)(?=[\s,.;:]|$)[^\n;|]{0,80}",
    re.IGNORECASE)
PHONE_TOKEN = "[telefon]"
ADDRESS_TOKEN = "[adres]"


def scrub_contact_text(s: Any) -> Any:
    """Serbest metindeki TR telefon numaralarını ve adres kalıplarını maskeler. Metin değilse aynen."""
    if not isinstance(s, str) or not s:
        return s
    out = ADDRESS_TEXT_RE.sub(ADDRESS_TOKEN, s)
    out = PHONE_TEXT_RE.sub(PHONE_TOKEN, out)
    return out


def scrub_contact(obj: Any, _depth: int = 0, *, text: bool = True) -> Any:
    """Log / ham pazaryeri yükü / LLM bağlamı için KOPYA: telefon/adres anahtarlarının değerleri
    maskelenir, mühür (_enc/_hash) alanları atılır; text=True iken serbest metindeki telefon/adres
    kalıpları da maskelenir. Kişisel veri olmayan yapıya dokunulmaz."""
    if _depth > 12:
        return obj
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            ks = str(k)
            base = ks
            for suf in _SEALED_SUFFIXES:
                if ks.endswith(suf):
                    base = ks[: -len(suf)]
                    break
            if base != ks and (is_phone_key(base) or is_address_key(base)):
                continue
            if not isinstance(v, (dict, list)) and is_phone_key(k):
                out[k] = mask_phone(v) if _is_present(v) else v
            elif not isinstance(v, (dict, list)) and is_address_key(k):
                out[k] = mask_address(v) if _is_present(v) else v
            else:
                out[k] = scrub_contact(v, _depth + 1, text=text)
        return out
    if isinstance(obj, list):
        return [scrub_contact(x, _depth + 1, text=text) for x in obj]
    if isinstance(obj, tuple):
        return tuple(scrub_contact(x, _depth + 1, text=text) for x in obj)
    if text and isinstance(obj, str):
        return scrub_contact_text(obj)
    return obj


def deepcopy_scrubbed(obj: Any) -> Any:
    return scrub_contact(copy.deepcopy(obj))


def has_plain_contact(obj: Any, _depth: int = 0) -> bool:
    """Log/olay kaydında maskelenmemiş telefon/adres değeri var mı (anahtar bazlı)."""
    if _depth > 12:
        return False
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(v, (dict, list)) and (is_phone_key(k) or is_address_key(k)) \
                    and _is_present(v) and not is_masked(v):
                return True
            if isinstance(v, (dict, list)) and has_plain_contact(v, _depth + 1):
                return True
        return False
    if isinstance(obj, list):
        return any(has_plain_contact(x, _depth + 1) for x in obj)
    return False


# ───────────────────────────── açma (reveal) denetimi ─────────────────────────────
async def note_contact_reveal(actor: Optional[dict], kind: str, entity_id: str = "", n: int = 1, *,
                              request=None, entity_type: str = "") -> None:
    """Tam telefon/adres açılmadan ÖNCE çağrılır. PRIV07 toplu çözme sayacına işler (eşik aşımında
    429 / sayaç deposu hatasında 503 YAYILIR → açık değer verilmez) ve auth_audit_logs'a
    'pii_contact_view' yazar (değerin kendisi yazılmaz)."""
    from security.events import note_pii_reveal
    n = max(1, int(n or 1))
    await note_pii_reveal(actor, f"contact_{kind}"[:40], n, request=request,
                          refs=[f"{entity_type or kind}:{entity_id}"] if entity_id else None)
    try:
        from routes.deps import write_audit_log
        ip = ""
        ua = ""
        try:
            if request is not None:
                from routes.deps import client_ip_from_request
                ip = client_ip_from_request(request) or ""
                ua = request.headers.get("user-agent") or ""
        except Exception:
            pass
        await write_audit_log("pii_contact_view", user_id=(actor or {}).get("id"),
                              email=(actor or {}).get("email"), ip=ip or None, user_agent=ua,
                              success=True, meta={"kind": kind, "entity_type": entity_type or kind,
                                                  "entity_id": str(entity_id or "")[:80], "count": n})
    except Exception:
        pass
