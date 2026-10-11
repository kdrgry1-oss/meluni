"""
Shared dependencies and utilities for all routes
"""
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timezone, timedelta
import jwt
import bcrypt
import os
import logging
import re
import uuid
import random

# Persistent database
MONGO_URL = os.environ.get('MONGO_URL')
if not MONGO_URL:
    # Try unix socket first (works with OS sandbox), fallback to TCP
    import pathlib
    sock = pathlib.Path('/tmp/mongodb-27017.sock')
    if sock.exists():
        MONGO_URL = 'mongodb://%2Ftmp%2Fmongodb-27017.sock'
    else:
        MONGO_URL = 'mongodb://127.0.0.1:27017'
db_name = os.environ.get('DB_NAME', 'test_database')

client = AsyncIOMotorClient(MONGO_URL)
db = client[db_name]

# Security
security = HTTPBearer(auto_error=False)


def _load_jwt_secret() -> str:
    """Y12: JWT_SECRET önce ortamdan okunur. Ortamda yoksa, kaynak koduna GÖMÜLÜ sabit bir
    anahtar KULLANILMAZ (yayınlanmış anahtarla token sahtelenebiliyordu). Bunun yerine kalıcı
    bir dosyada (backend/data/.jwt_secret) rastgele güçlü bir anahtar üretilip saklanır; böylece
    hem restart'lar hem de birden fazla worker arasında STABİL kalır. Dosya yazılamazsa (salt-okunur
    fs) süreç-ömürlü rastgele anahtara düşülür ve KRİTİK uyarı loglanır."""
    env_secret = (os.environ.get("JWT_SECRET") or "").strip()
    if len(env_secret) >= 32:
        return env_secret
    _log = logging.getLogger(__name__)
    if env_secret:
        _log.warning("JWT_SECRET <32 bayt; env değeri yok sayıldı, kalıcı dosya kullanılıyor.")
    try:
        import secrets as _secrets
        _dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
        os.makedirs(_dir, exist_ok=True)
        _path = os.path.join(_dir, ".jwt_secret")
        if os.path.exists(_path):
            with open(_path, "r") as fh:
                val = fh.read().strip()
            if len(val) >= 32:
                return val
        val = _secrets.token_urlsafe(48)
        with open(_path, "w") as fh:
            fh.write(val)
        try:
            os.chmod(_path, 0o600)
        except Exception:
            pass
        _log.critical("JWT_SECRET ortamda ayarlı değil — kalıcı dosyada yeni rastgele anahtar üretildi. "
                      "Üretimde JWT_SECRET ortam değişkenini ayarlayın.")
        return val
    except Exception as _e:
        import secrets as _secrets
        _log.critical(f"JWT_SECRET dosyaya yazılamadı ({_e}); süreç-ömürlü rastgele anahtar kullanılıyor "
                      "(restart'ta tüm oturumlar düşer). JWT_SECRET ortam değişkenini ayarlayın.")
        return _secrets.token_urlsafe(48)


JWT_SECRET = _load_jwt_secret()
JWT_ALGORITHM = "HS256"  # strict — prevents 'alg=none' attacks
JWT_ISSUER = "meluni-api"

# Logger
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SECURITY HELPERS (NoSQL injection guard, audit log, brute-force lockout)
# ---------------------------------------------------------------------------

def safe_str(value, max_len: int = 256) -> str:
    """Coerce arbitrary input to a safe string for MongoDB equality matching.
    Strips type-confusion attacks (dict/list with $operators, deeply nested
    payloads). Always returns a primitive str — never an operator dict.
    """
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple, set)):
        # NoSQL injection attempt — refuse
        return ""
    s = str(value)
    if len(s) > max_len:
        s = s[:max_len]
    return s


def is_safe_email(email: str) -> bool:
    """Basic email validation — rejects $ { } operators that could leak into
    Mongo equality match if blindly used. Pydantic + safe_str cover the rest.
    """
    import re
    if not email or not isinstance(email, str):
        return False
    if any(ch in email for ch in ("$", "{", "}", "\x00")):
        return False
    return bool(re.match(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$", email))


async def write_audit_log(event: str, *, user_id: str = None, email: str = None,
                          ip: str = None, user_agent: str = None,
                          success: bool = True, meta: dict = None) -> None:
    """Append an entry to `auth_audit_logs`. Best-effort, never raises."""
    try:
        await db.auth_audit_logs.insert_one({
            "id": str(uuid.uuid4()),
            "event": event,
            "user_id": user_id,
            "email": (email or "").lower() if email else None,
            "ip": ip,
            "user_agent": (user_agent or "")[:500],
            "success": bool(success),
            "meta": meta or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        logger.warning(f"audit log failed: {e}")


# Lockout policy (denetim 2026-10, #8 — hesap kilitleme istismarı):
# Eskiden kilit YALNIZ e-postaya bağlıydı → e-postayı bilen herkes 5 yanlış şifreyle HERHANGİ bir
# hesabı (mağaza sahibi dâhil) 15 dk kilitleyebiliyordu. Artık iki katman:
#   1) IP+hesap: aynı IP'den aynı hesaba 15 dk'da 5 hatalı deneme → O IP o hesap için 15 dk kilit
#      (saldırgan kendi IP'sini kilitler; gerçek sahibi başka IP'den girebilir).
#   2) Hesap-geneli (dağıtık saldırı): 15 dk'da 30 hatalı deneme →
#        • personel (is_admin/is_super_admin): sert kilit YOK, artan bekleme (2^n sn, en çok 60 sn)
#        • müşteri: 15 dk kilit (users.locked_until — Güvenlik Paneli'nde görünür/açılabilir)
#      Hesaba son 30 günde BAŞARIYLA giriş yapılmış (bilinen) IP bu katmandan muaf tutulur →
#      sahibi kendi cihazından kilitlenmez; IP+hesap katmanı ve IP ban yine uygulanır.
# IP-level ban (aşağıda, 1 saatte 50+ hata → 24 saat) aynen korunur.
LOCKOUT_WINDOW_MIN = 15
LOCKOUT_THRESHOLD = 5            # IP+hesap başına
LOCKOUT_DURATION_MIN = 15
ACCOUNT_FAIL_THRESHOLD = 30      # hesap-geneli (tüm IP'ler)
ACCOUNT_DELAY_MAX_SEC = 60       # personel artan bekleme üst sınırı
KNOWN_IP_DAYS = 30


def _lock_key(email: str, ip: str) -> str:
    return f"{(email or '').lower()}|{ip or ''}"


def _iso_dt(v):
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


async def _is_known_ip(user_id: str, ip: str) -> bool:
    """Bu IP'den bu hesaba son KNOWN_IP_DAYS günde BAŞARILI giriş yapılmış mı?"""
    if not user_id or not ip:
        return False
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=KNOWN_IP_DAYS)).isoformat()
        doc = await db.auth_audit_logs.find_one(
            {"user_id": user_id, "ip": ip, "success": True,
             "event": {"$in": ["login", "mfa_verify"]}, "created_at": {"$gte": since}},
            {"_id": 1})
        return bool(doc)
    except Exception:
        return False


async def is_account_locked(email: str, ip: str = None) -> tuple[bool, int]:
    """Return (locked, retry_after_seconds).
    • IP+hesap kilidi (login_ip_failures) — Güvenlik Paneli'nden kilit açılınca (users.first_failed_at
      silinir) o ana kadarki IP kilitleri de geçersiz sayılır.
    • Hesap-geneli eşik (users.*) — bilinen IP muaf; personelde artan bekleme, müşteride kilit."""
    if not email:
        return False, 0
    email = email.lower()
    now = datetime.now(timezone.utc)
    user = await db.users.find_one(
        {"email": email},
        {"_id": 0, "id": 1, "locked_until": 1, "failed_attempts": 1, "first_failed_at": 1,
         "fail_window_start": 1, "last_failed_at": 1, "is_admin": 1, "is_super_admin": 1})
    # 1) IP+hesap kilidi — KAYITLI OLMAYAN e-postalar için de uygulanır: aksi halde 6. denemede
    #    kayıtlı adrese 429, olmayana 401 dönüp e-postanın varlığını ele veriyordu (enumeration).
    try:
        rec = await db.login_ip_failures.find_one({"key": _lock_key(email, ip)},
                                                  {"_id": 0, "locked_until": 1, "locked_at": 1})
        if rec and rec.get("locked_until"):
            until = _iso_dt(rec.get("locked_until"))
            locked_at = _iso_dt(rec.get("locked_at"))
            if user:
                streak = _iso_dt(user.get("first_failed_at"))
                # Panelden kilit açıldıysa (first_failed_at yok ya da kilitten SONRA başladıysa) yok say.
                if until and until > now and streak and locked_at and streak <= locked_at:
                    return True, int((until - now).total_seconds())
            elif until and until > now:
                return True, int((until - now).total_seconds())
    except Exception:
        pass
    if not user:
        return False, 0
    # 2) Hesap-geneli eşik
    win = _iso_dt(user.get("fail_window_start"))
    cnt = int(user.get("failed_attempts") or 0)
    acct_hot = bool(win and (now - win) <= timedelta(minutes=LOCKOUT_WINDOW_MIN)
                    and cnt >= ACCOUNT_FAIL_THRESHOLD)
    lu = _iso_dt(user.get("locked_until"))
    hard_locked = bool(lu and lu > now)
    if not (acct_hot or hard_locked):
        return False, 0
    if await _is_known_ip(user.get("id"), ip):
        return False, 0
    if user.get("is_admin") is True or user.get("is_super_admin") is True:
        # Personel: sert kilit yok → artan bekleme.
        last = _iso_dt(user.get("last_failed_at"))
        delay = min(ACCOUNT_DELAY_MAX_SEC, 2 ** max(0, min(cnt - ACCOUNT_FAIL_THRESHOLD, 6)))
        if acct_hot and last:
            wait = delay - (now - last).total_seconds()
            if wait > 0:
                return True, int(wait) + 1
        return False, 0
    if hard_locked:
        return True, int((lu - now).total_seconds())
    return False, 0


async def register_failed_login(email: str, ip: str = None) -> None:
    """Hatalı girişi IP+hesap ve hesap-geneli sayaçlara işler; eşikte ilgili kilidi koyar."""
    if not email:
        return
    email = email.lower()
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    user = await db.users.find_one(
        {"email": email},
        {"_id": 0, "id": 1, "failed_attempts": 1, "fail_window_start": 1, "first_failed_at": 1,
         "is_admin": 1, "is_super_admin": 1})
    if user:
        await _register_failed_account_counter(user, email, now)
    # ── IP+hesap sayacı (kayıtlı olmayan e-postalarda da — enumeration olmasın) ──
    await _register_failed_ip_account(email, ip, now)


async def _register_failed_account_counter(user: dict, email: str, now) -> None:
    now_iso = now.isoformat()
    # ── hesap-geneli sayaç ──
    win = _iso_dt(user.get("fail_window_start"))
    upd = {"last_failed_at": now_iso}
    if user.get("failed_attempts") is None or not win or (now - win) > timedelta(minutes=LOCKOUT_WINDOW_MIN):
        upd.update({"failed_attempts": 1, "fail_window_start": now_iso})
        new_count = 1
    else:
        new_count = int(user.get("failed_attempts") or 0) + 1
        upd["failed_attempts"] = new_count
    if not user.get("first_failed_at"):
        upd["first_failed_at"] = now_iso  # kilit "serisi" başlangıcı (panelden açılınca silinir)
    _staff = user.get("is_admin") is True or user.get("is_super_admin") is True
    if new_count >= ACCOUNT_FAIL_THRESHOLD and not _staff:
        upd["locked_until"] = (now + timedelta(minutes=LOCKOUT_DURATION_MIN)).isoformat()
    await db.users.update_one({"email": email}, {"$set": upd})


async def _register_failed_ip_account(email: str, ip, now) -> None:
    now_iso = now.isoformat()
    try:
        key = _lock_key(email, ip)
        rec = await db.login_ip_failures.find_one({"key": key}, {"_id": 0, "count": 1, "window_start": 1})
        rwin = _iso_dt((rec or {}).get("window_start"))
        if not rec or not rwin or (now - rwin) > timedelta(minutes=LOCKOUT_WINDOW_MIN):
            rset = {"key": key, "email": email, "ip": ip or "", "count": 1, "window_start": now_iso}
            rcount = 1
        else:
            rcount = int(rec.get("count") or 0) + 1
            rset = {"count": rcount}
        if rcount >= LOCKOUT_THRESHOLD:
            rset["locked_until"] = (now + timedelta(minutes=LOCKOUT_DURATION_MIN)).isoformat()
            rset["locked_at"] = now_iso
        await db.login_ip_failures.update_one({"key": key}, {"$set": rset}, upsert=True)
        # Fırsatçı temizlik (~%2): 1 günden eski kayıtlar.
        if random.random() < 0.02:
            await db.login_ip_failures.delete_many(
                {"window_start": {"$lt": (now - timedelta(days=1)).isoformat()}})
    except Exception as _e:
        logger.warning(f"login_ip_failures güncellenemedi: {type(_e).__name__}")


async def reset_failed_login(email: str, ip: str = None) -> None:
    """Başarılı girişte YALNIZ bu IP'nin IP+hesap sayacı temizlenir. Hesap-geneli sayaç penceresi
    dolunca kendiliğinden sıfırlanır — böylece sahibin girişi, saldırganın başka IP'lerdeki
    kilitlerini/sayaçlarını sıfırlamaz. Müşteri sert kilidi (locked_until) bilinen IP'den girişte
    zaten uygulanmaz; başarılı girişte kaldırılır."""
    if not email:
        return
    email = email.lower()
    try:
        await db.login_ip_failures.delete_one({"key": _lock_key(email, ip)})
    except Exception:
        pass
    await db.users.update_one({"email": email}, {"$unset": {"locked_until": ""}})


# ---------------------------------------------------------------------------
# IP-LEVEL BRUTE FORCE BLOCKLIST
# ---------------------------------------------------------------------------
# Hesap-bazlı lockout (yukarıda) tek bir email'i koruyor. IP-level blocklist
# ise: aynı IP'den 1 saatte 50+ failed login olduğunda 24 saat ban koyar
# (collection: ip_blocklist). Botnet/distribuited scanning saldırılarını
# erken durdurur. `auth_audit_logs` ile entegre.
IP_BLOCK_WINDOW_MIN = 60       # 1 saatlik pencere
IP_BLOCK_THRESHOLD = 50        # bu pencerede 50+ failed login → ban
IP_BLOCK_DURATION_HOURS = 24   # ban süresi


async def is_ip_blocked(ip: str) -> tuple[bool, int]:
    """Return (blocked, retry_after_seconds). Manuel admin ban ve otomatik
    threshold ban'ları aynı koleksiyonda tutar (`ip_blocklist`)."""
    if not ip:
        return False, 0
    doc = await db.ip_blocklist.find_one({"ip": ip}, {"_id": 0, "blocked_until": 1, "permanent": 1})
    if not doc:
        return False, 0
    if doc.get("permanent"):
        return True, 0
    bu = doc.get("blocked_until")
    if not bu:
        return False, 0
    try:
        until = datetime.fromisoformat(bu)
    except Exception:
        return False, 0
    now = datetime.now(timezone.utc)
    if until > now:
        return True, int((until - now).total_seconds())
    # Süresi dolmuş — pasifle
    await db.ip_blocklist.delete_one({"ip": ip})
    return False, 0


async def register_failed_login_ip(ip: str) -> None:
    """IP'nin 1 saatlik pencerede başarısız login sayısını sayar.
    Threshold aşıldıysa 24 saatlik geçici ban koyar."""
    if not ip:
        return
    since = (datetime.now(timezone.utc) - timedelta(minutes=IP_BLOCK_WINDOW_MIN)).isoformat()
    fail_count = await db.auth_audit_logs.count_documents({
        "event": "login",
        "success": False,
        "ip": ip,
        "created_at": {"$gte": since},
    })
    if fail_count >= IP_BLOCK_THRESHOLD:
        until = datetime.now(timezone.utc) + timedelta(hours=IP_BLOCK_DURATION_HOURS)
        # Yeni ban mı? (aktif ban sürerken login zaten erken reddedilir; yine de çift uyarı olmasın)
        _prev = await db.ip_blocklist.find_one({"ip": ip}, {"_id": 0, "blocked_until": 1, "permanent": 1})
        _prev_active = bool(_prev and (_prev.get("permanent") or
                                       (_iso_dt(_prev.get("blocked_until")) or datetime.min.replace(tzinfo=timezone.utc))
                                       > datetime.now(timezone.utc)))
        await db.ip_blocklist.update_one(
            {"ip": ip},
            {"$set": {
                "ip": ip,
                "blocked_until": until.isoformat(),
                "blocked_at": datetime.now(timezone.utc).isoformat(),
                "reason": f"auto: {fail_count} failed logins in {IP_BLOCK_WINDOW_MIN}min",
                "trigger_count": fail_count,
                "auto_blocked": True,
            }, "$setOnInsert": {"id": str(uuid.uuid4())}},
            upsert=True,
        )
        if not _prev_active:
            try:
                from security.events import security_event, fire_and_forget
                fire_and_forget(security_event(
                    "ip_autoban", None,
                    {"target": ip, "ip": ip, "failed_logins": fail_count,
                     "window_min": IP_BLOCK_WINDOW_MIN, "ban_hours": IP_BLOCK_DURATION_HOURS, "fp": ip}))
            except Exception:
                pass


# A1.3 (GÜVENLİK): Cloudflare'in eklediği paylaşımlı gizli header ile "istek gerçekten
# CF üzerinden mi geldi" doğrulaması. CF Transform Rule ile her isteğe
# `X-Meluni-Edge: <secret>` eklenir; origin bu secret'ı bilir. Böylece origin'e
# DOĞRUDAN (CF'i baypas ederek) gelen bir saldırgan `cf-connecting-ip` header'ını
# spoof edip rate-limit/lockout/blocklist anahtarını rotasyonlayamaz.
#   - CF_EDGE_SECRET set + header eşleşiyorsa → cf-connecting-ip güvenilir (gerçek client).
#   - CF_EDGE_SECRET set + header YOK/yanlış → istek CF'i baypas etmiş; cf-connecting-ip
#     güvenilmez, gerçek TCP peer (spoof edilemez) anahtar olur.
#   - CF_EDGE_SECRET set DEĞİL → eski davranış korunur (regresyon yok). Beyaz-etiket
#     onboarding: yeni firmada bu secret + CF Transform Rule kurulmalı.
_CF_EDGE_SECRET = (os.environ.get("CF_EDGE_SECRET") or "").strip()

# GÜVENLİK (denetim 2026-10, #11): CF_EDGE_SECRET YOKKEN `cf-connecting-ip` koşulsuz güveniliyordu →
# origin'e doğrudan gelen saldırgan header'ı her istekte değiştirip rate-limit / hesap kilidi / IP
# blok anahtarını rotasyonlayabiliyordu. Secret yokken artık header YALNIZ isteği barındırma
# kenarına (Railway) bağlayan son hop bir Cloudflare IP'si ise güvenilir. Son hop: X-Forwarded-For'un
# SON girdisi (Railway kenarı bağlanan IP'yi sona ekler; istemci yalnız öne ekleyebilir) — yoksa TCP
# peer. Son hop özel/iç ağ adresiyse (gerçek üst bağlantı görülemiyor) eski davranış korunur
# (tüm kullanıcıların tek anahtara çökmesini ve toplu kilitlenmeyi önlemek için).
# Cloudflare yayımlanmış IP aralıkları (https://www.cloudflare.com/ips/); env CF_IP_RANGES_EXTRA ile
# (virgülle) genişletilebilir.
_CF_IP_RANGES_RAW = [
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
    "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32",
    "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32",
]


def _build_cf_networks():
    import ipaddress as _ipa
    nets = []
    extra = [x.strip() for x in (os.environ.get("CF_IP_RANGES_EXTRA") or "").split(",") if x.strip()]
    for r in _CF_IP_RANGES_RAW + extra:
        try:
            nets.append(_ipa.ip_network(r, strict=False))
        except Exception:
            pass
    return nets


_CF_NETWORKS = _build_cf_networks()


def _parse_ip(value):
    import ipaddress as _ipa
    try:
        return _ipa.ip_address(str(value or "").strip())
    except Exception:
        return None


def _upstream_hop(request):
    """Barındırma kenarına (Railway) bağlanan IP: XFF'in SON girdisi (geçerli IP ise), yoksa TCP peer."""
    try:
        xff = request.headers.get("x-forwarded-for") or ""
        if xff:
            last = _parse_ip(xff.split(",")[-1])
            if last is not None:
                return last
    except Exception:
        pass
    try:
        return _parse_ip(request.client.host) if request.client else None
    except Exception:
        return None


def _cf_header_trustworthy_without_secret(request) -> bool:
    """Secret yokken: cf-connecting-ip'ye güvenilebilir mi? Son hop Cloudflare ise EVET; son hop
    özel/iç ağ (belirlenemiyor) ise eski davranış (EVET); son hop genel ve CF-dışı ise HAYIR."""
    hop = _upstream_hop(request)
    if hop is None:
        return True
    if not hop.is_global:  # özel / CGNAT / loopback / link-local → gerçek üst hop görülemiyor
        return True
    try:
        if any(hop in n for n in _CF_NETWORKS if n.version == hop.version):
            return True
    except Exception:
        return True
    return False


def _edge_trusted(request) -> bool:
    """CF_EDGE_SECRET yapılandırılmışsa: istek geçerli edge secret'ı taşıyor mu?
    Yapılandırılmamışsa: istek bir Cloudflare kenarından mı geldi (son hop CF aralığında)."""
    if not _CF_EDGE_SECRET:
        try:
            return _cf_header_trustworthy_without_secret(request)
        except Exception:
            return True
    try:
        import hmac as _hmac
        hdr = (request.headers.get("x-meluni-edge") or "").strip()
        return bool(hdr) and _hmac.compare_digest(hdr, _CF_EDGE_SECRET)
    except Exception:
        return False


def client_ip_from_request(request) -> str:
    """Return the real client IP.

    1. CF-Connecting-IP — YALNIZCA istek CF üzerinden geldiyse (edge secret doğrulanır);
       Cloudflare'in set ettiği spoof-edilemez gerçek client IP.
    2. Edge doğrulanmadıysa (doğrudan origin erişimi) veya cf-ip yoksa → gerçek TCP peer
       (request.client.host) — saldırgan bunu spoof edemez.
    3. X-Forwarded-For ilk hop — CF arkasında olmayan meşru istekler için ara fallback.
    """
    if not request:
        return ""
    if _edge_trusted(request):
        # IPv6 uyumu (Meta plan §9): Cloudflare "Pseudo IPv4 = Overwrite headers" modunda
        # cf-connecting-ip pseudo-IPv4'e çevrilir, GERÇEK client IPv6 'cf-connecting-ipv6'da
        # gelir. Header MEVCUTSA gerçek IPv6 tercih edilir (browser'la aynı sürüm → Meta
        # IPv6/IPv4 uyumsuzluğu kalkar). Header yoksa (normal mod) davranış AYNI kalır.
        # Kapatma: env CAPI_PREFER_CF_IPV6=0.
        if os.environ.get("CAPI_PREFER_CF_IPV6", "1") == "1":
            cf_v6 = (request.headers.get("cf-connecting-ipv6")
                     or request.headers.get("CF-Connecting-IPv6"))
            if cf_v6 and cf_v6.strip():
                return cf_v6.strip()
        cf_ip = request.headers.get("cf-connecting-ip") or request.headers.get("CF-Connecting-IP")
        if cf_ip:
            return cf_ip.strip()
        xff = request.headers.get("x-forwarded-for") or request.headers.get("X-Forwarded-For")
        if xff:
            return xff.split(",")[0].strip()
    elif not _CF_EDGE_SECRET:
        # Secret yok + istek CF-dışı genel bir IP'den geldi (doğrudan origin erişimi): spoof
        # edilebilir cf-connecting-ip/XFF-ilk-hop YERİNE barındırma kenarının gördüğü son hop.
        _hop = _upstream_hop(request)
        if _hop is not None:
            return str(_hop)
    # Edge güvenilmez VEYA cf/xff yok → spoof edilemez TCP peer'ı kullan.
    try:
        return request.client.host if request.client else ""
    except Exception:
        return ""


# Shared SlowAPI limiter instance (single instance per app)
try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address as _gra

    def _rate_key(request):
        # A1.3: cf-connecting-ip yalnız edge doğrulanırsa güvenilir (spoof koruması);
        # değilse gerçek TCP peer. client_ip_from_request ile aynı mantık — tek kaynak.
        ip = client_ip_from_request(request)
        return ip or _gra(request)

    # key_style="endpoint": sınır UÇ (fonksiyon) + istemci bazlı; tam URL değil — aksi halde
    # /track/{kod} gibi uçlarda her farklı kod ayrı sayılıp numaralandırma hiç yavaşlamıyordu.
    limiter = Limiter(key_func=_rate_key, default_limits=[], key_style="endpoint")
except Exception as _limiter_init_exc:  # pragma: no cover
    # GÜVENLİK (denetim 2026-10, #11): eskiden limiter=None olup TÜM `limiter.limit(...)`
    # dekoratörleri sessizce no-op'a düşüyordu (giriş/OTP/MFA brute-force korumasız). Artık hata
    # YÜKSEK SESLE loglanır ve süreç-içi (in-memory) yedek limiter devreye girer.
    limiter = None
    try:
        logging.getLogger(__name__).error(
            "RATE_LIMITER_INIT_FAILED (%r) — in-memory yedek limiter kullanılıyor", _limiter_init_exc
        )
    except Exception:
        pass


def global_rate_key(request) -> str:
    """IP'DEN BAĞIMSIZ genel sayaç anahtarı (key_style="endpoint" ile uç başına tek sayaç).
    Numaralandırmaya açık uçlarda IP havuzu (dönen IP) kullanan saldırganı da sınırlar."""
    return "global"


class _InMemoryLimiter:
    """slowapi yüklenemezse kullanılan minimal yedek. `@limiter.limit("3/minute;20/day")`
    sözdizimini destekler; anahtar client_ip_from_request (+ uç adı). Aşımda HTTP 429.
    Süreç-içi (worker başına) sayaç — slowapi'nin memory:// deposuyla aynı kapsam."""

    _UNITS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400,
              "seconds": 1, "minutes": 60, "hours": 3600, "days": 86400}

    def __init__(self):
        import collections as _col
        import threading as _thr
        self._hits = _col.defaultdict(_col.deque)
        self._lock = _thr.Lock()
        # SlowAPIMiddleware'e (server.py yine de bağlarsa) "atla" sinyali: sınırlama bu sınıfın
        # dekoratöründe yapılır; middleware slowapi-özel iç alanlara erişip çökmesin.
        self.enabled = False

    def _parse(self, spec: str):
        out = []
        for part in str(spec or "").split(";"):
            part = part.strip()
            if not part or "/" not in part:
                continue
            n, _, unit = part.partition("/")
            unit = unit.strip().lower()
            mult = 1
            if " " in unit:  # "5 minutes" biçimi
                _m, _, unit = unit.partition(" ")
                try:
                    mult = int(_m)
                except Exception:
                    mult = 1
            try:
                out.append((int(n.strip()), self._UNITS.get(unit, 60) * mult))
            except Exception:
                continue
        return out

    def _check(self, key: str, rules) -> bool:
        import time as _t
        now = _t.monotonic()
        with self._lock:
            for count, window in rules:
                dq = self._hits[(key, window, count)]
                while dq and now - dq[0] > window:
                    dq.popleft()
                if len(dq) >= count:
                    return False
            for count, window in rules:
                self._hits[(key, window, count)].append(now)
            if len(self._hits) > 50000:  # kaba bellek koruması
                self._hits.clear()
        return True

    def limit(self, spec, *args, **kwargs):
        rules = self._parse(spec)
        import functools as _ft

        def _decorator(func):
            @_ft.wraps(func)
            async def _wrapper(*a, **kw):
                req = kw.get("request")
                if req is None:
                    for v in list(a) + list(kw.values()):
                        if hasattr(v, "headers") and hasattr(v, "client"):
                            req = v
                            break
                if req is not None and rules:
                    key = f"{client_ip_from_request(req)}|{func.__module__}.{func.__name__}"
                    if not self._check(key, rules):
                        raise HTTPException(status_code=429,
                                            detail="Çok fazla istek. Lütfen biraz sonra tekrar deneyin.")
                return await func(*a, **kw)
            return _wrapper
        return _decorator


if limiter is None:
    limiter = _InMemoryLimiter()


# Password helpers
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()

async def invalidate_password_reset_tokens(user_id: str) -> None:
    """ID3-04: hesabın BEKLEYEN tüm şifre-sıfırlama token'larını (e-posta linki / SMS sonrası token) ve
    kullanılmamış SMS sıfırlama kodlarını geçersiz kılar. Şifre değişince/sıfırlanınca ya da hesap
    sahipliği kesinleşince çağrılır — aksi halde posta kutusuna/SIM'e kısa süre erişip link/kod alan
    kişi, sahibi şifresini değiştirdikten SONRA da (30 dk'ya kadar) şifreyi yeniden belirleyebiliyordu.
    Deneme/gönderim bütçesi sayaçlarına dokunmaz (kayıtlar silinmez). Best-effort; hata loglanır."""
    if not user_id:
        return
    try:
        await db.password_reset_otps.update_many(
            {"user_id": user_id, "reset_token_expires": {"$gt": 0}},
            {"$set": {"reset_token_expires": 0, "invalidated": True}})
        await db.password_reset_otps.update_many(
            {"user_id": user_id, "used": False},
            {"$set": {"used": True, "invalidated": True}})
    except Exception as _e:
        logger.warning(f"şifre sıfırlama token'ları geçersiz kılınamadı: {type(_e).__name__}")


async def secure_social_link(user: dict, provider: str) -> dict:
    """GÜVENLİK (denetim H2 — ön-hesap ele geçirme): sosyal giriş, e-posta eşleşmesiyle MEVCUT bir
    hesaba bağlanırken hesabın e-postası doğrulanmamışsa, o hesabı daha önce BAŞKASI (e-postanın
    gerçek sahibi olmayan biri) parolayla açmış olabilir. Sağlayıcı e-postayı doğruladığı için
    hesabın sahibi artık kesinleşir: eski parola geçersiz kılınır (rastgele), eski oturumlar
    düşürülür (token_version+1) ve e-posta doğrulanmış işaretlenir. Aynı sağlayıcıyla açılmış
    ya da e-postası zaten doğrulanmış hesaplara dokunulmaz."""
    if not user or user.get("email_verified") is True or user.get("auth_provider") == provider:
        return user
    if user.get("is_admin") is True or user.get("is_super_admin") is True:
        return user  # personel hesabı: parolası DEĞİŞTİRİLMEZ (sosyal giriş zaten admin yetkisi vermez)
    import secrets as _sec
    _now = datetime.now(timezone.utc).isoformat()
    _set = {"password": hash_password(_sec.token_urlsafe(24)), "email_verified": True,
            "social_link_secured_at": _now, "social_link_secured_by": provider}
    # ID3-06 (ön-hesap ele geçirme kalıcılığı): hesabı önceden açan kişi KENDİ telefonunu da yazmış
    # olabilir — kayıtta telefon OTP'siz alınır ve alanı olmayan hesap "doğrulanmış" sayılır. Şifre
    # rastgele yapılsa bile o kişi SMS ile şifre sıfırlayıp (ya da elindeki bekleyen sıfırlama
    # token'ıyla) e-postanın gerçek sahibinin artık kullandığı hesaba geri girebiliyordu. Telefon
    # SİLİNMEZ; yalnız doğrulanmamış işaretlenir (SMS sıfırlama/MFA SMS hedefi olamaz; sahibi
    # İYS OTP ile yeniden doğrulayabilir) ve bekleyen sıfırlama kod/token'ları öldürülür.
    # Denetim #2: telefon KVKK gereği şifreli saklanır (phone_enc/phone_hash) — düz "phone" alanı
    # boş olsa da numara vardır; yalnız düz alana bakmak saldırganın numarasını güvenilir bırakıyordu.
    _has_phone = bool(str(user.get("phone") or "").strip())
    if not _has_phone:
        try:
            from security.contact_pii import has_value as _has_val
            _has_phone = _has_val(user, "phone") or bool(user.get("phone_hash"))
        except Exception:
            _has_phone = bool(user.get("phone_enc") or user.get("phone_hash"))
    if _has_phone:
        _set["phone_verified"] = False
        _set["phone_unverified_reason"] = f"social_link_secured:{provider}"
    await db.users.update_one({"id": user["id"]}, {"$set": _set, "$inc": {"token_version": 1}})
    await invalidate_password_reset_tokens(user["id"])
    user["token_version"] = int(user.get("token_version") or 0) + 1
    user["email_verified"] = True
    if "phone_verified" in _set:
        user["phone_verified"] = False
    try:
        logger.info(f"[auth] sosyal bağlama güvenceye alındı provider={provider} user={user.get('id')}")
    except Exception:
        pass
    return user


def verify_password(password: str, hashed: str) -> bool:
    if not hashed or not isinstance(hashed, str):
        return False
    # Reject legacy weak hashes (md5/sha1 length) — force bcrypt-only
    if not (hashed.startswith("$2a$") or hashed.startswith("$2b$") or hashed.startswith("$2y$")):
        return False
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except Exception:
        return False

# Personel (is_admin) oturum ömrü — env STAFF_TOKEN_TTL_HOURS (varsayılan 24 saat; en çok 168).
# Kullanıcı isteği: panele giren personelin oturumu aynı cihazda 24 saat sonlanmasın. Etkin kullanımda
# /auth/staff/refresh ile yenilenir (ilk girişten en çok panel.max_session_days). Müşteri oturumu 7 gün.
_STAFF_TTL_DEFAULT_H = 24


def _staff_token_ttl() -> timedelta:
    try:
        h = float((os.environ.get("STAFF_TOKEN_TTL_HOURS") or "").strip() or _STAFF_TTL_DEFAULT_H)
        if h <= 0:
            h = _STAFF_TTL_DEFAULT_H
    except Exception:
        h = _STAFF_TTL_DEFAULT_H
    return timedelta(hours=min(h, 168))


def create_token(user_id: str, is_admin: bool = False, token_version: int = 0,
                 auth_time=None, amr=None) -> str:
    """auth_time (epoch sn) — kullanıcının KİMLİĞİNİ (şifre/MFA/sosyal sağlayıcı) en son KANITLADIĞI an.
    Yalnız giriş/MFA doğrulama/şifre değişimi gibi uçlar verir; hassas işlemler (MFA faktörü
    bağlama/değiştirme) "son 5 dk'da kimlik doğrulandı" kanıtı olarak kullanır. Eski token'larda
    yoktur → geriye uyumlu (yalnız 'yakın zamanda doğrulandı' sayılmaz).
    Ek talepler: typ ("staff"|"customer" — require_admin müşteri token'ını reddeder),
    ep (küresel oturum-iptal epoch'u), jti (oturum kimliği — personel boşta-kalma takibi)."""
    now = datetime.now(timezone.utc)
    payload = {
        "user_id": user_id,
        "is_admin": is_admin,
        "tv": int(token_version or 0),  # token_version — parola değişince/sıfırlanınca bump edilir
        "typ": "staff" if is_admin else "customer",
        "ep": int(_EPOCH_CACHE.get("value") or 0),
        "jti": uuid.uuid4().hex,
        "iat": now,
        "iss": JWT_ISSUER,
        "exp": now + (_staff_token_ttl() if is_admin else timedelta(days=7)),
    }
    if auth_time is not None:
        try:
            payload["auth_time"] = int(auth_time)
        except Exception:
            pass
    # amr: oturumun nasıl kanıtlandığı (ör. ["mfa", "sms"]) — yalnız ikinci faktör doğrulandığında verilir.
    if amr:
        payload["amr"] = [str(x)[:20] for x in amr][:4]
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


# ─────────────── Küresel oturum iptali (security epoch) ───────────────
# settings._id="security_epoch" {epoch:int, bumped_at:float(epoch sn)}. Token'daki ep < epoch ise
# (ep yoksa 0) VE token bump'tan ÖNCE verilmişse oturum geçersiz. Bellekte ≤10 sn önbellek →
# istek başına DB okuması yok. Okuma hatasında son bilinen değer kullanılır (kimse kilitlenmez).
_EPOCH_CACHE = {"value": 0, "bumped_at": 0.0, "loaded_at": 0.0}
_EPOCH_TTL_SEC = 10.0


async def get_security_epoch() -> tuple:
    import time as _t
    now = _t.monotonic()
    if _EPOCH_CACHE["loaded_at"] and (now - _EPOCH_CACHE["loaded_at"]) < _EPOCH_TTL_SEC:
        return int(_EPOCH_CACHE["value"]), float(_EPOCH_CACHE["bumped_at"])
    try:
        doc = await db.settings.find_one({"_id": "security_epoch"}, {"_id": 0, "epoch": 1, "bumped_at": 1}) or {}
        _EPOCH_CACHE["value"] = int(doc.get("epoch") or 0)
        _EPOCH_CACHE["bumped_at"] = float(doc.get("bumped_at") or 0.0)
    except Exception as _e:
        logger.warning(f"security epoch okunamadı: {type(_e).__name__}")
    _EPOCH_CACHE["loaded_at"] = now
    return int(_EPOCH_CACHE["value"]), float(_EPOCH_CACHE["bumped_at"])


async def bump_security_epoch() -> int:
    """TÜM oturumları geçersiz kılar (epoch+1). Yeni epoch'u döndürür."""
    import time as _t
    from pymongo import ReturnDocument
    now_f = datetime.now(timezone.utc).timestamp()
    doc = await db.settings.find_one_and_update(
        {"_id": "security_epoch"},
        {"$inc": {"epoch": 1}, "$set": {"id": "security_epoch", "bumped_at": now_f,
                                        "bumped_at_iso": datetime.now(timezone.utc).isoformat()}},
        upsert=True, return_document=ReturnDocument.AFTER) or {}
    _EPOCH_CACHE.update({"value": int(doc.get("epoch") or 0), "bumped_at": now_f,
                         "loaded_at": _t.monotonic()})
    return int(doc.get("epoch") or 0)


def _epoch_revoked(payload: dict, epoch: int, bumped_at: float) -> bool:
    try:
        ep = int((payload or {}).get("ep") or 0)
    except Exception:
        ep = 0
    if ep >= int(epoch or 0):
        return False
    # Başka worker'ın eski önbelleğiyle bump'tan SONRA verilmiş token'lar (ep eski) geçerli kalır.
    try:
        iat = float((payload or {}).get("iat") or 0)
    except Exception:
        iat = 0.0
    if bumped_at and iat and iat > bumped_at:
        return False
    return True


# ─────────────── Personel boşta-kalma zaman aşımı (sunucu tarafı) ───────────────
# İşletme Kuralı panel.auto_logout_minutes (0 = kapalı). Token jti başına son-görülme bellekte;
# DB'ye (staff_sessions) en çok dakikada bir yazılır (çok-worker/yeniden başlatma). Reddetmeden
# önce DB'deki değere bakılır (başka worker aktivite görmüş olabilir). jti'siz eski token'lar ve
# okuma hataları → kontrol atlanır (fail-open: kimse kilitlenmez). Acil kapatma:
# env STAFF_IDLE_TIMEOUT=off.
_IDLE_SEEN: dict = {}

# ─────────────── Makine-okunur oturum-düşme kodu (ADM-15) ───────────────
# 401 yanıtının `detail` METNİ mevcut istemciler/testler için AYNEN kalır; neden ayrıca bir
# başlıkla bildirilir. Panel/mobil uygulama `session_revoked` görünce cihazdaki TÜM personel
# önbelleğini siler (frontend/src/lib/staffDataWipe.js). CORS'ta expose edilir (server.py).
AUTH_CODE_HEADER = "X-Auth-Code"
SESSION_REVOKED_CODE = "session_revoked"   # hesap pasif/silinmiş, token_version (logout-all/şifre), epoch
SESSION_IDLE_CODE = "session_idle"         # personel boşta-kalma süresi doldu


def session_revoked_401(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail,
                         headers={AUTH_CODE_HEADER: SESSION_REVOKED_CODE})


_IDLE_PERSIST_SEC = 60
_IDLE_INDEX_DONE = {"v": False}


async def _enforce_staff_idle(payload: dict, user: dict) -> None:
    import time as _t
    if (os.environ.get("STAFF_IDLE_TIMEOUT") or "").strip().lower() in ("0", "off", "false", "no", "disable"):
        return
    jti = str((payload or {}).get("jti") or "")
    if not jti:
        return
    try:
        from business_rules import get_rule as _gr
        minutes = float(await _gr(db, "panel.auto_logout_minutes", 1440) or 0)
    except Exception:
        return
    if minutes <= 0:
        return
    window = minutes * 60.0
    now = _t.time()
    rec = _IDLE_SEEN.get(jti)
    if rec is None or (now - rec[0]) > window:
        try:
            doc = await db.staff_sessions.find_one({"jti": jti}, {"_id": 0, "last_seen": 1})
        except Exception:
            return
        cands = [x for x in ((rec[0] if rec else None), (float(doc.get("last_seen") or 0) if doc else None)) if x]
        if not cands:
            try:
                cands = [float(payload.get("iat") or 0)]
            except Exception:
                cands = [now]
        last = max(cands) or now
        if (now - last) > window:
            raise HTTPException(status_code=401,
                                detail="Uzun süre işlem yapılmadığı için oturumunuz kapatıldı. Tekrar giriş yapın.",
                                headers={AUTH_CODE_HEADER: SESSION_IDLE_CODE})
        rec = (last, rec[1] if rec else 0.0)
    persisted = rec[1]
    if (now - persisted) > _IDLE_PERSIST_SEC:
        try:
            if not _IDLE_INDEX_DONE["v"]:
                _IDLE_INDEX_DONE["v"] = True
                try:
                    await db.staff_sessions.create_index("jti", unique=True)
                    await db.staff_sessions.create_index("expires_at", expireAfterSeconds=0)
                except Exception:
                    pass
            _exp = payload.get("exp")
            _exp_dt = (datetime.fromtimestamp(float(_exp), tz=timezone.utc) if _exp
                       else datetime.now(timezone.utc) + timedelta(days=1))
            await db.staff_sessions.update_one(
                {"jti": jti},
                {"$set": {"last_seen": now, "user_id": (user or {}).get("id"), "expires_at": _exp_dt}},
                upsert=True)
            persisted = now
        except Exception:
            pass
    _IDLE_SEEN[jti] = (now, persisted)
    if len(_IDLE_SEEN) > 20000:
        cutoff = now - 86400
        for k in [k for k, v in _IDLE_SEEN.items() if v[0] < cutoff][:10000]:
            _IDLE_SEEN.pop(k, None)


_TYP_MISSING_LOG = {"at": 0.0}


def now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


RECENT_AUTH_MAX_AGE_SEC = 300


def token_auth_time(credentials) -> int:
    """Bearer token'daki auth_time (yoksa/geçersizse 0). credentials: HTTPAuthorizationCredentials
    ya da ham token string'i."""
    try:
        raw = credentials if isinstance(credentials, str) else getattr(credentials, "credentials", "")
        if not raw:
            return 0
        return int(_decode_jwt_strict(raw).get("auth_time") or 0)
    except Exception:
        return 0


def is_recent_auth(credentials, max_age: int = RECENT_AUTH_MAX_AGE_SEC) -> bool:
    at = token_auth_time(credentials)
    return bool(at) and 0 <= (now_ts() - at) <= max_age


def _token_revoked(payload: dict, user: dict) -> bool:
    """Token iptal edilmiş mi? Kullanıcının token_version'ı, token'daki tv'den büyükse
    (parola değişikliği/sıfırlama sonrası) eski token GEÇERSİZ. Grandfathering: ikisi de
    yoksa 0 → 0 > 0 False → mevcut oturumlar bozulmaz."""
    try:
        return int((user or {}).get("token_version", 0) or 0) > int((payload or {}).get("tv", 0) or 0)
    except Exception:
        return True


def _decode_jwt_strict(token: str) -> dict:
    """Strictly decode JWT — locks algorithm to HS256 and validates issuer.
    Raises jwt exceptions on tamper/expiry which the caller maps to HTTP errs."""
    payload = jwt.decode(
        token,
        JWT_SECRET,
        algorithms=[JWT_ALGORITHM],
        options={"require": ["exp", "user_id"], "verify_signature": True},
        issuer=JWT_ISSUER,
    )
    # GÜVENLİK (MFA atlatma): Tek-amaçlı token'lar (ör. purpose="mfa_pending") TAM
    # OTURUM olarak KABUL EDİLMEZ. Aksi halde login'de dönen mfa_token doğrudan Bearer
    # olarak kullanilip ikinci faktör atlanabiliyordu.
    if payload.get("purpose"):
        raise jwt.InvalidTokenError("Tek-amaçlı token oturum için kullanılamaz")
    return payload


# ── Denetim #11: vitrin "üye mi?" kararı DOĞRULANMIŞ oturuma dayanır ─────────────────────────
# Eskiden üyelere özel katalog/menü kararları yalnız JWT imzasına bakıyordu: pasif edilmiş,
# şifresi sıfırlanmış (token_version) ya da küresel oturum iptaline (security epoch) takılmış
# müşteri 7 günlük token ömrü boyunca üyelere özel ürünleri görmeye devam ediyordu.
# MemberPrincipalMiddleware her Bearer'lı istekte kullanıcıyı get_current_user ile AYNI
# kontrollerle doğrular, sonucu istek durumuna (scope.state) yazar; senkron yardımcılar
# (categories._req_is_member / products.request_is_member) bu sonucu okur. Sonuç token başına
# kısa süre (30 sn) süreç içinde önbelleklenir.
_MEMBER_CACHE: dict = {}
_MEMBER_CACHE_TTL = 30.0
_MEMBER_CACHE_MAX = 5000


async def resolve_member_principal(token: str) -> bool:
    import hashlib as _hl
    import time as _t
    if not token:
        return False
    key = _hl.sha256(token.encode("utf-8", "ignore")).hexdigest()
    now = _t.monotonic()
    hit = _MEMBER_CACHE.get(key)
    if hit and hit[1] > now:
        return hit[0]
    ok = False
    try:
        payload = _decode_jwt_strict(token)
        user = await db.users.find_one({"id": payload["user_id"]},
                                       {"_id": 0, "id": 1, "is_active": 1, "token_version": 1})
        if user and user.get("is_active") is not False and not _token_revoked(payload, user):
            _ep, _ba = await get_security_epoch()
            ok = not _epoch_revoked(payload, _ep, _ba)
    except Exception:
        ok = False
    if len(_MEMBER_CACHE) >= _MEMBER_CACHE_MAX:
        _MEMBER_CACHE.clear()
    _MEMBER_CACHE[key] = (ok, now + _MEMBER_CACHE_TTL)
    return ok


class MemberPrincipalMiddleware:
    """Saf ASGI: Bearer'lı HTTP isteğinde doğrulanmış üyelik sonucunu scope['state']'e yazar."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            tok = ""
            for k, v in scope.get("headers") or []:
                if k == b"authorization":
                    raw = v.decode("latin-1", "ignore")
                    if raw[:7].lower() == "bearer ":
                        tok = raw[7:].strip()
                    break
            scope.setdefault("state", {})
            scope["state"]["_member_ok"] = await resolve_member_principal(tok) if tok else False
        await self.app(scope, receive, send)


def request_member_ok(request) -> bool:
    """İstek doğrulanmış (aktif, iptal edilmemiş) bir kullanıcı oturumu taşıyor mu? Ara katman
    çalışmadıysa (izole çağrı) FAIL-CLOSED: misafir sayılır."""
    try:
        st = (request.scope.get("state") or {}) if request is not None else {}
        return bool(st.get("_member_ok"))
    except Exception:
        return False


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Get current user from JWT token (strict decode)."""
    if not credentials:
        return None
    try:
        payload = _decode_jwt_strict(credentials.credentials)
        user = await db.users.find_one({"id": payload["user_id"]}, {"_id": 0, "password": 0})
        if user and user.get("is_active") is False:
            return None
        if user and _token_revoked(payload, user):
            return None  # parola değişikliği/sıfırlama sonrası eski token
        if user:
            _ep, _ba = await get_security_epoch()
            if _epoch_revoked(payload, _ep, _ba):
                return None  # küresel oturum iptali (security epoch)
            open_user_phone(user)
        return user
    except Exception:
        return None

# ID3-01: kullanıcının KENDİ belgesi istemciye dönerken (GET /auth/me, sosyal giriş yanıtı) kimlik
# bilgisi materyali ASLA taşınmaz — eski şifre hash'leri (çevrimdışı kırılabilir), şifreli MFA sırrı/
# telefonu, kurtarma kodu hash'leri, kilit/deneme sayaçları. XSS ya da çalınmış oturum bunları
# toplayamasın. İstemcinin kullandığı alanlar (ad, e-posta, is_admin, mfa_enabled/mfa_method …) kalır.
_SELF_USER_SECRET_FIELDS = frozenset({
    "password", "password_history", "token_version",
    "mfa_secret_enc", "mfa_pending_secret_enc", "mfa_phone_enc", "mfa_pending_phone_enc",
    "mfa_recovery_codes", "mfa_fail_count", "mfa_fail_window", "mfa_day_fail_count", "mfa_day_window",
    "failed_attempts", "fail_window_start", "first_failed_at", "last_failed_at", "locked_until",
})


def public_self_user(user: dict) -> dict:
    """Kullanıcı belgesinin istemciye dönebilecek kopyası (kimlik bilgisi materyali ayıklanır)."""
    out = {}
    for k, v in (user or {}).items():
        ks = str(k)
        if ks == "_id" or ks in _SELF_USER_SECRET_FIELDS or ks.endswith(("_enc", "_hash", "_hashes")):
            continue
        out[k] = v
    return out


def open_user_phone(user: "dict | None") -> "dict | None":
    """KVKK: üye telefonu at-rest şifreli (phone_enc/phone_hash/phone_last2 — security.contact_pii).
    Oturum kullanıcısı belleğe yüklenirken gerçek değer `phone` anahtarına AÇILIR (SMS, profil, sipariş
    akışları değişmeden çalışır) ve mühür alanları atılır — bu sözlük ASLA olduğu gibi DB'ye yazılmamalı.
    YERİNDE değiştirir; kullanıcıyı döndürür."""
    if not isinstance(user, dict):
        return user
    if user.get("phone_enc") and not user.get("phone"):
        try:
            from security.contact_pii import open_field as _open
            user["phone"] = _open(user, "phone")
        except Exception:
            user["phone"] = ""
    for _suf in ("_enc", "_hash", "_last2"):
        user.pop(f"phone{_suf}", None)
    return user

async def token_is_revoked(credentials) -> bool:
    """İmzası GEÇERLİ ama sunucu tarafında İPTAL edilmiş oturum mu? (hesap pasif/silinmiş,
    token_version artmış — logout-all/şifre değişimi —, küresel epoch). Yalnız 401 yolunda çağrılır
    (başarılı isteklere ek DB okuması getirmez). Bozuk/süresi dolmuş token → False."""
    try:
        raw = credentials if isinstance(credentials, str) else getattr(credentials, "credentials", "")
        if not raw:
            return False
        payload = _decode_jwt_strict(raw)
        user = await db.users.find_one({"id": payload["user_id"]},
                                       {"_id": 0, "is_active": 1, "token_version": 1})
        if not user or user.get("is_active") is False:
            return True
        if _token_revoked(payload, user):
            return True
        _ep, _ba = await get_security_epoch()
        return bool(_epoch_revoked(payload, _ep, _ba))
    except Exception:
        return False


async def unauthorized_401(credentials, detail: str = "Giriş yapmanız gerekiyor") -> HTTPException:
    """Oturumsuz 401 — token iptal edilmişse `X-Auth-Code: session_revoked` başlığıyla (metin aynı)."""
    if await token_is_revoked(credentials):
        return session_revoked_401(detail)
    return HTTPException(status_code=401, detail=detail)


async def require_auth(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Require authentication"""
    user = await get_current_user(credentials)
    if not user:
        raise await unauthorized_401(credentials, "Giriş yapmanız gerekiyor")
    return user

async def require_admin(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Require admin authentication (strict JWT)."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Yetkilendirme gerekli")
    try:
        payload = _decode_jwt_strict(credentials.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token süresi dolmuş")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Geçersiz token")
    except Exception:
        raise HTTPException(status_code=401, detail="Geçersiz token")
    if not payload.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin yetkisi gerekli")
    # Token türü: müşteri token'ı yönetim API'sine KABUL EDİLMEZ. typ'siz eski token'lar süreleri
    # dolana kadar kabul edilir (geriye uyum) ama loglanır.
    _typ = payload.get("typ")
    if _typ == "customer":
        raise HTTPException(status_code=403, detail="Admin yetkisi gerekli")
    if not _typ:
        import time as _t
        if _t.time() - _TYP_MISSING_LOG["at"] > 300:
            _TYP_MISSING_LOG["at"] = _t.time()
            logger.info("[auth] typ talebi olmayan (eski) personel token'ı kabul edildi user=%s",
                        str(payload.get("user_id"))[:12])
    user = await db.users.find_one({"id": payload["user_id"]}, {"_id": 0, "password": 0})
    if not user or user.get("is_active") is False:
        raise session_revoked_401("Hesap devre dışı")
    if user.get("phone_enc"):   # personel telefonu normalde mühürlenmez (MFA'da ayrıca şifreli); savunma
        open_user_phone(user)
    # GÜVENLİK: Yalnız GERÇEK panel personeli admin erişebilir. Panel personeli
    # create_panel_user ile oluşur → atanmış 'role_id' taşır; ya da is_super_admin.
    # Bunların HİÇBİRİ yoksa hesap yetkisizdir (eski/bozuk
    # veride is_admin=True kalmış register kaydı — 'role' alanı bile yok) → admin erişimi REDDEDİLİR.
    _is_staff = (
        user.get("is_super_admin") is True
        or (user.get("role_id") not in (None, ""))
    )
    if user.get("is_admin") is not True or user.get("role") == "customer" or not _is_staff:
        raise HTTPException(status_code=403, detail="Admin yetkisi gerekli")
    _eff_perms = await get_effective_permissions(user)
    if not _eff_perms:
        raise HTTPException(status_code=403, detail="Geçerli bir personel rolü atanmalı")
    if _token_revoked(payload, user):
        raise session_revoked_401("Oturum sonlandırıldı, tekrar giriş yapın")
    _ep, _ba = await get_security_epoch()
    if _epoch_revoked(payload, _ep, _ba):
        raise session_revoked_401("Tüm oturumlar güvenlik nedeniyle sonlandırıldı, tekrar giriş yapın")
    await _enforce_staff_idle(payload, user)
    try:
        CURRENT_STAFF.set(user)  # AUTH24: export/anahtar kapıları isteği yapan personeli görebilsin
        # CHAIN09: akan dışa aktarım, oturumun iptal edilip edilmediğini (token_version / küresel epoch)
        # akış sırasında yeniden denetleyebilsin diye token'ın ilgili talepleri (sır içermez).
        CURRENT_STAFF_TOKEN.set({"tv": payload.get("tv"), "ep": payload.get("ep"),
                                 "iat": payload.get("iat"), "jti": payload.get("jti"),
                                 "amr": list(payload.get("amr") or [])})
        # IAM-02: aynı istekte require_permission rol belgesini İKİNCİ kez okumasın (yalnız bu istek).
        CURRENT_STAFF_PERMS.set((user.get("id"), tuple(_eff_perms)))
    except Exception:
        pass
    return user


# ─────────────── Kurtarma kodu sonrası kısıt (AUTH24) ───────────────
# Personel MFA kurtarma koduyla giriş yaptıysa (ikinci faktör kaybolmuş YA DA kurtarma kodu çalınmış
# olabilir) 24 saat boyunca yüksek riskli işlemler (IBAN değişimi, rol/yetki değişimi, kasa açma, MFA
# kapatma, toplu dışa aktarım, acil anahtarlar, onay verme) YAPILAMAZ — meğer ki kurtarma girişinden
# SONRA yeni bir faktör (Authenticator / SMS telefonu) bağlanmış olsun. Kayıt kullanıcı belgesinde
# (mfa_recovery_login_at) tutulur: token yenileme (logout-all, şifre değişimi) kısıtı kaldıramaz.
import contextvars as _cv

CURRENT_STAFF: "_cv.ContextVar[dict | None]" = _cv.ContextVar("current_staff", default=None)
# require_admin'in doğruladığı token'ın iptal-ilgili talepleri {tv, ep, iat, jti} (CHAIN09 dışa aktarım koruması).
CURRENT_STAFF_TOKEN: "_cv.ContextVar[dict | None]" = _cv.ContextVar("current_staff_token", default=None)
# require_admin'in bu istekte hesapladığı etkin izinler: (user_id, izinler) — require_permission yeniden okumaz.
CURRENT_STAFF_PERMS: "_cv.ContextVar[tuple | None]" = _cv.ContextVar("current_staff_perms", default=None)
RECOVERY_RESTRICT_HOURS = 24
RECOVERY_RESTRICTED_MSG = ("Kurtarma koduyla giriş yaptığınız için bu yüksek riskli işlem 24 saat boyunca "
                           "kapalı. Önce Hesabım → Güvenlik bölümünden yeni bir doğrulama yöntemi "
                           "(Authenticator ya da SMS telefonu) tanımlayın; ardından işlem açılır.")


def recovery_restriction_active(user: dict) -> bool:
    u = user or {}
    rcv = _iso_dt(u.get("mfa_recovery_login_at"))
    if not rcv:
        return False
    if (datetime.now(timezone.utc) - rcv) > timedelta(hours=RECOVERY_RESTRICT_HOURS):
        return False
    for f in ("mfa_factor_enrolled_at", "mfa_enabled_at"):
        at = _iso_dt(u.get(f))
        if at and at > rcv:
            return False
    return True


async def assert_no_recovery_restriction(user: dict, action: str = "") -> None:
    """Kısıt etkinse 403 + güvenlik günlüğü. user: require_admin'in döndürdüğü güncel kayıt."""
    if not recovery_restriction_active(user):
        return
    try:
        await write_audit_log("recovery_restricted_action", user_id=(user or {}).get("id"),
                              email=(user or {}).get("email"), success=False,
                              meta={"action": str(action or "")[:80]})
    except Exception:
        pass
    raise HTTPException(status_code=403, detail=RECOVERY_RESTRICTED_MSG)


async def assert_current_staff_not_recovery_restricted(action: str = "") -> None:
    """İsteği yapan personel (require_admin bağlamı) için kısıt kontrolü — kullanıcıyı parametre
    olarak almayan kapılar (ör. security_switches.require_switch_off('freeze_exports')) için."""
    u = CURRENT_STAFF.get()
    if u:
        await assert_no_recovery_restriction(u, action)


async def optional_admin(request) -> dict | None:
    """require_admin ile AYNI kontroller (imza, is_admin iddiası, aktif hesap, token_version, personel
    rolü/izinleri) — başarısızlıkta istisna yerine None döner. Herkese açık uçlarda iç alanları
    (maliyet, tedarikçi, yayınlanmamış içerik) yalnız GERÇEK personele açmak için kullanılır.
    İmzalı token'daki is_admin iddiası TEK BAŞINA yetmez (pasifleştirilmiş/rolü alınmış personel)."""
    try:
        hdr = (request.headers.get("authorization") or "").strip() if request is not None else ""
        if not hdr.lower().startswith("bearer "):
            return None
        tok = hdr.split(" ", 1)[1].strip()
        if not tok:
            return None
        return await require_admin(HTTPAuthorizationCredentials(scheme="Bearer", credentials=tok))
    except Exception:
        return None


async def get_effective_permissions(user: dict) -> list:
    """Explicit super-admin flag or assigned role only; no implicit email grants."""
    u = user or {}
    if u.get("is_super_admin") is True:
        return ["*"]
    role_id = u.get("role_id") or ""
    if not role_id:
        return []
    role = await db.roles.find_one({"id": role_id}, {"_id": 0})
    if not role:
        return []
    return role.get("permissions", []) or []


def require_permission(perm_key: str, any_in_area: bool = False):
    """Belirli bir RBAC yetkisini ZORUNLU kilan FastAPI dependency uretir.

    super_admin ('*') her yetkiyi gecer. Yetkisi olmayan -> 403.
    any_in_area=True (IAM-02 okuma uçları): izin YA DA aynı alanda (ör. "products.") herhangi bir izin
    yeterlidir — IAM-02 öncesi her personele açık olan liste/okuma uçlarında, alanda yazma izni olup
    görüntüleme izni olmayan rollerin bugünkü erişimi korunur (yetki yükseltmesi yok).
    Kullanim:  current_user: dict = Depends(require_permission("returns.approve"))
    """
    area_prefix = perm_key.split(".", 1)[0] + "."

    async def _checker(current_user: dict = Depends(require_admin)) -> dict:
        try:
            _cached = CURRENT_STAFF_PERMS.get()
        except Exception:  # bağlam değişkeni yoksa (izole kullanım) → DB'den oku
            _cached = None
        if _cached and current_user and _cached[0] == current_user.get("id"):
            perms = _cached[1]
        else:
            perms = await get_effective_permissions(current_user)
        if "*" in perms or perm_key in perms:
            return current_user
        if any_in_area and any(str(p).startswith(area_prefix) for p in perms):
            return current_user
        raise HTTPException(status_code=403, detail=f"Bu işlem için yetkiniz yok ({perm_key})")
    return _checker


async def require_super_admin(current_user: dict = Depends(require_admin)) -> dict:
    """SADECE süper-admin (etkin izin '*'). Kullanıcı/rol yönetimi gibi ayricalik-
    yükseltmeye yol açan işlemler için — herhangi bir is_admin personelin kendini
    super_admin yapmasını (BFLA) engeller."""
    perms = await get_effective_permissions(current_user)
    if "*" not in perms:
        raise HTTPException(status_code=403, detail="Bu işlem yalnızca süper-admin yetkisi gerektirir")
    return current_user

def generate_id() -> str:
    """Generate unique UUID"""
    return str(uuid.uuid4())

async def generate_short_id(collection_name: str) -> str:
    """Generate a unique 4-digit numeric ID (1000-9999) for a collection"""
    for _ in range(100):
        new_id = str(random.randint(1000, 9999))
        existing = await db[collection_name].find_one({"id": new_id}, {"_id": 1})
        if not existing:
            return new_id
    # Fallback if somehow 9000 IDs are exhausted or we get extremely unlucky
    return str(uuid.uuid4())[:4]

def serialize_doc(doc):
    """Serialize MongoDB document for JSON response"""
    if not doc:
        return doc
    if isinstance(doc.get('created_at'), datetime):
        doc['created_at'] = doc['created_at'].isoformat()
    if isinstance(doc.get('updated_at'), datetime):
        doc['updated_at'] = doc['updated_at'].isoformat()
    return doc

def _ean13_check_digit(twelve: str) -> str:
    """12 haneli taban için EAN-13 (mod-10) kontrol hanesi."""
    total = 0
    for i, ch in enumerate(twelve):
        d = ord(ch) - 48
        total += d if (i % 2 == 0) else d * 3
    return str((10 - (total % 10)) % 10)


async def build_used_barcode_set() -> set:
    """Tum urun ve varyant barkodlarini (legacy dahil) tek seferde toplar."""
    used = set()
    async for p in db.products.find({}, {"_id": 0, "barcode": 1, "variants": 1}):
        for var in (p.get("variants") or []):
            bc = str(var.get("barcode", "") or "")
            if bc:
                used.add(bc)
        pbc = str(p.get("barcode", "") or "")
        if pbc:
            used.add(pbc)
    return used


async def generate_barcode_from_range(used_barcodes_set=None) -> str:
    """GS1 onekine gore BENZERSIZ, gecerli EAN-13 (GTIN-13) barkod uretir.

    settings.gs1_prefix (varsayilan '8683851', 7 hane) + sirali urun referansi
    (kalan haneler; 7 hane onek => 5 hane = 100.000 barkod) + EAN-13 kontrol hanesi.
    Atomik sirali sayac (db.counters._id='gs1_item_ref') kullanir ve kullanilan/
    legacy barkodlari atlar. Boylece hicbir barkod cakismaz; her cagri (her varyant)
    farkli barkod alir.
    """
    from pymongo import ReturnDocument
    settings = await db.settings.find_one({"id": "main"}, {"_id": 0}) or {}
    prefix = str(settings.get("gs1_prefix") or "8683851").strip()
    if not prefix.isdigit() or len(prefix) >= 12:
        return None
    ref_len = 12 - len(prefix)          # 7 hane onek => 5
    max_ref = 10 ** ref_len             # 100000

    if used_barcodes_set is None:
        used_barcodes_set = await build_used_barcode_set()

    for _ in range(max_ref + 1):
        c = await db.counters.find_one_and_update(
            {"_id": "gs1_item_ref"},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        ref = int((c or {}).get("seq", 1)) - 1      # 0'dan basla (00000)
        if ref >= max_ref:
            logger.error("GS1 barkod kapasitesi (%s) doldu - yeni onek/blok gerekli." % max_ref)
            return None
        base = prefix + str(ref).zfill(ref_len)     # 12 hane
        barcode = base + _ean13_check_digit(base)   # 13 hane GTIN-13
        if barcode in used_barcodes_set:
            continue                                # legacy/kullanilmis -> atla
        used_barcodes_set.add(barcode)
        return barcode
    return None


async def generate_urun_karti_id() -> str:
    """Yeni urun icin Urun Kart ID uretir: sistemdeki EN BUYUK sayisal
    Urun Kart ID + 1. Hic yoksa settings.urun_karti_start (varsayilan 1000).
    Manuel ayni no varsa bir sonraki bos numaraya ilerler.
    """
    settings = await db.settings.find_one({"id": "main"}, {"_id": 0}) or {}
    try:
        start = int(settings.get("urun_karti_start") or 1000)
    except Exception:
        start = 1000
    mx = 0
    async for p in db.products.find({"urun_karti_id": {"$nin": [None, ""]}}, {"_id": 0, "urun_karti_id": 1}):
        v = str(p.get("urun_karti_id") or "").strip()
        if v.isdigit():
            iv = int(v)
            if iv > mx:
                mx = iv
    nxt = max(mx + 1, start)
    for _ in range(100000):
        cand = str(nxt)
        ex = await db.products.find_one({"urun_karti_id": cand}, {"_id": 1})
        if not ex:
            return cand
        nxt += 1
    return str(nxt)


async def build_used_urun_id_set() -> set:
    """Sistemdeki tum varyant urun_id (ve urun-seviyesi urun_id) degerlerini toplar."""
    used = set()
    async for p in db.products.find({}, {"_id": 0, "urun_id": 1, "variants": 1}):
        pu = str(p.get("urun_id") or "").strip()
        if pu.isdigit():
            used.add(pu)
        for v in (p.get("variants") or []):
            vu = str(v.get("urun_id") or "").strip()
            if vu.isdigit():
                used.add(vu)
    return used


async def urun_id_owner_map(exclude_product_id: str = "") -> dict:
    """{beden urun_id: ürün id} — başka ürünün 4 haneli beden ID'sini kopyalayan kaydı yakalamak için."""
    owners = {}
    async for p in db.products.find({}, {"_id": 0, "id": 1, "variants.urun_id": 1}):
        if exclude_product_id and p.get("id") == exclude_product_id:
            continue
        for v in (p.get("variants") or []):
            u = str(v.get("urun_id") or "").strip()
            if u.isdigit():
                owners.setdefault(u, p.get("id"))
    return owners


def normalize_variant_ids(variants: list, used_uid_set: set, other_owners: dict) -> int:
    """Beden kimliği KURALI: her bedenin urun_id'si 4 haneli/sayısal ve ürünler arası TEKİL,
    ve bedenin iç id'si = urun_id (Meta katalog/piksel content_id, feed g:id bununla aynı).
    - urun_id boş ya da BAŞKA ürüne ait (kopyalanmış ürün) → yeni sıradaki urun_id.
    - Aynı payload içinde tekrar eden urun_id → ikincisine yeni id.
    - id ≠ urun_id ise id = urun_id (YALNIZ barkodu olan bedende: stok/iade barkodla çözülür;
      barkodsuz bedenin eski siparişleri id ile geri alındığından o bedenin id'sine dokunulmaz).
    Değişen beden sayısını döndürür. variants yerinde güncellenir."""
    changed = 0
    seen = set()
    for v in variants or []:
        uid = str(v.get("urun_id") or "").strip()
        if (not uid.isdigit()) or (uid in (other_owners or {})) or (uid in seen):
            uid = next_urun_id(used_uid_set)
            v["urun_id"] = uid
            changed += 1
        seen.add(uid)
        if str(v.get("barcode") or "").strip() and str(v.get("id") or "").strip() != uid:
            v["id"] = uid
            changed += 1
    return changed


def next_urun_id(used_set) -> str:
    """Sistemdeki EN BUYUK sayisal urun_id + 1; kullanilmis degerleri atlar.
    used_set yerinde guncellenir; ardisik her cagri bir sonraki BOS id'yi dondurur
    (her beden icin +1 ilerler).
    """
    if used_set is None:
        used_set = set()
    mx = 0
    for u in used_set:
        s = str(u).strip()
        if s.isdigit():
            iv = int(s)
            if iv > mx:
                mx = iv
    nid = mx + 1
    while str(nid) in used_set:
        nid += 1
    used_set.add(str(nid))
    return str(nid)


# =============================================================================
# Şifre Politikası (Amazon DPP uyumu — personel/admin hesapları)
# Amazon, Amazon verisine erişen personel için min 12 karakter + karmaşıklık ister.
# Müşteri (storefront) hesaplarına UYGULANMAZ; mevcut login akışını bozmaz.
# =============================================================================
import re as _re_pw


def validate_strong_password(password: str, identifiers=None) -> None:
    """Personel/admin şifresi için güç doğrulaması. Zayıfsa HTTPException(400) atar.
    identifiers: ad/soyad/username/e-posta gibi değerler → şifre bunların bir parçasını
    İÇEREMEZ (Amazon DPP). Geriye-uyumlu: verilmezse yalnız karmaşıklık kontrol edilir."""
    pw = password or ""
    errors = []
    if len(pw) < 12:
        errors.append("en az 12 karakter")
    if not _re_pw.search(r"[A-ZÇĞİÖŞÜ]", pw):
        errors.append("en az 1 büyük harf")
    if not _re_pw.search(r"[a-zçğıöşü]", pw):
        errors.append("en az 1 küçük harf")
    if not _re_pw.search(r"[0-9]", pw):
        errors.append("en az 1 rakam")
    if not _re_pw.search(r"[^A-Za-z0-9]", pw):
        errors.append("en az 1 özel karakter")
    # Ad/soyad/username/e-posta parçası içerememe (≥4 karakterlik parçalar).
    _pw_low = pw.lower()
    for ident in (identifiers or []):
        s = str(ident or "").strip().lower()
        if not s:
            continue
        # e-posta ise yerel kısmı da ayrı kontrol et
        parts = [s] + ([s.split("@", 1)[0]] if "@" in s else [])
        # ad-soyad boşlukla ayrılırsa her kelimeyi de kontrol et
        parts += [w for w in s.replace(".", " ").split() if len(w) >= 4]
        for p in parts:
            if len(p) >= 4 and p in _pw_low:
                errors.append("ad/soyad/e-posta bilginizin bir parçasını içeremez")
                break
        else:
            continue
        break
    if errors:
        raise HTTPException(
            status_code=400,
            detail="Personel şifresi şu kuralları sağlamalı: " + ", ".join(errors) + ".",
        )


def _search_tr_regex(s: str) -> str:
    """Serbest metin arama için Türkçe duyarsız, regex-güvenli desen.
    MongoDB $options:'i' Türkçe İ↔i / ı↔I eşlemesini yapmadığından her Türkçe
    harf ailesini kapsayan bir karakter sınıfına çeviririz (ör. 'büstiyer',
    'Büstiyer' ve 'BÜSTİYER' hepsi aynı sonucu verir); diğer karakterler
    re.escape ile kaçırılır (kullanıcı +, (, . girince regex bozulmaz).

    2026-07-01 temizlik: integrations_common.py / orders.py / products.py
    içinde birebir aynı mantıkla üç kez kopyalanmıştı, buraya (deps.py —
    zaten tüm route modüllerinin import ettiği ortak yer) taşındı.
    """
    cls = {
        'i': '[iıİI]', 'ı': '[iıİI]', 'İ': '[iıİI]', 'I': '[iıİI]',
        'o': '[oöÖO]', 'ö': '[oöÖO]', 'O': '[oöÖO]', 'Ö': '[oöÖO]',
        'u': '[uüÜU]', 'ü': '[uüÜU]', 'U': '[uüÜU]', 'Ü': '[uüÜU]',
        's': '[sşŞS]', 'ş': '[sşŞS]', 'S': '[sşŞS]', 'Ş': '[sşŞS]',
        'c': '[cçÇC]', 'ç': '[cçÇC]', 'C': '[cçÇC]', 'Ç': '[cçÇC]',
        'g': '[gğĞG]', 'ğ': '[gğĞG]', 'G': '[gğĞG]', 'Ğ': '[gğĞG]',
    }
    # Dayanıklılık: bazı istemci/ara katman 'İ'yi toLowerCase ile 'i̇' (i + U+0307
    # combining dot) olarak gönderebilir → DB'deki 'Büstiyer' ile eşleşmezdi.
    # NFC normalize + combining dot temizliği ile ham (U+0130) ya da ayrışmış gelişten bağımsız eşleşir.
    import unicodedata as _ud
    _s = _ud.normalize('NFC', (s or '').strip()).replace('̇', '')
    # Dayanıklılık: DB'de bazı adlar NFD (AYRIŞMIŞ) saklanmış olabilir — ör. 'ü' = 'u'+U+0308
    # (birleşik iki nokta). Bu durumda düz [uüÜU] sınıfı yalnız 'u'yu tüketir, ardındaki U+0308
    # takılır ve eşleşme kırılır ("Büstiyer" bulunamaz). Her karakterden sonra opsiyonel birleşik
    # aksan (U+0300–U+036F) eşleyerek hem NFC hem NFD saklanmış veriyi yakalarız.
    _comb = r'[̀-ͯ]*'
    return ''.join(cls.get(ch, re.escape(ch)) + _comb for ch in _s)


# =============================================================================
# Tarih aralığı — TÜRKİYE yerel günü → UTC ISO sınırı (GLOBAL, tek kaynak)
# =============================================================================
# Sorun: created_at UTC saklanır; "07.05–08.05" gibi bir aralık ham verilince
#   (a) bitiş günü ("...T14:30") string olarak "2026-05-08"den büyük çıkıp dışlanır,
#   (b) UTC sınırı TR (+03:00) ile 3 saat kayar → önceki/sonraki günün verisi karışır.
# Çözüm: seçilen tarihi TR yerel GÜN sınırı (00:00 / 23:59:59.999999 +03:00) kabul edip
#   UTC'ye çevirmek. created_at .isoformat() (mikro-saniyeli, +00:00) ile string-karşılaştırma
#   bu sınırlarda doğru çalışır. Tam ISO gelirse (T içeren) olduğu gibi bırakılır.
_TR_TZ = timezone(timedelta(hours=3))


def _tr_naive_to_utc(s):
    """Saat içeren ama saat dilimi İÇERMEYEN tarih ('YYYY-MM-DDTHH:MM[:SS]') TR yerel
    saati kabul edilip UTC ISO'ya çevrilir. (DENETİM HATA-7: bu tarihler UTC sanılıyordu →
    7 günlük pencere sessizce 7g3s'e uzuyor, satış hızı ~%1,8 düşük çıkıyordu.)"""
    try:
        if ("T" in s or " " in s) and ("+" not in s) and ("Z" not in s.upper()):
            dt = datetime.fromisoformat(s.replace(" ", "T"))
            if dt.tzinfo is None:
                return dt.replace(tzinfo=_TR_TZ).astimezone(timezone.utc).isoformat()
    except Exception:
        pass
    return s


def tr_day_start_utc(d):
    """'YYYY-MM-DD' (TR gün başı 00:00) → UTC ISO. Saatli-tz'siz tarih TR saati kabul edilir;
    tam ISO (tz'li) / boş ise dokunmadan döndürür."""
    s = str(d or "").strip()
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        try:
            y, m, dd = int(s[0:4]), int(s[5:7]), int(s[8:10])
            return datetime(y, m, dd, 0, 0, 0, 0, tzinfo=_TR_TZ).astimezone(timezone.utc).isoformat()
        except Exception:
            return s
    return _tr_naive_to_utc(s)


def tr_day_end_utc(d):
    """'YYYY-MM-DD' (TR gün sonu 23:59:59.999999) → UTC ISO. Saatli-tz'siz tarih TR saati
    kabul edilir; tam ISO (tz'li) / boş ise dokunmaz."""
    s = str(d or "").strip()
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        try:
            y, m, dd = int(s[0:4]), int(s[5:7]), int(s[8:10])
            return datetime(y, m, dd, 23, 59, 59, 999999, tzinfo=_TR_TZ).astimezone(timezone.utc).isoformat()
        except Exception:
            return s
    return _tr_naive_to_utc(s)


def tr_range_to_utc(start, end):
    """(start, end) tarih aralığını TR yerel günü kabul edip (start_utc, end_utc) döndürür."""
    return tr_day_start_utc(start), tr_day_end_utc(end)


def instance_tag() -> str:
    """Bu süreci tanımlayan etiket (hangi deployment/replica çalışıyor): stok gönderim loglarına
    yazılır → api.meluni.com.tr dışında ikinci bir senkron çalışıyorsa /health'te ayrı etiketle görünür."""
    import os as _os, socket as _socket
    parts = [
        _os.environ.get("RAILWAY_SERVICE_NAME") or "",
        _os.environ.get("RAILWAY_ENVIRONMENT_NAME") or "",
        (_os.environ.get("RAILWAY_DEPLOYMENT_ID") or "")[:8],
        (_os.environ.get("RAILWAY_REPLICA_ID") or "")[:8],
        (_os.environ.get("RAILWAY_GIT_COMMIT_SHA") or "")[:7],
    ]
    tag = "/".join(x for x in parts if x)
    if not tag:
        try:
            tag = "host:" + _socket.gethostname()[:24]
        except Exception:
            tag = "unknown"
    return tag
