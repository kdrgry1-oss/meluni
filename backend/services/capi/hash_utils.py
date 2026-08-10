"""
PII Hashing utilities for CAPI (Conversions API) integrations.

Bu modül, Meta, Google, TikTok, Pinterest ve Snapchat'in EN GELİŞMİŞ
"Advanced Matching" / "Enhanced Conversion" parametrelerine uygun hash'leri üretir.

Standart: Tüm değerler trim + lowercase + SHA-256, hex output.
İstisnalar: country = ISO-alpha-2 lower (tr, us, ...), zip = boşluksuz lower.
"""
import hashlib
import re
from typing import Optional


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


_HEX64_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _already_hashed(s: Optional[str]) -> bool:
    """Değer zaten 64 karakter hex SHA-256 hash'i mi? (Meta best-practice: PII önceden
    hash'lenmiş gelebilir → İKİNCİ kez hashleme eşleşmeyi bozar.) Bugün storefront ham PII
    gönderiyor (bu koruma tetiklenmez); ileride hashli gönderilirse eşleşme korunur."""
    return bool(_HEX64_RE.match((s or "").strip()))


# ============================================================================
#  Bireysel hash fonksiyonları
# ============================================================================

def hash_email(email: Optional[str]) -> Optional[str]:
    if _already_hashed(email):
        return (email or "").strip().lower()
    e = _norm(email)
    if not e or "@" not in e:
        return None
    return _sha256(e)


def hash_phone(phone: Optional[str], default_country: str = "90") -> Optional[str]:
    """E.164 format. Türkiye için default 90 prefix'i kullanılır."""
    if not phone:
        return None
    if _already_hashed(phone):
        return str(phone).strip().lower()
    digits = re.sub(r"\D+", "", str(phone))
    if not digits:
        return None
    if digits.startswith("0") and not digits.startswith("00"):
        digits = digits[1:]
    if not digits.startswith(default_country) and len(digits) == 10:
        digits = default_country + digits
    return _sha256(digits)


def hash_name(name: Optional[str]) -> Optional[str]:
    n = _norm(name)
    return _sha256(n) if n else None


def hash_name_initial(name: Optional[str]) -> Optional[str]:
    """Meta f5first/f5last için ilk 5 karakter hash (advanced matching subset)."""
    n = _norm(name)
    return _sha256(n[:5]) if n else None


def hash_initial(name: Optional[str]) -> Optional[str]:
    """Tek karakter hash — Meta 'fi' (first initial)."""
    n = _norm(name)
    return _sha256(n[:1]) if n else None


def hash_city(city: Optional[str]) -> Optional[str]:
    n = re.sub(r"\s+", "", _norm(city))
    return _sha256(n) if n else None


def hash_state(state: Optional[str]) -> Optional[str]:
    """ISO 3166-2 region kod veya il adı. Meta 'st' = 2 karakter (US için CA, NY).
    TR için trafik kodu (34, 06, ...) veya il adının ilk 2 karakteri kullanılabilir."""
    n = re.sub(r"\s+", "", _norm(state))
    return _sha256(n) if n else None


def hash_country(country_code: Optional[str]) -> Optional[str]:
    """ISO 3166-1 alpha-2 lowercase (tr, us, ...)."""
    n = _norm(country_code)
    if len(n) != 2:
        return None
    return _sha256(n)


def hash_zip(zipcode: Optional[str]) -> Optional[str]:
    z = re.sub(r"\s+", "", _norm(zipcode))
    return _sha256(z) if z else None


def hash_street(street: Optional[str]) -> Optional[str]:
    """Google Enhanced Conversions için cadde adresi (lowercase, trim)."""
    n = _norm(street)
    return _sha256(n) if n else None


def hash_gender(gender: Optional[str]) -> Optional[str]:
    """Meta 'ge' = 'm' veya 'f' (lowercase). TikTok/Pinterest aynı."""
    n = _norm(gender)
    if not n:
        return None
    # Normalleştir: erkek/male/m → m ; kadın/female/woman → f
    if n[0] in ("m", "e"):
        return _sha256("m")
    if n[0] in ("f", "k", "w"):
        return _sha256("f")
    return _sha256(n[:1])


def hash_dob_full(dob: Optional[str]) -> Optional[str]:
    """YYYYMMDD format. Örn: 1990-05-15 → '19900515'."""
    if not dob:
        return None
    digits = re.sub(r"\D+", "", str(dob))
    if len(digits) < 8:
        return None
    return _sha256(digits[:8])


def hash_dob_year(dob: Optional[str]) -> Optional[str]:
    """Sadece yıl (YYYY)."""
    if not dob:
        return None
    digits = re.sub(r"\D+", "", str(dob))
    return _sha256(digits[:4]) if len(digits) >= 4 else None


def hash_dob_month(dob: Optional[str]) -> Optional[str]:
    """Sadece ay (MM)."""
    if not dob:
        return None
    digits = re.sub(r"\D+", "", str(dob))
    return _sha256(digits[4:6]) if len(digits) >= 6 else None


def hash_dob_day(dob: Optional[str]) -> Optional[str]:
    """Sadece gün (DD)."""
    if not dob:
        return None
    digits = re.sub(r"\D+", "", str(dob))
    return _sha256(digits[6:8]) if len(digits) >= 8 else None


def hash_madid(madid: Optional[str]) -> Optional[str]:
    """Mobile advertiser id (IDFA/AAID). Lower + hash."""
    n = _norm(madid)
    return _sha256(n) if n else None


# ============================================================================
#  Toplu user_data builder
# ============================================================================

def build_user_data(
    *,
    # Temel kimlik
    email: Optional[str] = None,
    phone: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    # Adres
    city: Optional[str] = None,
    state: Optional[str] = None,                  # il/eyalet
    country: Optional[str] = "TR",
    zipcode: Optional[str] = None,
    street: Optional[str] = None,                 # tam cadde
    # Demografi (advanced matching)
    date_of_birth: Optional[str] = None,          # YYYY-MM-DD veya YYYYMMDD
    gender: Optional[str] = None,                 # m/f/erkek/kadın
    # ID'ler
    external_id: Optional[str] = None,            # CRM/customer id (hashed)
    subscription_id: Optional[str] = None,        # Meta subscription
    fb_login_id: Optional[str] = None,            # Meta FB user id
    lead_id: Optional[str] = None,                # Meta lead form id
    madid: Optional[str] = None,                  # Mobile ad id (IDFA/AAID)
    idfa: Optional[str] = None,                   # iOS IDFA (hashed)
    idfv: Optional[str] = None,                   # iOS IDFV (hashed)
    # Server-side context (raw, NOT hashed)
    client_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    # Click ID cookies (raw, NOT hashed)
    fbp: Optional[str] = None,                    # Meta browser cookie
    fbc: Optional[str] = None,                    # Meta click ID cookie
    gclid: Optional[str] = None,                  # Google Ads click ID
    wbraid: Optional[str] = None,                 # Google iOS web-to-app
    gbraid: Optional[str] = None,                 # Google iOS app
    ttclid: Optional[str] = None,                 # TikTok click ID
    ttp: Optional[str] = None,                    # TikTok browser cookie (_ttp)
    epik: Optional[str] = None,                   # Pinterest click ID
    sc_click_id: Optional[str] = None,            # Snapchat click ID
    sc_cookie1: Optional[str] = None,             # Snapchat cookie
    # Locale
    locale: Optional[str] = None,                 # tr-TR
) -> dict:
    """Normalize edilmiş user_data dict döner. Her provider adapter'ı buradan
    ihtiyacı olan field'ları seçer."""
    return {
        # Hashed PII
        "em": hash_email(email),
        "ph": hash_phone(phone),
        "fn": hash_name(first_name),
        "ln": hash_name(last_name),
        "f5first": hash_name_initial(first_name),
        "f5last": hash_name_initial(last_name),
        "fi": hash_initial(first_name),
        "ct": hash_city(city),
        "st": hash_state(state),
        "country": hash_country(country or "TR"),
        "zp": hash_zip(zipcode),
        "street": hash_street(street),
        "db": hash_dob_full(date_of_birth),
        "doby": hash_dob_year(date_of_birth),
        "dobm": hash_dob_month(date_of_birth),
        "dobd": hash_dob_day(date_of_birth),
        "ge": hash_gender(gender),
        "external_id": (external_id.strip().lower() if _already_hashed(external_id)
                        else (_sha256(_norm(external_id)) if external_id else None)),
        "subscription_id": subscription_id,       # raw allowed for Meta
        "fb_login_id": fb_login_id,
        "lead_id": lead_id,
        "madid": hash_madid(madid),
        "idfa": hash_madid(idfa),
        "idfv": hash_madid(idfv),
        # Y24: Ham (hash'siz) adres bileşenleri — Google GA4 Enhanced Conversions şehir/bölge/
        # posta/ülkeyi HASH'SİZ ister (yalnızca ad/soyad/sokak hash'lidir). Meta vb. hash'li
        # ct/st/country/zp kullanmaya devam eder; bu alanlar yalnızca Google tarafında kullanılır.
        "ct_raw": (city or "").strip(),
        "st_raw": (state or "").strip(),
        "country_raw": (country or "TR").strip(),
        "zp_raw": (zipcode or "").strip(),
        # Raw (server-side context)
        "client_ip_address": client_ip,
        "client_user_agent": user_agent,
        "locale": locale,
        # Click IDs (raw)
        "fbp": fbp, "fbc": fbc,
        "gclid": gclid, "wbraid": wbraid, "gbraid": gbraid,
        "ttclid": ttclid, "ttp": ttp,
        "epik": epik,
        "sc_click_id": sc_click_id, "sc_cookie1": sc_cookie1,
    }
