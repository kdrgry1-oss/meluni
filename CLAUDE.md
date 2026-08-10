# Facette — Geliştirme Kuralları (ZORUNLU)

Bu depo canlı bir e-ticaret sistemidir (FastAPI + MongoDB backend / Railway,
React CRA frontend / Cloudflare Pages). Gerçek para, gerçek siparişler.

## ⛔ ÖDEME & SİPARİŞ-STATÜSÜ — EN KRİTİK ALAN (çok dikkat)

Bu alanda hatalar defalarca tekrarlandı ve para/veri bütünlüğünü bozdu.
**Aşağıdaki değişmezleri (invariants) ASLA bozma. Bu dosyalara dokunan HER
değişiklikten önce bu bölümü oku, sonunda testleri çalıştır.**

### Değişmezler (invariants)
1. **Bir sipariş yalnız iyzico ödemeyi GERÇEKTEN onayladıysa `payment_status="paid"`
   ve `status="confirmed"` olur.** Tek yetkili yol: `payment.py::_mark_order_from_payment`
   → `_is_paid(data)` **VE** `_payment_matches_order(data, order)` (tutar+sipariş eşleşmesi).
   Başka hiçbir yerde bir siparişi "paid"/"confirmed" yapma. İstemci girdisine ASLA güvenme.
   **Tek belgeli istisna (C2):** `orders.py::create_order` içinde tutarın TAMAMI hediye
   çekiyle karşılanırsa (`gift_cards.redeem_gift_card_for_order` bakiyeyi ATOMİK düşer,
   sunucu hesaplar — istemciye güvenilmez) sipariş `paid/confirmed` +
   `paid_with_gift_card_only=True` açılır. İptalde bakiye `refund_gift_card_once` ile
   idempotent iade edilir (`_restock_order_once` kancası). Bu yolu genişletme.
2. **Ödeme gerektiren sipariş (kart + havale/EFT) oluşturulurken `status="awaiting_payment"`
   (Ödeme Bekleniyor) başlar** (`orders.py::create_order`). Kart siparişi "pending"
   BAŞLATILMAZ — 3DS yarıda kalırsa panelde normal/onaylanabilir görünür (yasak).
3. **Kart siparişinde müşteri "Siparişiniz Alındı" maili ve admin "yeni sipariş" push'u
   OLUŞTURMADA GÖNDERİLMEZ**; yalnız ödeme onaylanınca (`_notify_paid_order_confirmed`) gider.
   Havale bank-detay maili YALNIZ havale/EFT'ye gider (karta gitmez).
4. **Ödeme başarısızsa/yarıda kalırsa** sipariş `awaiting_payment` (veya `failed`) kalır ve
   `scheduler.py::auto_cancel_unpaid_card_orders` (3 saat) süpürür → iptal + stok iadesi.
   iyzico `paymentId` döndüyse (kart çekilmiş olabilir) `needs_reconciliation=True` +
   `reconcile_charged_but_unrecorded_orders` cron'u kurtarır. Bunları bozma.
5. **Kupon/redemption yalnız ödeme onayından SONRA** yazılır (kart) — `record_order_redemptions`
   `_notify_paid_order_confirmed` içinden çağrılır. Başarısız ödemede kupon YANMAZ.
6. **Stok** sipariş insert'inde atomik-koşullu düşülür (A2.5b oversell), iptalde iade edilir.
   Statü değişiklikleri stok mantığını değiştirmemeli.

### Dokunulan dosyalar (kritik — sözleşmeleri koru)
- `routes/orders.py`: `create_order` (statü ataması ~993, insert öncesi oversell düşümü,
  bildirim/push gating), order-list junk/awaiting süzgeci.
- `routes/payment.py`: `_mark_order_from_payment`, `_is_paid`, `_payment_matches_order`,
  `initialize_3ds_payment`, `callback_3ds`, `card_pay_non3ds`, `_notify_paid_order_confirmed`.
- `scheduler.py`: `auto_cancel_unpaid_card_orders`, `reconcile_charged_but_unrecorded_orders`.

### Bu alana dokunduğunda ZORUNLU kontrol listesi
- [ ] Değişmezler 1–6 hâlâ geçerli mi? (özellikle "yalnız gerçek ödeme = paid/confirmed")
- [ ] `python -m py_compile` ile derle.
- [ ] Deploy sonrası **canlı test**: kart siparişi oluştur → `status=awaiting_payment`,
      `payment_status!=paid` olduğunu doğrula (test siparişini SİL + stoğu geri yükle).
- [ ] `scratchpad/smoke.sh` çalıştır (site/admin/giriş/ödeme/promo/anasayfa).
- [ ] Şüpheliyse tüm ödeme yaşam-döngüsünü tarayan denetim ajanı çalıştır.

## Genel kurallar
- **Deploy:** `git push origin HEAD:main HEAD:claude/facette-repo-connect-s07sw3`
  (Railway backend ~30-90s, Cloudflare frontend ~2-4dk).
- **Her deploy sonrası smoke:** `bash <scratchpad>/smoke.sh` — site/admin/giriş/ödeme
  bozulmamalı. Smoke'a promo motoru + anasayfa slider-feed kontrolleri eklidir (sessiz
  500'leri yakalar).
- **Frontend build:** `cd frontend && CI=true npx craco build` (Cloudflare CI=true ile
  build eder; uyarılar hata sayılır — yerelde CI=true ile doğrula). `npm install` YAPMA.
- **Beyaz etiket:** firma-özel her değer `company.py` / İşletme Kuralları'ndan gelmeli
  (koddan değil). Yeni firma yalnız ayarlardan geçirilebilmeli.
- **İşletme Kuralları** (`business_rules.py`): eşik/toggle'lar admin-düzenlenebilir; yeni
  sabit eklerken katalog + (gerekiyorsa) `_PUBLIC_KEYS`'e ekle.
