# Facette — Veri Koruma Politikası (Data Protection Policy)

_Amazon Selling Partner API — Data Protection Policy (DPP) uyumu için._
Son güncelleme: 2026-06-04

## 1. Kapsam
Bu politika, Facette'in Amazon SP-API üzerinden eriştiği tüm **Amazon Bilgileri**
(sipariş, alıcı PII'si dahil) ile site/pazaryeri siparişlerindeki kişisel verilerin
işlenmesi, saklanması, korunması ve imhasını kapsar.

## 2. Veri Sınıflandırma
- **PII (Kişisel Tanımlanabilir Bilgi):** ad-soyad, telefon, e-posta, adres.
- **İşlemsel veri:** sipariş no, tutar, ürün, durum (kişisel olmayan).
- **Gizli kimlik bilgileri:** API token, client secret, şifreler.

## 3. Saklama ve İmha (Retention)
- Amazon kaynaklı siparişlerdeki PII, **sipariş gönderiminden (shipment) sonra 30 gün**
  içinde otomatik olarak anonimleştirilir (`pii_retention_purge` zamanlanmış görevi,
  her gün 03:00 UTC).
- İşlemsel veriler muhasebe/yasal yükümlülük için saklanır; kişisel tanımlayıcılar silinir.
- Manuel imha: `POST /api/compliance/pii-retention/run`.

## 4. Şifreleme
- **İletimde (in transit):** Tüm trafik HTTPS/TLS üzerinden.
- **Durağan (at rest):** Hassas kimlik bilgileri AES (Fernet/AES-128) ile şifreli vault'ta;
  MongoDB bağlantısı şifreli.

## 5. Erişim Kontrolü
- Rol bazlı erişim (RBAC): yalnızca yetkili admin/personel PII'ye erişir.
- Personel şifreleri: min 12 karakter + büyük/küçük harf + rakam + özel karakter.
- En az ayrıcalık (least privilege) ilkesi.

## 6. Denetim (Audit)
- `audit_logs` koleksiyonu: kimlik doğrulama, vault erişimi, uyum olayları.
- En az 12 ay saklanır; periyodik (≤2 hafta) gözden geçirilir.

## 7. Kimlik Bilgisi Yönetimi
- Token/secret asla koda gömülmez, public depoya konmaz, paylaşılmaz.
- `secrets_vault` (AES) + ortam değişkenleri kullanılır.
