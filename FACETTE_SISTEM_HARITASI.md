# FACETTE SİSTEM HARİTASI

> Amaç: "bir dosyayı silince/yeniden adlandırınca başka yerde import kalıp build'i kırma" sınıfı
> hataları bitirmek. Bir şeyi silmeden/taşımadan önce burayı + repo genelinde importer
> grep'ini kontrol et.
>
> Son güncelleme: 2026-06-14 · Bu doküman repo kökünde durur ve her yapısal değişiklikte güncellenir.

---

## 0. Mimari Özet

| Katman | Teknoloji | Konum |
|---|---|---|
| Backend | FastAPI (Python) | Railway · `api.facette.com.tr` · `backend/server.py` |
| Frontend | React + CRACO | Cloudflare Pages · `facette.com.tr` · `frontend/src/` |
| DB | MongoDB Atlas | — |
| Repo | github.com/kdrgry1-oss/facette (main) | — |

**Tüm API yolları `/api` öneki altında** (`api_router` → `server.py`).
Frontend backend adresi: `process.env.REACT_APP_BACKEND_URL` (+ `/api`).

---

## 1. Frontend Yönlendirme (Routing)

İki ayrı router var:
- **Storefront** → `frontend/src/App.js`
- **Admin panel** → `frontend/src/AdminApp.jsx` (App.js'te `/admin/*` buraya devreder)

### 1.1 Storefront (`App.js`)

| Yol | Açıklama |
|---|---|
| `/` | Ana sayfa |
| `/kategori/:slug` | Kategori listeleme |
| `/urun/:slug` | Ürün detay |
| `/sepet` · `/odeme` | Sepet · Ödeme (Iyzico) |
| `/arama` | Arama |
| `/hesabim` · `/giris` · `/sifremi-unuttum` · `/sifre-sifirla` | Müşteri hesabı |
| `/siparis-takip` · `/siparis-takip/:trackingCode` | Sipariş takip |
| `/order-success/:orderNumber` · `/siparis-tamamlandi/:orderNumber` | Sipariş başarı |
| `/odeme-bildirimi/:orderNumber` | Havale bildirimi |
| `/iade/:orderNumber` | **Müşteri iade talebi** (storefront tarafı) |
| `/sayfa/:slug` · `/:slug` | CMS sayfaları |
| `/admin/*` | → `AdminApp.jsx` |

### 1.2 Admin (`AdminApp.jsx`) — yol → sayfa bileşeni

> Hepsi `frontend/src/pages/admin/` altında. 74 sayfa import edilir.

| Yol (`/admin/…`) | Bileşen | Başlıca backend ucu |
|---|---|---|
| `urunler`, `urunler/:productId` | AdminProducts | `/api/products*` |
| `siparisler`, `odeme-bekleyen-siparisler` | AdminOrders | `/api/orders*` |
| `kategoriler` | AdminCategories | `/api/categories*` |
| `varyantlar` | AdminVariants | `/api/variants*` |
| `xml-feedler` | XmlFeeds | `/api/admin/xml-feeds*` |
| `ticimax-excel` | TicimaxExcelUpload | (tek seferlik içe aktarım) |
| `sorular` | AdminQuestions | `/api/integrations/trendyol/qna*` |
| `sayfa-tasarimi` | AdminPageDesign | `/api/admin/page-design*` |
| `bannerlar` | AdminBanners | `/api/banners*` |
| `temalar` | Themes | `/api/themes*` |
| `kampanyalar` | AdminCampaigns | `/api/campaigns*`, `/api/coupons*` |
| `entegrasyonlar` | AdminIntegrations | `/api/integrations*` |
| `odeme-tipleri` | Payments | `/api/payment*` |
| **`iadeler`** | **AdminReturns** (= `Returns.jsx`) | iade akışı — bkz. **§4** |
| `iptaller` | AdminCancellations | `/api/orders*` (iptal durumları) |
| `silinen-siparisler` | DeletedOrders | `/api/orders*` |
| `telefonla-siparis` | TelefonSiparis | `/api/orders*` |
| `sayfalar` | AdminPages | `/api/cms*` |
| `ayarlar` | AdminSettings | `/api/settings*` |
| `ayarlar/menu-duzeni` | MenuSettings | `/api/settings*` |
| `ayarlar/e-fatura` | EInvoiceSettings | `/api/integrations/dogan*` |
| `ayarlar/kargo` | CargoSettings | `/api/settings*` |
| `ayarlar/gonderici-adresi` | SenderAddress | `/api/settings*` |
| `ayarlar/bildirim`, `…/sablonlar` | NotificationSettings, NotificationTemplates | `/api/settings*` |
| `ayarlar/eposta` | EmailSettings | `/api/settings*` (ZeptoMail) |
| `ayarlar/pixel` | MarketingPixels | `/api/marketing-pixels*`, CAPI |
| `ayarlar/capi-loglar` | CapiLogs | `/api/capi*` |
| `ayarlar/sosyal-giris` | SocialAuthSettings | `/api/integrations/social*` |
| `ayarlar/siparis-durumlari` | OrderStatusSettings | `/api/settings*` |
| `bloklu-musteriler` | BlockedCustomers | `/api/customer*` |
| `uretim-plani`, `imalat` | ProductionPlan, Manufacturing | `/api/manufacturing*` |
| `influencer` | Influencers | `/api/influencers*` |
| `amazon` | AmazonSpApi | `/api/integrations/amazon*` |
| `dpp-uyum` | Compliance | `/api/compliance*` |
| `raporlar/iade-ve-trend` | ReportsAdvanced | `/api/reports*` |
| `raporlar/satis` `…/urun` `…/stok` `…/uye` | SalesReport, ProductsReport, StockReport, MembersReport | `/api/reports*` |
| `pazaryerleri` | MarketplaceHub | `/api/admin/marketplace-hub*` |
| `entegrasyon-loglari` | IntegrationLogs | `/api/integrations*` |
| `aktarilamayanlar` | FailedTransfers | `/api/integrations*` |
| `marka-eslestir`, `kategori-eslestir` | BrandMapping, CategoryMapping | `/api/admin/brand-mapping*`, `…/category-mapping*` |
| `toplu-fiyat-stok` | BulkPriceStock | `/api/products*` |
| `stok-uyarilari` | StockAlerts2 | `/api/stock-notify*` |
| `musteri-segmentleri` | CustomerSegments | `/api/members*` |
| `otomasyon` | AutomationStatus | `/api/admin/automation-status*` |
| `guvenlik-paneli` | SecurityDashboard | `/api/admin/security*` |
| `sistem-sagligi` | SystemHealth | `/api/admin/system-health*` |
| `secrets-vault` | SecretsVault | `/api/admin/secrets*` |
| `iys` | IysAdmin | `/api/integrations/iys*` |
| `mobil-uygulama` | MobileApp | `/api/admin/mobile*` |
| `ai-asistan` | AIAssistant | `/api/ai-assistant*` |
| `footer-tasarim` | FooterDesign | `/api/footer*` |
| `pazaryeri-karlilik` | MarketplaceProfit | `/api/reports*` |
| `trendyol-loglar` | TrendyolLogs | `/api/integrations*` |
| `barkod-sorunlari` | BarcodeIssues | `/api/integrations*` |
| `trendyol-hayalet` | TrendyolGhostScanner | `/api/integrations*` |
| `urun-ozellikleri` | ProductAttributes | `/api/attributes*` |
| `cariler` | Vendors | `/api/vendors*` |
| `kullanicilar` | AdminUsersRoles | `/api/admin/rbac*` |
| `uyeler` | Members | `/api/members*` |
| `kaynak` | Attribution | `/api/attribution*` |
| `olcu-tablolari` | SizeTablesList | `/api/admin/size-tables*` |
| `kuponlar` | Coupons | `/api/coupons*` |
| `yorumlar` | ProductReviews | `/api/reviews*` |
| `terkedilmis-sepet` | AbandonedCarts | `/api/cart*` |
| `seo/meta`, `seo/yonlendirmeler` | SeoMeta, SeoRedirects | `/api/seo*` |
| `markalar` | Brands | `/api/products/brands*` |
| `login` | AdminLogin | `/api/auth*` |
| `iade-edilenler`, `trendyol-eslestir`, `hepsiburada-eslestir`, `temu-eslestir` | `<Navigate>` (yönlendirme — sayfa yok) | — |

**Ortak admin kabuğu:** `pages/admin/AdminLayout.jsx` (menü + içerik çerçevesi).
**Global onay diyaloğu:** `components/admin/AppConfirm.jsx` → `appConfirm()` (native `confirm` yerine).

---

## 2. Backend Router Envanteri

`backend/server.py` içinde **72 router'ın tamamı** `api_router.include_router(...)` ile bağlı —
**yetim (bağlanmamış) router yok**. Tüm uçlar `/api` önekli.

Başlıca gruplar:

- **Katalog/satış:** `products`, `categories`, `variants`, `attributes`, `orders`, `cart`, `coupons`, `campaigns`, `members`, `customer`, `vendors`
- **İçerik:** `cms`, `banners`, `themes`, `footer_template`, `seo`, `size_tables`
- **Ödeme:** `payment`, `integrations_iyzico`
- **E-fatura:** `integrations_dogan` (Doğan e-Arşiv/e-Fatura UBL)
- **Pazaryeri:** `integrations` (Trendyol), `integrations_temu`, `amazon_spapi`, `integrations_trendyol_qna`, `marketplace_hub`, `brand_mapping`, `category_mapping`, `trendyol_retry_queue`
- **İade/operasyon:** `orders` (iade uçları), `ticimax_returns` (iade listesi) — bkz. **§4**
- **Pazarlama/ölçüm:** `capi` (Meta CAPI), `marketing_pixels`, `attribution`, `reports`, `reports_v2`, `analytics_extra`
- **Sistem/güvenlik:** `auth`, `admin`, `admin_rbac`, `mfa`, `secrets_vault`, `security_dashboard`, `system_health`, `provider_settings`, `settings`, `notifications`, `webhooks`, `upload`, `upload_files`
- **Üretim:** `manufacturing`, `production_plan`, `production_hooks`
- **Diğer:** `ai_chatbot`, `ai_assistant`, `stock_notify`, `locations`, `iys_integration`, `inbound_mail`, `docs`, `compliance`, `influencers`, `automation_status`, `barcode_cards`
- **Ticimax (çıkış aşamasında):** `ticimax_stock_sync`, `ticimax_category_sync`, `ticimax_member_sync`, `ticimax_product_pull`, `ticimax_returns` — bkz. **§6**

---

## 3. Kimlik Doğrulama / Kaynak Kuralları

- Admin uçları: `Depends(require_admin)` (`routes/deps.py`).
- **Sipariş kaynağı = sipariş NUMARASI ÖNEKİ** (paneldeki "Sipariş Kaynağı" etiketi DEĞİL):
  `TY…` = Trendyol · `HB…` = Hepsiburada · önek yok = **Site**.

---

## 4. İADE AKIŞI (kritik — en sık dokunulan ve bizi yakan yer)

### 4.1 Bileşen zinciri

```
/admin/iadeler
  └─ Returns.jsx  (AdminReturns — platform sekmeli kapsayıcı)
       ├─ "Web Sitesi" sekmesi  →  <TicimaxReturns embedded />   ← gerçek operasyonel liste
       └─ "Trendyol" sekmesi     →  (Trendyol iade görünümü)
```

- `Returns.jsx`: sadece platform sekme kabuğu. (Eski "Sipariş İadeleri / Site İade Talepleri"
  alt-pilleri **kaldırıldı** — Web Sitesi doğrudan `TicimaxReturns`'ü gömülü açar.)
- **`TicimaxReturns.jsx`** ("Sipariş İadeleri" listesi): veriyi `orders` koleksiyonundan
  `GET /api/admin/ticimax/return-orders` ile çeker. Günlük kullanılan ~1444 satırlık liste burası.

> ⚠️ NOT: "Ticimax" adı tarihsel; bu sayfa **kendi DB'mizdeki sipariş tabanlı iadeleri** gösterir,
> Ticimax'tan veri ÇEKMEZ. Çıkış planında bu router/isimler "Site/Web" olarak yeniden adlandırılacak (§6).

### 4.2 6 Aşamalı sekme yapısı (`RETURN_TABS` — TicimaxReturns.jsx)

| # | Sekme | `status` değer(ler)i |
|---|---|---|
| 1 | Talep Oluşturulan | `return_requested` (varsayılan) |
| 2 | İade Kargoda | `return_in_transit` |
| 3 | Teslim Alındı | `returned` |
| 4 | Onaylananlar | `return_approved` |
| 5 | İade Ödemeleri | `refunded` + `partial_refunded` (ikisi birden) |
| 6 | Reddedilenler | `return_rejected` |

- "Kısmi İade Yapıldı" (`partial_refunded`) ayrı sekme değil — manuel durum dropdown'ında (STATUS_OPTS) seçenek.
- Liste 3 tarih sütunu gösterir: **Sipariş Tarihi** (`created_at`) · **İade Onay** (`return_approved_at`) · **İade Ödeme** (`refund_paid_at`).
- Satır genişletince: ürün kalemleri **tiklenebilir** (`selItems`, `toggleItem`) + iade kargo barkodu / `return_code` / takip no / `reship_code` detayda.

### 4.3 İşlem akışı modalı (`openWorkflow`)

`openWorkflow(row)` →
1. **Köprü:** `POST /api/admin/ticimax/returns/{order_id}/open` → `customer_returns` kaydını
   `order_id` üzerinden bulur/bağlar, `return_id` döner.
2. **Önizleme:** `GET /api/orders/returns/{return_id}/refund-preview?fault=customer`
3. Modal: Onayla / Reddet / Gider Pusulası / Bedeli Öde / Yeniden Gönder.

### 4.4 Backend uçları

**`backend/routes/ticimax_returns.py`** (iade listesi):
| Satır | Uç | İş |
|---|---|---|
| 49 | `GET /return-orders` | Liste; `status` çoklu (virgüllü → `$in`); `status_counts` filtreden bağımsız; `customer_returns` tek batch join (return_code, barcode_url, reship…) |
| 222 | `POST /orders/refresh-dates` | Geçmiş kayıtlara tarih damgası tamiri |
| 291 | `GET /return-orders/export` | Excel/CSV dışa aktarım (aynı çoklu-status) |
| 393 | `POST /returns/{order_id}/open` | **Köprü** → customer_returns'e bağlar, `return_id` döner |

**`backend/routes/orders.py`** (iade yaşam döngüsü — hepsi `{return_id}` üzerinden):
| Satır | Uç | Fonksiyon | Tetiklediği durum/etki |
|---|---|---|---|
| 694 | `PUT /{order_id}/status` | `update_order_status` | Manuel dropdown; duruma göre `return_approved_at`/`refund_paid_at` damgalar |
| 3083 | `POST /{order_id}/return-request` | — | Müşteri iade talebi oluşturur (storefront `/iade/:orderNumber`) |
| 3204 | `GET /{order_id}/return` | — | Talep detayını getirir |
| 3221 | `GET /returns/{return_id}/barcode.png` | — | İade kargo barkodu görseli |
| 3248 | `GET /returns/admin/list` | — | customer_returns admin listesi (ham) |
| 3273 | `POST /returns/{return_id}/status` | `update_return_status` | customer_returns durum geçişi (+ tarih damgaları) |
| 3406 | `GET /returns/{return_id}/refund-preview` | — | İade tutarı önizleme (`fault=customer/seller`) |
| 3418 | `POST /returns/{return_id}/approve` | `approve_return` | order → `return_approved`, `return_approved_at` damgalar |
| 3555 | `POST /returns/{return_id}/reissue-barcode` | — | Barkodu yeniden üret |
| 3596 | `POST /returns/{return_id}/reject` | `reject_return` | order → `return_rejected` |
| 3679 | `POST /returns/{return_id}/gider-pusulasi` | — | Gider pusulası kaydı (numara serisi) |
| 3762 | `POST /returns/{return_id}/refund-pay` | `refund_pay_return` | order → `refunded`/`partial_refunded`, `refund_payment{by,at,method,amount,reference}` + `refund_paid_at` |
| 3835 | `POST /returns/{return_id}/reship` | `reship_return` | Yeniden gönderim (`reship_code`, `reshipped_at`) |

**Tarih damgası gerçeği:** Geçmiş ~1432 "Bedeli Ödendi" siparişinin İade Onay tarihi yok ("—"),
İade Ödeme `updated_at`'e düşer. Sadece deploy SONRASI yapılan geçişler birebir doğrudur.

---

## 5. Entegrasyonlar

| Entegrasyon | Router | Not |
|---|---|---|
| **Iyzico** (ödeme) | `integrations_iyzico` + `payment` | Kart + havale; CAPI Purchase her iki başarı yolunda |
| **Trendyol** | `integrations`, `integrations_trendyol_qna`, `trendyol_retry_queue` | Sipariş/ürün/soru-cevap |
| **Doğan e-Arşiv/e-Fatura** | `integrations_dogan` | UBL; `cbc:Note` formatı `Renk:{c};Beden:{s}:Barcode:{b}` |
| **Meta CAPI** | `capi` (+ `services/capi/purchase.py`) | event_id = `purchase_<order_number>` (dedup) |
| **ZeptoMail** | `email_smtp.py` / `settings` | HTTPS API (api.zeptomail.eu); from = `info@facette.com.tr` |
| **Amazon** | `amazon_spapi` | SP-API |
| **Temu** | `integrations_temu` | — |
| **IYS** | `iys_integration` | İzin yönetimi |

---

## 6. Ticimax Çıkış Durumu

Ticimax paneli yalnızca **geçici referans + tek seferlik geçmiş-veri taşıma** kaynağı.
Hedef: sıfır senkron, kod/UI/DB'de "Ticimax/SOAP/WSDL" referansı kalmaması; `TicimaxWeb` kaynağı
→ "Site/Web". Veri taşıma bitene kadar **temizlik yapılmaz**.

Hâlâ duran Ticimax router'ları (taşıma bitince kaldırılacak/yeniden adlandırılacak):
`ticimax_stock_sync`, `ticimax_category_sync`, `ticimax_member_sync`, `ticimax_product_pull`,
`ticimax_returns` (→ bunun "Site iade listesi" olarak yeniden adlandırılması planlı; **kod olarak Ticimax'a bağımlı değil**).

Yönetici doküman: `TICIMAX_CIKIS_PLANI.md`.

---

## 7. Bilinen Yetimler (KASITLI — silinmeyecek)

`frontend/src/components/ui/*` — shadcn/ui kütüphane parçaları (accordion, alert, card, table…).
31 tanesi şu an hiçbir yerden import edilmiyor ama bu bir **kütüphane kiti**, mükerrer değil.
İleride kullanılabilir; **silme**.

---

## 8. HATA ÖNLEME — bir dosyayı silmeden/taşımadan ÖNCE

Bu dokümanın varlık sebebi: `SiteReturns.jsx` silindiğinde `Returns.jsx`'teki import temizlendi
ama `AdminApp.jsx`'teki import+route gözden kaçtı → Cloudflare build kırıldı
(`Can't resolve './pages/admin/SiteReturns'`). esbuild yalnız **sözdizimi** kontrol eder,
**import çözümlemesi yapmaz** — bu boşluk hatayı yakalamadı.

**Kural (her yapısal değişiklikte):**
1. Silmeden/yeniden adlandırmadan önce repo genelinde importer ara:
   ```
   grep -rn "DosyaAdi" frontend/src --include=*.jsx --include=*.js
   ```
   (yorum satırları zararsız; gerçek `import`/`<Route>` satırlarını temizle.)
2. Paketlemeden önce **import çözümleme denetçisini** çalıştır (sadece esbuild yetmez):
   ```
   cd frontend && python3 /tmp/audit.py
   # BROKEN: 0 olmalı. ORPHANS yalnız ui/* olmalı.
   ```
   Denetçi: tüm `import/require/lazy` spec'lerini (yorumları ayıklayarak) çözer; yerelde
   bulunamayan = build kırar; hiç import edilmeyen = yetim.
3. CRA build `CI=true` ile çalışır → kullanılmayan değişken/import **ve** çözülemeyen göreli
   import = **HATA**. Silinen importer'ı temizlerken kullanılmayan kalan değişkenleri de temizle.

---

## 9. Bu Oturumda Yapılan Temizlik (changelog)

**Silinen (mükerrer/eski/ölü):**
- `frontend/src/pages/admin/SiteReturns.jsx` — yeni açılan boş 6-aşama sayfası; gerçek hedef
  `TicimaxReturns.jsx` olduğu için mükerrerdi.
- `frontend/src/components/admin/GiderPusulasiPrint.jsx` — yalnız SiteReturns kullanıyordu (hiç deploy olmamıştı).
- `frontend/src/layouts/AdminLayout.jsx` — yetim + kırık import (`../../context/AuthContext` yok);
  gerçek olan `pages/admin/AdminLayout.jsx`.
- `frontend/src/pages/admin/ReturnedOrders.jsx` — tam yetim, eski çok-platformlu iade sayfası
  (iade akışının eski sürümü).
- `frontend/src/components/admin/AddressFields.jsx` — tam yetim (silinen SiteReturns kullanıyordu).

**Düzeltilen import (build kırıyordu):**
- `AdminApp.jsx` — `SiteReturns` import'u + `site-iadeler` route'u kaldırıldı.
- `Returns.jsx` — `SiteReturns` import'u kaldırıldı; Web Sitesi doğrudan `<TicimaxReturns embedded/>`.


**İade detay paneli yeniden düzenlendi (TicimaxReturns.jsx genişletilmiş satır):**
- Silinen alanlar: Telefon, E-posta, Ödeme, Ham ödeme, Ödenen, Kaynak No, gri "Sipariş iade sürecinde" satırı.
- Kalan + eklenen: Müşteri · Adres · Durum · Sipariş tutarı · **Fatura** (her zaman, boşsa "—", order.invoice_number'dan) · **İade Onay** tarihi · **İade Ödeme** tarihi.
- Ürün satırı: fiyat/adet/toplam/barkod bloğu sola taşındı; toplam dökümü sola yaslandı.
- Tüm metinler siyah (gri/silik kaldırıldı).
- Alt buton ikiye ayrıldı: **İade Onay** (yeşil) + **İade Reddet** (kırmızı). İkisi de işlem modalını (tutar+gider+öde) onay/ret modunda açar; kalem seçiliyse iade tutarı seçili kalemlerin toplamından ön-dolar.

**Sonuç:** import denetçisi → BROKEN: 0, ORPHANS: yalnız 31 shadcn `ui/*` (kasıtlı).
