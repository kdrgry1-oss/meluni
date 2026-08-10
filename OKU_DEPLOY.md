# Site İade (kargo + ödenen-tutar düzeltmesi) + Taksit Vade Farkı (kümülatif)

İKİ iş tek pakette. Tek deploy ile ikisi de canlıya çıkar.

---

## A) İADE TUTARI = ÖDENEN TUTAR + KARGO "MÜŞTERİDEN KES"  (bu tur — kritik düzeltme)

**Sorun (W10039):** Müşteri **1.881** TL ödedi; İade Onay **1.900** hesaplıyordu (= ürün brütü 1.990 − kargo 90; **kupon indirimi 199 yok sayılıyordu**). Ayrıca kargo tam iadede tiklenemiyordu.

**Kök sebep:** İade hesabı ürünlerin **brüt** toplamını baz alıyordu; müşterinin **gerçekte ödediği** tutarı (order.total / taksitliyse paidPrice) değil.

**Düzeltme:**
- **İade bazı = ödenen tutar.** Tam iadede `_compute_refund_breakdown` ve GP, `order.total`'ı (taksitliyse vade farkı dahil `paidPrice`) baz alır. Artık kupon indirimi de doğru.
- **Kargo toggle tek anlam: "Kargoyu müşteriden kes (−₺X)".** Kusur müşterideyse (bana uymadı vb.) işaretle → iadeden kargo düşülür. **Tam iadede de tiklenebilir.**
  - Kes **kapalı** (mağaza kusuru): net = ödenen (örn. **1.881**).
  - Kes **açık** (müşteri kusuru): net = ödenen − kargo (örn. **1.791**).
- **Onay penceresindeki Kusur (müşteri/mağaza) seçimi** kargoyu otomatik belirler: müşteri → kargo düşülür, mağaza → düşülmez.
- Kesilen kargo ayrı satır olarak yazılmaz; fark **indirim** toplamına katlanır ki pusuladaki satırlar net tutarla tutarlı kalsın. ("Net Tutar" totals'tan gelir.)

**Önceki "+kargo iadeye ekle / −müşteriye yansıt" ikili mantığı kaldırıldı** (kafa karıştırıcıydı ve tam iadede yanlış topluyordu).

---

## B) TAKSİT VADE FARKI — FATURA + İADE  (önceki tur — bu pakette dahil)

**Mevzuat (KDV 24/c):** Vade farkını mağaza uygular ve `paidPrice`=taksit toplamını mağaza tahsil eder → fark matraha dahil, %20.

- Taksitli siparişte faturaya **"Vade Farkı (Taksit xN)"** satırı (KDV %20 dahil = paidPrice − total); fatura toplamı gerçek tahsilata eşitlenir. Hem e-Arşiv hem e-Fatura. Satır adı = İBARE.
- Tam iadede net, gerçekte ödenen (vade farkı dahil) tutar; GP'de vade farkı satırı görünür.

---

## Dosyalar
- backend/routes/orders.py — `_compute_refund_breakdown` (baz=ödenen), GP endpoint (kargo=kes, baz=ödenen), taksit vade farkı (helper + e-Arşiv & e-Fatura satırı).
- frontend/src/pages/admin/RooftrReturns.jsx — kargo satırı tek anlam "müşteriden kes", tam iadede de tiklenebilir.
- frontend/src/pages/admin/Returns.jsx — pusula satır tutarı işaret-korur.
- backend/routes/rooftr_returns.py — yanıta free_ship_fee + GP-no projeksiyonu.
- backend/routes/integrations.py — Trendyol manuel iade köprüsü.
- backend/scripts/recompute_site_gp.py — geçmiş GP yeniden-hesaplama (DRY-RUN varsayılan).

## Deploy
    cd ~/Downloads/facette_deploy
    unzip -o ~/Downloads/facette_iade_vade.zip
    git add -A
    git commit -m "Iade tutari=odenen tutar + kargo musteriden kes; taksit vade farki fatura/iade"
    git push
- Railway yeşil: [scheduler] Background scheduler started
- Cloudflare Pages: 1-2 dk build + Cmd+Shift+R

## Test — W10039 (ödenen 1.881, kargo 90, indirim 199)
1. Web Sitesi → W10039 aç → **İade Onay**.
   - Kusur **Müşteri** (varsayılan): Otomatik iade **1.791** (1.881 − 90 kargo).
   - Kusur **Mağaza**: **1.881** (kargo düşülmez).
2. Kargo satırı artık **tiklenebilir** (tam iadede de). "Kargoyu müşteriden kes −₺90,00".
3. Belge ikonu → GP:
   - Kargo **işaretsiz** → Net **1.881,00**.
   - Kargo **işaretli** → Net **1.791,00**.

## Test — Taksit (W10129)
1. Taksitli siparişin faturasını kes → **"Vade Farkı (Taksit xN)"** satırı; toplam = ödenen (peşin değil).
2. Tam iade → GP net = ödenen (vade farkı dahil).

## Geçmiş GP'leri düzeltme (DİKKAT)
Railway shell, backend/:
    python -m scripts.recompute_site_gp          # rapor
    python -m scripts.recompute_site_gp --apply  # tam iade GP'lerini ödenen tutara çeker (yedekli)
