# Amazon SP-API — Güvenlik Runbook (Incident Response + Credential Rotation)

İlk faz (PII'siz) için teknik olay-müdahale ve kimlik-bilgisi yönetimi prosedürü.
Amazon DPP §11 (Incident Response) ve §4/§11 (Credential Rotation) karşılığı.

> Sorumlu teknik kişi: **<doldurulacak — teknik owner>** · Yedek: **<doldurulacak>**

---

## 1. Incident Response — Teknik Checklist

Bir güvenlik olayı (şüpheli erişim, secret sızıntısı, anormal SP-API kullanımı) şüphesinde
sırasıyla:

- [ ] **Etkilenen hesabı devre dışı bırak** — `users` koleksiyonunda `is_active=False`.
      (Kod: `deps.py:get_current_user` inactive kullanıcının token'ını reddeder.)
- [ ] **Kullanıcı session'larını sonlandır** — parolayı sıfırla/değiştir → `_token_revoked`
      eski JWT'leri geçersizler (`deps.py`). Gerekirse `JWT_SECRET` rotasyonu (tüm oturumları düşürür).
- [ ] **SP-API token'ını iptal et** — Admin > Amazon > config'ten `refresh_token`'ı sil/yenile
      (`amazon_spapi.py::spapi_save_config` access-token cache'ini sıfırlar). Amazon tarafında
      Seller Central > Apps & Services'ten uygulama yetkisini geri çek.
- [ ] **Credential rotation** — aşağıdaki §2 prosedürü.
- [ ] **Etkilenen servisi izole et** — Railway'de ilgili servisi durdur/rollback; gerekirse
      dış erişimi (Cloudflare) kısıtla.
- [ ] **Logları koru** — `db.integration_logs`, `db.audit_logs`, Railway deployment/app logları
      ve Atlas audit log'unu olay penceresi için dışa aktar/dondur (silme/rotasyonu durdur).
- [ ] **Root-cause için log topla** — SP-API çağrı logları (path/status/actor), auth başarısız
      girişleri (`register_failed_login`), vault reveal audit (`write_audit_log`).
- [ ] **Şüpheli export tespiti** — anormal toplu okuma/indirme, olağandışı admin işlemleri.
- [ ] **Olay zaman çizelgesi** oluştur (ilk belirti → tespit → müdahale → kapanış).
- [ ] **Düzeltme sonrası doğrulama** — güvenlik taraması (CodeQL/gitleaks/dep) + etkilenen
      akışın retest'i; kapanış notu.

### Olayda toplanacak log listesi
- `integration_logs` (SP-API çağrıları — PII'siz)
- `audit_logs` (secret reveal/değişiklik, yetki değişiklikleri)
- Auth: başarısız/başarılı giriş, lockout kayıtları
- Railway: app + deployment logları
- MongoDB Atlas: database audit log (etkin ise)

---

## 2. Credential Rotation Prosedürü

### 2.1 SP-API (LWA) client secret / refresh token
1. Amazon Developer Central / Seller Central'da yeni `client_secret` üret veya uygulama
   yetkisini yeniden ver (yeni `refresh_token`).
2. Admin > Amazon > Config'e yeni değerleri gir → `spapi_save_config` bunları **Fernet ile
   şifreleyip** (`client_secret_enc`, `refresh_token_enc`) saklar ve access-token cache'ini sıfırlar.
3. Eski token'ı Amazon tarafında geçersiz kıl.
4. `spapi_test` ile doğrula.

### 2.2 Uygulama master encryption key (`SECRETS_MASTER_KEY`)
1. Yeni 32-byte base64url key üret.
2. Mevcut şifreli satırları **`security/crypto.py::rotate_secret()`** ile yeni key'e re-encrypt et.
3. Env'i güncelle, servisleri yeniden başlat, doğrula.
> Not: `crypto.py` primitive'i **Fernet (AES-128-CBC + HMAC-SHA256)** — AES-256-GCM değildir.

### 2.3 Diğer secret'lar (DB, e-fatura/kargo entegratör, JWT)
- DB parolası: Atlas'ta rotate → Railway env güncelle → restart.
- `JWT_SECRET`: rotate (tüm oturumları düşürür — planlı yap).
- Entegratör credential'ları: ilgili panelde rotate → vault'ta güncelle.

---

## 3. Session Revocation Yöntemleri
- Tek kullanıcı: parola değişimi/sıfırlama → `_token_revoked` eski token'ı reddeder.
- Toplu: `JWT_SECRET` rotasyonu → tüm JWT'ler anında geçersiz.
- Hesap kilidi: `is_active=False` → token kabul edilmez + login engellenir.

## 4. Service Isolation
- Railway'de etkilenen servisi durdur / önceki sağlıklı deploy'a rollback.
- Cloudflare'de gerekirse WAF/rate-limit ile erişimi daralt.
- SP-API entegrasyonunu geçici kapat: `AMAZON_ALLOW_RESTRICTED=0` zaten kapalı; gerekirse
  config'i temizleyerek çağrıları durdur.

---

## 5. İlk Faz PII/Restricted Guard (kodda aktif)
- `AMAZON_ALLOW_RESTRICTED=0` (varsayılan) iken PII/RDT gerektiren yollar 403 ile **engellenir**
  (`amazon_spapi.py::_assert_restricted_allowed`).
- Yanıtta beklenmedik PII gelirse **saklanmadan/loglanmadan sökülür**
  (`amazon_spapi.py::_scrub_pii`). Restricted Role onaylanınca flag açılır.
