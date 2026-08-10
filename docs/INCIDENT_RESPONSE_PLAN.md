# Facette — Olay Müdahale Planı (Incident Response Plan)

_Amazon SP-API Data Protection Policy uyumu için._
Son güncelleme: 2026-06-04 · Gözden geçirme periyodu: **6 ayda bir**

## 1. Roller ve Sorumluluklar
- **Olay Sorumlusu (Incident Lead):** Teknik ekip yöneticisi — koordinasyon.
- **Güvenlik İrtibatı:** PII/güvenlik olaylarını değerlendirir, bildirimleri yapar.
- **Sistem Yöneticisi:** Erişim iptali, log toplama, sistem izolasyonu.

## 2. Tespit
- Audit log incelemesi (≤2 hafta), anormal erişim/uyarılar, hata izleme.

## 3. Sınıflandırma ve Önceliklendirme
- **Kritik:** PII sızıntısı / yetkisiz erişim → derhal müdahale.
- **Yüksek:** Potansiyel açık → 30 gün içinde giderme.

## 4. Bildirim (Notification)
- **Amazon Bilgilerini ilgilendiren güvenlik olayları tespit edildikten sonra 24 saat
  içinde `security@amazon.com` adresine bildirilir.**
- İlgili veri sahipleri ve yasal merciler gerektiğinde bilgilendirilir.

## 5. Müdahale ve Kurtarma
- Etkilenen kimlik bilgileri iptal/rotasyon (vault üzerinden token yenileme).
- Sistem yaması, erişim kısıtlama, kök neden analizi.
- Olay sonrası rapor + önleyici aksiyonlar.

## 6. Gözden Geçirme
- Plan **6 ayda bir** gözden geçirilir ve güncellenir.
- Her ciddi olay sonrası "lessons learned" dokümante edilir.
