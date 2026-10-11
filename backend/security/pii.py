"""
TCKN / VKN (T.C. kimlik no / vergi kimlik no) — at-rest şifreleme yardımcıları (KVKK).

TASARIM
  Düz değer (ör. ``billing_info.tax_number = "12345678901"``) veritabanına YAZILMAZ. Yerine:
    <alan>_enc    security.crypto.encrypt ile şifreli değer ("v1:<fernet>") — gerçek değer yalnız
                  bundan çözülür (Doğan e-Arşiv/e-Fatura UBL, iyzico buyer.identityNumber, admin
                  sipariş detayı/düzenleme, KVKK md.11 veri dökümü).
    <alan>_hash   "<kid>:<hex>" — ALAN-AYRIMLI HMAC-SHA256 (tam eşleşme araması için; düz değer
                  saklamadan "bu TCKN/VKN ile sipariş ara" çalışır). Anahtar sırası (kvkk mezar taşı
                  deseni): PII_HASH_SECRET (≥32) → SECRETS_MASTER_KEY'den türetilmiş → JWT_SECRET'tan
                  türetilmiş. Yazımda BİRİNCİL anahtar; aramada mevcut TÜM anahtarlar ($in) → anahtar
                  eklenince/rotasyonda eski kayıtlar bulunmaya devam eder (gece işi yeniden hash'ler).
    <alan>_last2  son 2 hane — listelerde maskeli gösterim ("*********12").
  Boş değer ("") düz kalır (kişisel veri değildir; şema/şekil korunur).

MASKE
  Maskeli değer ("*********12") yalnız YANITTA üretilir; istemci bunu geri gönderirse (ör. düzenleme
  formu değiştirilmeden kaydedilirse) sunucu "değişmedi" kabul eder ve mevcut şifreli değeri korur
  (``carry_over_masked``). Maske karakteri içeren değer ASLA gerçek değer diye saklanmaz.

KAPSAM (bilinen yollar)
  orders / orders_deleted : billing_info.*, billing_address.*, shipping_address.*, invoice_address.*,
                            customer.*, buyer.* + üst-seviye trendyol_identity_number vb.
  addresses               : üst-seviye tax_no / tax_number / ...
  vendors                 : üst-seviye tax_number / identity_number
  Log/olay kayıtları (integration_logs, order_events) için şifreleme değil MASKELEME
  (``scrub_tax_ids``) — teşhis kaydının gerçek kimlik numarasına ihtiyacı yoktur.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import logging
import os
import re
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

# Bir kapsayıcı (dict) içinde TCKN/VKN taşıyabilen anahtar adları.
TAX_ID_KEYS = (
    "tax_number", "tax_no", "vkn", "tckn", "tc_no", "tax_id", "identity_number",
    "national_id", "tc_kimlik", "tc_kimlik_no", "kimlik_no",
)
SUFFIXES = ("_enc", "_hash", "_last2")
MASK_CHARS = ("*", "•")
_MASK_PREFIX = "*" * 9

ORDER_CONTAINERS = ("billing_info", "billing_address", "shipping_address", "invoice_address",
                    "customer", "buyer")
ORDER_TOP_FIELDS = ("trendyol_identity_number", "identity_number", "tax_number", "tax_no",
                    "tckn", "vkn", "customer_tax_number", "customer_vkn")

# kind → (kapsayıcılar, üst-seviye alanlar)
_KIND_PATHS = {
    "order": (ORDER_CONTAINERS, ORDER_TOP_FIELDS),
    "address": ((), TAX_ID_KEYS),
    "vendor": ((), ("tax_number", "identity_number")),
}

# Log/ham pazaryeri yüklerinde maskelenecek anahtarlar (küçük harf, '_' ve '-' yok sayılır).
_LOG_KEYS = {k.replace("_", "") for k in TAX_ID_KEYS} | {
    "identitynumber", "taxnumber", "taxno", "tcno", "tckimlikno", "nationalid", "citizenshipnumber",
    "citizenid", "buyertaxnumber", "customertaxnumber", "customervkn", "receivervkn",
    "trendyolidentitynumber", "customertckn", "vergino", "verginumarasi", "vergikimlikno",
    "tckimlik", "alicivergino", "alicitckn", "alicivergino", "pchvergino", "pchverginumarasi",
}


# ───────────────────────────── anahtarlar / hash ─────────────────────────────
def _jwt_secret() -> str:
    try:
        from routes.deps import JWT_SECRET as _j  # aynı kaynak (env ya da kalıcı dosya)
        return str(_j or "")
    except Exception:
        return (os.environ.get("JWT_SECRET") or "").strip()


def derived_hash_keys(label: bytes) -> list:
    """[(kid, key_bytes)] — `label` ile ALAN-AYRIMLI türetilmiş HMAC anahtarları, birincil ilk sırada.
    Kök sırlar DOĞRUDAN kullanılmaz. TCKN/VKN (bu modül) ve telefon/adres (security.contact_pii) aynı
    anahtar kaynaklarını farklı etiketlerle kullanır → bir alanın hash'i başka alanda eşleşmez."""
    keys = []
    env = (os.environ.get("PII_HASH_SECRET") or "").strip()
    if len(env) >= 32:
        keys.append(("env", hmac.new(env.encode(), label, hashlib.sha256).digest()))
    smk = (os.environ.get("SECRETS_MASTER_KEY") or "").strip()
    if smk:
        keys.append(("smk", hmac.new(smk.encode(), label, hashlib.sha256).digest()))
    jwt = _jwt_secret()
    if jwt:
        keys.append(("jwt", hmac.new(jwt.encode(), label, hashlib.sha256).digest()))
    return keys


def _hash_keys() -> list:
    """[(kid, key_bytes)] — birincil ilk sırada. Kök sırlar DOĞRUDAN kullanılmaz (alan ayrımı)."""
    return derived_hash_keys(b"meluni:pii-tax-id:v1")


def primary_hash_kid() -> str:
    ks = _hash_keys()
    return ks[0][0] if ks else ""


def normalize_tax_id(value: Any) -> str:
    """Boşluk/tire/nokta atılır, büyük harf (yabancı vergi no harf içerebilir). Rakamsa yalnız rakam."""
    if value is None:
        return ""
    s = re.sub(r"[\s\-\.]", "", str(value)).upper()
    return s


def _hmac_hex(key: bytes, norm: str) -> str:
    return hmac.new(key, f"meluni:pii-tax-id:v1|{norm}".encode(), hashlib.sha256).hexdigest()


def tax_id_hash(value: Any) -> str:
    """Birincil anahtarla "kid:hex" ('' boş değer / anahtar yoksa)."""
    norm = normalize_tax_id(value)
    ks = _hash_keys()
    if not norm or not ks:
        return ""
    kid, key = ks[0]
    return f"{kid}:{_hmac_hex(key, norm)}"


def tax_id_hashes(value: Any) -> list:
    """Mevcut TÜM anahtarlarla "kid:hex" listesi (arama için)."""
    norm = normalize_tax_id(value)
    if not norm:
        return []
    return [f"{kid}:{_hmac_hex(key, norm)}" for kid, key in _hash_keys()]


# ───────────────────────────── tekil değer ─────────────────────────────
def is_masked(value: Any) -> bool:
    return isinstance(value, str) and any(c in value for c in MASK_CHARS)


def _is_present(value: Any) -> bool:
    return value is not None and not isinstance(value, (dict, list, bool)) and str(value).strip() != ""


def mask_tax_id(value: Any = None, last2: Optional[str] = None) -> str:
    """'*********12' — değer ya da yalnız son 2 hane ile."""
    if last2 is None:
        s = normalize_tax_id(value)
        if not s:
            return ""
        last2 = s[-2:]
    last2 = str(last2 or "")
    return (_MASK_PREFIX + last2) if last2 else ""


def seal_tax_id(value: Any) -> dict:
    """{'enc','hash','last2'} — boş/maskeli değerde {}. Şifre çözümü doğrulanır (çözülemeyen
    şifreli değer ASLA üretilmez → düz değer sessizce kaybolmaz)."""
    if not _is_present(value) or is_masked(value):
        return {}
    from security.crypto import encrypt, decrypt
    plain = str(value).strip()
    enc = encrypt(plain)
    if not enc or decrypt(enc) != plain:
        raise RuntimeError("TCKN/VKN şifreleme doğrulaması başarısız")
    norm = normalize_tax_id(plain)
    return {"enc": enc, "hash": tax_id_hash(plain), "last2": norm[-2:]}


def _apply_seal(container: dict, field: str, sealed: dict) -> None:
    container[f"{field}_enc"] = sealed["enc"]
    container[f"{field}_hash"] = sealed["hash"]
    container[f"{field}_last2"] = sealed["last2"]
    container.pop(field, None)


def _drop_sealed(container: dict, field: str) -> None:
    for suf in SUFFIXES:
        container.pop(f"{field}{suf}", None)


def seal_field(container: dict, field: str) -> bool:
    """Kapsayıcıdaki düz değeri yerinde mühürler. Dönüş: değişiklik yapıldı mı.
      - dolu düz değer      → _enc/_hash/_last2 yazılır, düz anahtar SİLİNİR
      - maskeli değer       → düz anahtar silinir (mevcut mühür, varsa, korunur)
      - "" / None           → dokunulmaz (kişisel veri değil); ama eski mühür varsa "" yeni değerdir
                              → mühür kaldırılır (kullanıcı alanı temizledi)."""
    if not isinstance(container, dict) or field not in container:
        return False
    v = container.get(field)
    if isinstance(v, (dict, list)):
        return False
    if is_masked(v):
        container.pop(field, None)
        return True
    if not _is_present(v):
        if any(f"{field}{s}" in container for s in SUFFIXES):
            _drop_sealed(container, field)
            return True
        return False
    _apply_seal(container, field, seal_tax_id(v))
    return True


def reveal_tax_id(container: Optional[dict], field: str) -> str:
    """Gerçek değer: önce (eski) düz değer, yoksa <alan>_enc çözülür. Bulunamazsa ''."""
    if not isinstance(container, dict):
        return ""
    v = container.get(field)
    if _is_present(v) and not is_masked(v):
        return str(v)
    enc = container.get(f"{field}_enc")
    if enc:
        try:
            from security.crypto import decrypt
            p = decrypt(enc)
            return p or ""
        except Exception:
            logger.error("TCKN/VKN şifresi çözülemedi (anahtar?)")
            return ""
    return ""


def has_tax_id(container: Optional[dict], field: str) -> bool:
    if not isinstance(container, dict):
        return False
    v = container.get(field)
    return (_is_present(v) and not is_masked(v)) or bool(container.get(f"{field}_enc"))


# ───────────────────────────── belge seviyesi ─────────────────────────────
def _paths_for(kind: str):
    return _KIND_PATHS.get(kind) or _KIND_PATHS["order"]


def kind_paths(kind: str = "order"):
    """(kapsayıcılar, üst-seviye alanlar) — backfill/projeksiyon için."""
    return _paths_for(kind)


def iter_plain_paths(kind: str = "order"):
    """Düz değer taşıyabilecek tüm nokta-yollar (sorgu/gece ağı için)."""
    containers, tops = _paths_for(kind)
    for c in containers:
        for k in TAX_ID_KEYS:
            yield f"{c}.{k}"
    for t in tops:
        yield t


def _containers_of(doc: dict, kind: str):
    containers, tops = _paths_for(kind)
    for c in containers:
        sub = doc.get(c)
        if isinstance(sub, dict):
            yield sub, TAX_ID_KEYS
    yield doc, tops


def seal_doc_tax_ids(doc: Optional[dict], kind: str = "order") -> Optional[dict]:
    """Belgedeki bilinen tüm TCKN/VKN alanlarını YERİNDE mühürler; belgeyi döndürür."""
    if not isinstance(doc, dict):
        return doc
    for cont, fields in _containers_of(doc, kind):
        for f in fields:
            if f in cont:
                seal_field(cont, f)
    return doc


def doc_has_plain_tax_ids(doc: Optional[dict], kind: str = "order") -> list:
    """Düz (mühürlenmemiş) değer taşıyan yollar."""
    out = []
    if not isinstance(doc, dict):
        return out
    containers, tops = _paths_for(kind)
    for c in containers:
        sub = doc.get(c)
        if isinstance(sub, dict):
            for k in TAX_ID_KEYS:
                if _is_present(sub.get(k)):
                    out.append(f"{c}.{k}")
    for t in tops:
        if _is_present(doc.get(t)):
            out.append(t)
    return out


def legacy_plain_unset(sealed_doc: Optional[dict], kind: str = "order") -> dict:
    """Mühürlü bir belge mevcut kayda $set ile yazılırken, üst-seviye alanların ESKİ düz değerleri
    kalmasın diye {alan: ""} $unset gövdesi (kapsayıcılar zaten bütün olarak değiştirilir)."""
    out = {}
    if not isinstance(sealed_doc, dict):
        return out
    _, tops = _paths_for(kind)
    for t in tops:
        if f"{t}_enc" in sealed_doc and t not in sealed_doc:
            out[t] = ""
    return out


def drop_client_sealed_fields(doc: Optional[dict], kind: str = "order") -> Optional[dict]:
    """İSTEMCİ girdisinden gelen <alan>_enc/_hash/_last2 anahtarlarını atar (YERİNDE). Mühürlü alanları
    yalnız sunucu üretir — istemci sahte şifreli değer/hash enjekte edemez (arama zehirleme)."""
    if not isinstance(doc, dict):
        return doc
    for cont, fields in _containers_of(doc, kind):
        for f in fields:
            _drop_sealed(cont, f)
    return doc


def carry_over_masked(new_doc: dict, old_doc: Optional[dict], kind: str = "order") -> dict:
    """Güncelleme gövdesinde MASKELİ (değiştirilmemiş) değerler varsa eski mühürlü değeri taşır.
    new_doc YERİNDE değişir. Eski belge düz (eski kayıt) ise o değer mühürlenerek taşınır."""
    if not isinstance(new_doc, dict):
        return new_doc
    old_doc = old_doc if isinstance(old_doc, dict) else {}
    containers, tops = _paths_for(kind)
    pairs = []
    for c in containers:
        if isinstance(new_doc.get(c), dict):
            pairs.append((new_doc[c], old_doc.get(c) if isinstance(old_doc.get(c), dict) else {}, TAX_ID_KEYS))
    pairs.append((new_doc, old_doc, tops))
    for new_c, old_c, fields in pairs:
        for f in fields:
            if f in new_c and is_masked(new_c.get(f)):
                new_c.pop(f, None)
                if old_c.get(f"{f}_enc"):
                    for s in SUFFIXES:
                        if f"{f}{s}" in old_c:
                            new_c[f"{f}{s}"] = old_c[f"{f}{s}"]
                elif _is_present(old_c.get(f)) and not is_masked(old_c.get(f)):
                    _apply_seal(new_c, f, seal_tax_id(old_c.get(f)))
    return new_doc


def reveal_doc_tax_ids(doc: Optional[dict], kind: str = "order", *, strip_sealed: bool = True) -> Optional[dict]:
    """Gerçek değerleri düz anahtarlarına geri koyan KOPYA döndürür (girdi değişmez). Yalnız
    gerçek değere yasal/işlevsel ihtiyacı olan okuyucular içindir (fatura UBL'i, iyzico, admin
    detay, KVKK veri dökümü). Dönen kopya ASLA veritabanına geri yazılmamalıdır."""
    if not isinstance(doc, dict):
        return doc
    out = dict(doc)
    containers, tops = _paths_for(kind)
    targets = []
    for c in containers:
        if isinstance(out.get(c), dict):
            out[c] = dict(out[c])
            targets.append((out[c], TAX_ID_KEYS))
    targets.append((out, tops))
    for cont, fields in targets:
        for f in fields:
            if f"{f}_enc" in cont or (f in cont and is_masked(cont.get(f))):
                val = reveal_tax_id(cont, f)
                if val or f in cont or f"{f}_enc" in cont:
                    cont[f] = val
            if strip_sealed:
                _drop_sealed(cont, f)
    return out


def mask_doc_tax_ids(doc: Optional[dict], kind: str = "order") -> Optional[dict]:
    """Yanıt için YERİNDE maskeler: düz anahtar '*********12' olur; _enc/_hash/_last2 çıkarılır."""
    if not isinstance(doc, dict):
        return doc
    for cont, fields in _containers_of(doc, kind):
        for f in fields:
            last2 = cont.get(f"{f}_last2")
            plain = cont.get(f)
            if _is_present(plain) and not is_masked(plain):
                cont[f] = mask_tax_id(plain)
            elif last2 or cont.get(f"{f}_enc"):
                if not last2:
                    last2 = normalize_tax_id(reveal_tax_id(cont, f))[-2:]
                cont[f] = mask_tax_id(last2=last2)
            _drop_sealed(cont, f)
    return doc


def mask_docs_tax_ids(docs: Iterable, kind: str = "order"):
    for d in docs or []:
        mask_doc_tax_ids(d, kind)
    return docs


def tax_id_last2_view(container: Optional[dict], field: str) -> str:
    """Ekran için maskeli değer ('' yoksa)."""
    if not isinstance(container, dict):
        return ""
    if container.get(f"{field}_last2"):
        return mask_tax_id(last2=container.get(f"{field}_last2"))
    v = container.get(field)
    if _is_present(v):
        return v if is_masked(v) else mask_tax_id(v)
    return ""


# ───────────────────────────── güncelleme ($set) yardımcıları ─────────────────────────────
def sealed_set_update(set_doc: dict, kind: str = "order", old_doc: Optional[dict] = None) -> dict:
    """{"$set": ..., ["$unset": ...]} — $set gövdesindeki TCKN/VKN'yi mühürler.
    Nokta-yollu alanlar ('billing_info.tax_number') ve tüm kapsayıcı ('billing_info': {...}) desteklenir.
    Maskeli değer gelirse eski belgedeki mühür korunur (old_doc verildiyse)."""
    containers, tops = _paths_for(kind)
    s = dict(set_doc or {})
    unset = {}
    # Tüm kapsayıcılar / üst-seviye alanlar → belge-seviyesi mantık
    carry_over_masked(s, old_doc, kind)
    for c in containers:
        if isinstance(s.get(c), dict):
            s[c] = dict(s[c])
            for f in TAX_ID_KEYS:
                if f in s[c]:
                    seal_field(s[c], f)
    for t in tops:
        if t in s:
            v = s[t]
            if is_masked(v):
                s.pop(t)
                continue
            if _is_present(v):
                sealed = seal_tax_id(v)
                s.pop(t)
                s[f"{t}_enc"], s[f"{t}_hash"], s[f"{t}_last2"] = sealed["enc"], sealed["hash"], sealed["last2"]
                unset[t] = ""
            else:
                for suf in SUFFIXES:
                    unset[f"{t}{suf}"] = ""
    # Nokta-yollu alanlar
    for key in list(s.keys()):
        if "." not in key:
            continue
        parent, _, leaf = key.rpartition(".")
        if parent in containers and leaf in TAX_ID_KEYS:
            v = s[key]
            if is_masked(v):
                s.pop(key)
                continue
            if _is_present(v):
                sealed = seal_tax_id(v)
                s.pop(key)
                s[f"{key}_enc"], s[f"{key}_hash"], s[f"{key}_last2"] = sealed["enc"], sealed["hash"], sealed["last2"]
                unset[key] = ""
            else:
                for suf in SUFFIXES:
                    unset[f"{key}{suf}"] = ""
    out = {"$set": s}
    if unset:
        out["$unset"] = unset
    return out


# ───────────────────────────── arama ─────────────────────────────
def tax_id_search_clauses(search: str, kind: str = "order") -> list:
    """Arama metni 10/11 haneli (ya da ≥8 hane) kimlik no gibiyse TAM eşleşme cümleleri:
    <yol>_hash ∈ {tüm anahtarlarla hash} + (backfill tamamlanmamış eski kayıtlar için) düz tam eşleşme.
    Kısmi (regex) eşleşme YOKTUR — TCKN/VKN parça parça taranamaz."""
    s = normalize_tax_id(search)
    if not s or not s.isdigit() or not (8 <= len(s) <= 11):
        return []
    hashes = tax_id_hashes(s)
    out = []
    for p in iter_plain_paths(kind):
        if hashes:
            out.append({f"{p}_hash": {"$in": hashes}})
        out.append({p: s})
    return out


# ───────────────────────────── log/ham yük maskeleme ─────────────────────────────
def _log_key(k: Any) -> bool:
    kl = re.sub(r"[_\-\s]", "", str(k)).lower()
    return kl in _LOG_KEYS


def scrub_tax_ids(obj: Any, _depth: int = 0):
    """Log/ham pazaryeri yükü için KOPYA: TCKN/VKN anahtarlarının değerleri maskelenir,
    *_enc / *_hash alanları atılır. Kimlik no olmayan veriye dokunulmaz."""
    if _depth > 12:
        return obj
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            ks = str(k)
            if ks.endswith("_enc") or ks.endswith("_hash"):
                base = ks.rsplit("_", 1)[0]
                if _log_key(base):
                    continue
            if _log_key(ks) and not isinstance(v, (dict, list)):
                out[k] = mask_tax_id(v) if (_is_present(v) and not is_masked(v)) else v
            else:
                out[k] = scrub_tax_ids(v, _depth + 1)
        return out
    if isinstance(obj, list):
        return [scrub_tax_ids(x, _depth + 1) for x in obj]
    return obj


def log_has_plain_tax_ids(obj: Any, _depth: int = 0) -> bool:
    if _depth > 12:
        return False
    if isinstance(obj, dict):
        for k, v in obj.items():
            if _log_key(k) and not isinstance(v, (dict, list)) and _is_present(v) and not is_masked(v):
                return True
            if isinstance(v, (dict, list)) and log_has_plain_tax_ids(v, _depth + 1):
                return True
        return False
    if isinstance(obj, list):
        return any(log_has_plain_tax_ids(x, _depth + 1) for x in obj)
    return False


def deepcopy_scrubbed(obj: Any):
    return scrub_tax_ids(copy.deepcopy(obj))
