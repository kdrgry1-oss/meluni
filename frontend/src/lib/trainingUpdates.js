/**
 * Sürümlü sistem yardım ekleri.
 *
 * Bu kayıtlar mevcut firmanın düzenlediği yardım metinlerini değiştirmez. Aynı
 * section/item anahtarı daha önce kaydedilmişse kayıtlı sürüm korunur; yalnız
 * eksik sistem maddeleri eklenir. Böylece yeni sürüm dokümantasyonu upsert
 * edilirken tenant'a özel içerik kaybolmaz.
 */
export const TRAINING_CONTENT_VERSION = "2026.09.09.2";

export const TRAINING_UPDATES = [
  {
    key: "platform-rehberi-v2",
    title: "Platform Ayarları ve Güvenli İşlemler",
    icon: "Settings",
    intro: "Beyaz etiket ayarları, birleşik ekranlar, rapor tanımları ve salt-okunur kontroller için güncel başvuru.",
    items: [
      {
        key: "full-look-mobile-2026-09-09",
        title: "Full Look mobil görünümü",
        path: "/admin/sayfa-tasarimi?tab=full-look",
        what: "Mobilde kombin fotoğrafı tam genişlikte, parçalar altında iki sütunlu kartlarla gösterilir. Fotoğraf kırpılmaz; Parçaları keşfet bağlantısı ilgili ürün grubuna kaydırır.",
        where: "Full Look düzenleyici → Mobil önizleme; müşteride Koleksiyonlar → Full Look.",
        how: [
          "Kaynak üründen seçilen aynı fotoğraf mobilde de kullanılır. Alt ve üstü birlikte gösteren bir fotoğraf seçin; yakın plan bir fotoğrafı sistem kendiliğinden tam boy fotoğrafa dönüştürmez.",
          "İki ürün tek sıra, dört ürün iki sıra halinde görünür. Kartın tamamına dokunmak ürün detayını açar; fiyatlar katalogdan gelir.",
          "Mobil önizleme düğmesi yalnız görünümü değiştirir. Ürün/kombin sırası masaüstüyle ortaktır; Yayında ve Kaydet kontrolleri her iki görünümü birlikte yönetir.",
        ],
      },
      {
        key: "full-look-editor-2026-09-09",
        title: "Full Look: kombin fotoğrafı ve ürünlerini birlikte yayınlama",
        path: "/admin/sayfa-tasarimi?tab=full-look",
        what: "Full Look sayfasında büyük kombin fotoğrafının yanında o görseldeki ürünler güncel fiyatlarıyla listelenir. Mobilde fotoğraf üstte, ürünler altta görünür.",
        where: "Tasarım → Sayfa Tasarımı → Full Look Sayfası Düzenleme. Sayfa Tasarımı yetkisi gerekir.",
        how: [
          "Kombin Ekle'ye basın. Solda kaynak ürünü adı veya stok koduyla arayın; galerisindeki hangi fotoğrafı kullanacağınızı seçin. Fotoğraf kırpılmadan gösterilir.",
          "Sağdaki aramayla görselde bulunan alt, üst ve aksesuarları ekleyin. En fazla 8 ürün seçilebilir; oklarla ürünleri ve kombinleri sıralayın.",
          "Mobil/Masaüstü önizlemede görünümü kontrol edin. Yayında seçeneği kapalı kombinler taslak olarak saklanır. Değişiklikleri Kaydet'e basmadan hiçbir değişiklik müşteriye yansımaz.",
          "Koleksiyonlar → Full Look veya /full-look üzerinden yayını kontrol edin. Kartlar ürün detayına gider; beden seçimi ve sepete ekleme normal ürün sayfasındadır.",
          "Fiyatlar elle girilmez: mevcut ürün fiyatları ve geçerli ürün kampanyaları kullanılır. Kupon ve havale indirimleri sepet aşamasına aittir. Pasif/silinmiş/üyeye özel ürünler herkese açık görünümde listelenmez; kaynak fotoğrafı kaldırılan kombin gizlenir.",
          "Başka bir kullanıcı aynı sayfayı kaydettiyse sürüm çakışması uyarısı çıkar. Taslağınız otomatik silinmez; yaptığınız değişiklikleri not alıp sayfayı yeniden yükleyerek güncel sürüme uygulayın.",
        ],
      },
      {
        key: "promotion-calculation-order-2026-09-08",
        title: "Kampanya ve kupon indirim sırası",
        path: "/admin/kampanyalar",
        what: "Birleşen otomatik ürün kampanyaları önce, ilk sipariş/girilen kupon sonra, havale indirimi kalan tutara uygulanır. Üst ve alt ödeme özeti aynı kampanya motorunun tutarlarını gösterir.",
        where: "Pazarlama → Kampanyalar; havale oranı Genel Ayarlar → Ödeme ayarları.",
        how: [
          "3.790 TL örneği: %20 lansman 758 TL; kalan 3.032 TL üzerinden %10 hoş geldin 303,20 TL; kalan 2.728,80 TL üzerinden %5 havale 136,44 TL. Net ürün tutarı 2.592,36 TL; 99 TL kargoyla toplam 2.691,36 TL.",
          "Birleşebilirlik ve kampanya grubu kuralları korunur. Girilen kod birleşmeyen otomatik kampanyalara karşı seçim önceliğini korur; bu öncelik hesaplama sırasıyla aynı şey değildir.",
          "Kampanya oranları, kapsamı ve aşama içindeki öncelik panelden değiştirilebilir. Ürünün indirimli satış fiyatı varsa liste fiyatıyla arasındaki fark 'Ürün fiyat indirimi' olarak ayrıca gösterilir.",
        ],
      },
      {
        key: "net-shipping-and-registration-2026-09-08",
        title: "İndirim sonrası ücretsiz kargo ve zorunlu ad-soyad",
        path: "/admin/kampanyalar",
        what: "Ücretsiz kargo eşiği kampanya/kupon, ödeme yöntemi ve kullanılan puan indirimlerinden sonraki ürün tutarıyla karşılaştırılır; eşiğe eşit tutar dahildir.",
        where: "Pazarlama → Kampanyalar → Ücretsiz Kargo → Min. Sipariş Tutarı.",
        how: [
          "Eşik 4.000 TL ise son ürün tutarı 3.999,99 TL olduğunda kargo ücretli; 4.000,00 TL ve üzerinde ücretsizdir. Eşik kodda sabit değildir, kampanya ayarından gelir.",
          "Kargo, hediye paketi ve kapıda ödeme hizmet bedeli eşiğe eklenmez. Hediye çeki/mağaza kredisi bir ödeme aracıdır; ücretsiz kargo tabanını azaltmaz.",
          "Sepet göstergesi ön tahmindir. Son kargo kararı, ödeme yöntemi ve indirimler seçildikten sonra ödeme ekranında kesinleşir.",
          "E-posta/şifreyle yeni üyelikte ad ve soyad zorunludur; boş veya yalnız boşluk içeren isimler sunucu tarafından da reddedilir. Üye Ol formunda 'veya' ayracı kaldırılmıştır.",
        ],
      },
      {
        key: "security-hardening-2026-09-08",
        title: "Personel yetkileri, gizli anahtarlar ve adres gizliliği",
        path: "/admin/ayarlar?tab=users-roles",
        what: "Rol atanmamış personelin örtük yetkilerini kaldırır; e-posta anahtarlarının şifreleme başarısızlığında kaydedilmesini engeller.",
        where: "Ayarlar → Kullanıcılar & Roller; Pazarlama → E-posta Pazarlama → Sağlayıcı Ayarları.",
        how: [
          "Süper yöneticiyle Kullanıcılar & Roller'i açıp her personele yalnız gereken izinleri içeren bir rol ata. Rolü olmayan personel yönetim API'sine erişemez.",
          "Bir e-posta adresi tek başına süper yönetici hakkı vermez. Süper yönetici erişimi açık hesap işareti veya atanmış yetkili rolle tanımlanır.",
          "E-posta sağlayıcı ayarlarını okumak, değiştirmek ve bağlantıyı doğrulamak settings.emails izni ister. Kampanya gönderimi tasarim.email izniyle ayrıdır.",
          "Şifreleme hatasında yeni anahtarlar ve sağlayıcı ayarları kaydedilmez. Sunucunun şifreleme yapılandırması düzeltilmeden tekrar deneme; eski anahtarı korumak için maskeli alanı değiştirme.",
          "Engellenen adresler listesi okunamazsa gönderim durur. Sonuç doğrulanmalı durumunda sağlayıcı kayıtlarını incelemeden yeniden gönderme; SES'e kör yeniden deneme yapılmaz.",
        ],
        tips: [
          "Adresler ortak tarayıcının localStorage alanında otomatik saklanmaz. Üyenin kayıtlı adresleri yalnız kendi oturumuyla yüklenir; misafir adresi tekrar girilir.",
          "Fatura, etiket, barkod kartı ve müşteri raporu yazdırmada oturum anahtarı URL'ye eklenmez. Eski token içeren yazdırma bağlantıları yerine paneldeki güncel Yazdır düğmesini kullanın.",
          "Git geçmişindeki olası anahtar sızıntıları ayrıca sağlayıcıda iptal/yenileme ister. Kodun güncellenmesi geçmiş anahtarları otomatik geçersiz kılmaz.",
        ],
      },
      {
        key: "tenant-setting-impact-matrix",
        title: "Firma ayarları: alan → ekran / etki matrisi",
        path: "/admin/ayarlar?tab=general",
        what: "Yeni bir firma için marka, şirket, iletişim, alan adı, ticaret, kargo ve katalog varsayılanlarını kod değiştirmeden yönetir.",
        where: "Ayarlar → Mağaza → Genel Ayarlar. SEO metin şablonları ayrıca SEO → Meta Yönetimi'ndedir.",
        how: [
          "Site Adı ve Logo URL alanlarını doldur; bunlar canonical marka kimliğini ve bu kimliği kullanan bildirim/SEO çıktılarını besler.",
          "Mağaza Adresi/Web Sitesi alanına storefront kök adresini gir; canonical bağlantı, sitemap, robots, llms.txt ve ürün feed bağlantıları bu alanı kullanır.",
          "Firma ünvanı, adres, il, vergi dairesi/no ve IBAN'ı Şirket Bilgileri'nde yönet; firma e-postası, telefon, WhatsApp ve sosyal hesapları aynı kartta güncelle.",
          "Para birimi kodu/sembolü ile varsayılan KDV'yi Ticaret alanında; kargo firması ve firma bazlı ücretleri Kargo Ücretleri alanında değiştir.",
          "Varsayılan Ürün Markası yalnız sonradan oluşturulan ürünlerin başlangıç değeridir; var olan ürünleri topluca değiştirmez.",
          "Kaydet'ten sonra yeni ayarlar canonical tenant_config kaydına yazılır; eski tüketiciler için uyumluluk alanları da geçici olarak güncellenir.",
        ],
        tips: [
          "Para birimi, KDV ve varsayılan marka değişikliği geçmiş siparişleri veya mevcut ürünleri geriye dönük dönüştürmez.",
          "Şirket ayarını değiştirmek KVKK, mesafeli satış veya diğer CMS/yasal sayfaların metnini otomatik olarak yeniden yazmaz; bu sayfaları Tasarım → Sayfalar bölümünde ayrıca inceleyin.",
          "API anahtarı ve parolayı bu forma yazmayın; yalnız Secrets Vault veya ilgili entegrasyonun gizli alanını kullanın.",
        ],
      },
      {
        key: "settings-workspace-and-legacy-routes",
        title: "Birleşik Ayarlar çalışma alanı ve eski bağlantılar",
        path: "/admin/ayarlar",
        what: "Ayarları Mağaza, Sipariş & Finans, Teslimat, İletişim, Görünüm & Panel, Pazarlama & İzleme ve Ekip & Tedarik gruplarında tek sayfada toplar.",
        where: "Ayarlar menüsü; masaüstünde soldaki grup listesi, dar ekranda Ayar bölümü seçicisi.",
        how: [
          "Genel Ayarlar için /admin/ayarlar adresini aç; diğer bölümlere soldaki listeden geç.",
          "Eski yer imleri yeni sekmeye otomatik gider: işletme kuralları → business-rules, e-fatura → einvoice, kargo → cargo, gönderici adresi → sender-address.",
          "Bildirim, e-posta, şablon, özel tema, menü, pixel, CAPI, sosyal giriş ve sipariş durumu eski yolları da karşılık gelen ?tab= adresine yönlenir.",
          "Döviz, kullanıcılar ve cariler eski üst seviye yollarından sırasıyla currency, users-roles ve vendors sekmelerine yönlenir.",
        ],
        tips: [
          "Yönlendirmeler veri taşımaz veya silmez; yalnız aynı bileşeni birleşik çalışma alanında açar.",
          "Bir sekmeye doğrudan bağlantı vermek için /admin/ayarlar?tab=SEKME biçimini kullanın.",
        ],
      },
      {
        key: "automatic-seo-geo",
        title: "Otomatik ürün ve kategori SEO / GEO",
        path: "/admin/seo/meta",
        what: "Global şablonlardan ürün/kategori title, description, canonical, Open Graph, robots ve yapılandırılmış veri üretir; llms.txt ve firma şeması kayıtlı gerçek alanlardan beslenir.",
        where: "SEO → Meta Yönetimi → Global SEO / GEO Şablonları ve Ürün / Kategori Toplu Önizleme.",
        how: [
          "Global başlık/açıklama, ürün ve kategori şablonları, Organization ve llms.txt açıklaması, OG görseli ve robots varsayılanını düzenleyip Kaydet'e bas.",
          "Şablonlarda yalnız gösterilen tokenları kullan; eksik token değeri boş kalır ve sistem ürün özelliği uydurmaz.",
          "Ürünler, Kategoriler veya Tümü kapsamını seçip Dry-run Önizle'ye bas; bu işlem kayıt yazmadan üretilecek başlık, açıklama ve kaynaklarını gösterir.",
          "Tekil ürün/kategori meta alanı varsa otomatik şablondan önce gelir; Manuel URL Override ise en yüksek önceliktedir.",
          "Ürün sayfasında Product, gerçek fiyat varsa Offer ve BreadcrumbList; kategori sayfasında CollectionPage ve BreadcrumbList JSON-LD üretilir.",
        ],
        tips: [
          "Fiyat veya stok bilgisi yoksa Offer fiyatı ya da availability uydurulmaz.",
          "Dry-run hiçbir ürün/kategori kaydını değiştirmez. Ayrı bulk-apply servisi açık onay ifadesi ister ve yalnız boş meta alanlarını doldurur; panel önizleme sırasında bu servisi çağırmaz.",
          "Canonical alan adı Ayarlar → Genel Ayarlar → Web Sitesi/Mağaza Adresi değerinden gelir.",
        ],
      },
      {
        key: "amazon-single-workspace",
        title: "Amazon tek ekran",
        path: "/admin/amazon",
        what: "Amazon bağlantısı, aktarım/eşleştirme ve DPP uyum kontrollerini üç sekmeli tek çalışma alanında toplar.",
        where: "Entegrasyonlar → Amazon.",
        how: [
          "SP-API & Bağlantı sekmesinde hesap bağlantısını ve entegrasyon durumunu yönet.",
          "Aktarım & Eşleştirme sekmesinde Amazon TR için kategori/alan eşleştirmelerini incele.",
          "DPP Uyum sekmesinde ürün güvenliği ve uyum kontrollerini yönet.",
          "Eski /admin/amazon/sp-api bağlantısı connection; /amazon/aktarim ve /amazon/eslestirme mapping; /amazon/dpp ve /dpp-uyum compliance sekmesine gider.",
        ],
        tips: ["Eski yolların yönlenmesi bağlantı veya eşleştirme verisini yeniden oluşturmaz; aynı ekranın ilgili sekmesini açar."],
      },
      {
        key: "email-marketing-workspace",
        title: "Gelişmiş E-posta Pazarlama",
        path: "/admin/eposta-pazarlama",
        what: "Brevo Marketing birincil ve Amazon SES yedek gönderim sağlayıcısı; izinli hedef kitle, şablonlar, canlı önizleme, test ve kampanya geçmişini tek ekranda yönetir.",
        where: "Pazarlama → E-posta Pazarlama.",
        how: [
          "Brevo'da API anahtarı, doğrulanmış gönderici ve rızalı kişilerin tutulacağı liste kimliğini oluştur; Brevo'yu aktif ve birincil seçip Kaydet'e bas.",
          "Bağlantıyı doğrula ile API anahtarını gönderim yapmadan sınayabilir, Test gönder ile yalnız belirlediğin adrese gerçek deneme yapabilirsin.",
          "Brevo webhook adresini Brevo panelinde marketing unsubscribe, spam ve hard bounce olaylarına bağla; aynı güvenlik anahtarını iki tarafta kullan.",
          "Hazır şablonu önizle veya yeni kampanyada konu, içerik ve ürün bloklarını düzenle; canlı önizlemeyi kontrol et.",
          "Kampanya taslağını şablon olarak kaydedebilir veya doğrulanmış bir adrese Test olarak gönder ile sınayabilirsin.",
          "Hedef kitle ve ileti izinlerini kontrol ettikten sonra kampanyayı başlat; sistem Brevo listesini yerel rızalı kitleyle eşitler ve kampanyayı Brevo kuyruğuna yollar.",
          "Amazon SES ayarları Amazon SES yedek ayarları bölümünde saklanır; silinmez. Birincil sağlayıcı hazır değilse yedek kullanımını ayrıca açıp kapatabilirsin.",
          "Eski /admin/toplu-mail yer imi otomatik olarak bu ekrana yönlenir.",
        ],
        tips: [
          "Canlı önizleme ve şablon önizleme e-posta göndermez; Test gönder ve kampanya başlatma ayrı işlemlerdir.",
          "Brevo'ya iletildi durumu teslim edildi demek değildir; kesin teslim, açılma ve tıklama metriklerini Brevo kampanya raporundan kontrol edin.",
          "Ticari ileti gönderiminden önce izin/IYS durumunu kontrol edin; Brevo'da abonelikten çıkan kişi eşitleme sırasında yeniden aktifleştirilmez.",
        ],
      },
      {
        key: "report-metric-dictionary",
        title: "Rapor metrik sözlüğü ve formüller",
        path: "/admin/raporlar/urun",
        what: "Satış ve ürün raporlarında kullanılan adet, ciro, iade oranı, stok kapsama ve mutabakat ölçülerinin kanonik tanımlarıdır.",
        where: "Raporlar → Satış Raporları ve Ürün Raporları; aynı kapsam Excel dışa aktarımına da uygulanır.",
        how: [
          "Brüt Satış Adedi = Net Satış Adedi + İptal Adedi + Onaylanmış İade Adedi.",
          "Brüt Ciro = Net Ciro + İptal Tutarı + Onaylanmış İade Tutarı.",
          "Trendyol/kapsam iade yüzdesi = Onaylanmış İade Adedi / Brüt Satış Adedi × 100.",
          "Operasyonel iade yüzdesi (iptal hariç) = Onaylanmış İade Adedi / (Net Satış Adedi + Onaylanmış İade Adedi) × 100.",
          "Açık İade, henüz sonuçlanmamış kalem adedi/tutarıdır; Öngörülen Net Ciro = Net Ciro − Açık İade Tutarı.",
          "Haftalık satış hızı seçili dönemin net satış adedinden; stok kapsama haftası = Güncel Stok / Haftalık Satış Hızı formülünden gelir. Hız yoksa kapsama tahmin edilmez.",
          "Pazaryeri sipariş tarihinde marketplace_order_date esastır; bu alan yoksa created_at kullanılır. Aynı order_number kopyaları kanonik rapor satırında tekilleştirilir.",
        ],
        tips: [
          "Kısmi iptal/iade tüm sipariş yerine yalnız ilgili kalem adedi ve tutarıyla ayrıştırılır; siparişte kalan ürün net satışta kalır.",
          "Kaynak filtresi seçildiğinde adet/ciro/iptal/iade ve oran paydası yalnız o kanala daralır; güncel stok kanallar arasında ortaktır.",
          "Reddedilen iade gerçekleşmiş iade sayılmaz ve satış ciroda kalır.",
        ],
      },
      {
        key: "open-return-reconciliation",
        title: "Açık iade ve Trendyol mutabakatı",
        path: "/admin/raporlar/urun",
        what: "Açık ve onaylanmış iadeleri kalem durumuna göre ayırır; Trendyol ile panel sipariş/adet/tutar farklarını salt-okunur karşılaştırır.",
        where: "Ürün Raporları → Trendyol ile Mutabakat; satış raporunda Açık İade ve Öngörülen Net alanları.",
        how: [
          "Tarih aralığını ve gerekiyorsa platform filtresini seç; raporu yenile.",
          "Trendyol ile Mutabakat'a bas; uzun aralıklar ekran tarafından boşluksuz parçalara ayrılır ve her istek apply=false ile çalışır.",
          "Sipariş, ürün adedi ve tutar farkının yanında kopya belge, panelde eksik/fazla sipariş, iptal durum farkı ve kısmi iptal özetini incele.",
          "Açık pazaryeri claim kalemleri Created, WaitingInAction veya InAnalysis durumundadır; Accepted gerçekleşmiş iadeye, Rejected/Unresolved reddedilene ayrılır.",
        ],
        tips: [
          "Paneldeki Mutabakat düğmesi sipariş, ödeme veya stok kaydı değiştirmez.",
          "Bir fark bulmak düzeltmenin otomatik uygulandığı anlamına gelmez; önce sipariş numarası ve tarih kapsamını doğrulayın.",
        ],
      },
      {
        key: "stock-and-user-audit",
        title: "Stok geçmişi ve kullanıcı işlem geçmişi",
        path: "/admin/islem-gecmisi",
        what: "Stok değişimlerinin ürün/varyant zaman çizelgesini ve giriş, sipariş, stok, ayar/yönetim olaylarının birleşik salt-okunur görünümünü sağlar.",
        where: "Ürünler → ürünü düzenle → Stok Geçmişi; ayrıca Entegrasyonlar → İşlem Geçmişi.",
        how: [
          "Bir ürünü açıp Stok Geçmişi sekmesinde tarih, kullanıcı, kaynak, varyant/SKU, eski-yeni stok, fark ve senkron sonucunu incele.",
          "İşlem Geçmişi'nde kullanıcı, olay, kaynak, tarih ve başarı filtresini doldurup Uygula'ya bas.",
          "Satırın Maskelenmiş değişiklik bölümünden kaydedilmiş güvenli ayrıntıyı görüntüle; kaynak koleksiyon her satırda gösterilir.",
        ],
        tips: [
          "Bu ekranlar salt okunurdur; geçmiş kayıtları değiştirmez.",
          "Eski kayıtta kullanıcı, eski/yeni stok, başarı veya bağlam tutulmamışsa alan boş gösterilir; sistem geriye dönük tahmin üretmez.",
          "İşlem Geçmişi için audit.read yetkisi gerekir; yoğun kaynaklarda tarama sınırı uyarısı görünürse tarih aralığını daraltın.",
        ],
      },
    ],
  },
];

const identity = (value) => String(value || "").trim().toLocaleLowerCase("tr");

/** Preserve tenant-authored content and insert only missing versioned system records. */
export function mergeTrainingUpdates(sections, updates = TRAINING_UPDATES) {
  const merged = JSON.parse(JSON.stringify(Array.isArray(sections) ? sections : []));
  for (const incomingSection of updates) {
    let section = merged.find((candidate) => identity(candidate.key) === identity(incomingSection.key));
    if (!section) {
      merged.push(JSON.parse(JSON.stringify(incomingSection)));
      continue;
    }
    section.items = Array.isArray(section.items) ? section.items : [];
    for (const incomingItem of incomingSection.items || []) {
      const existing = section.items.find((candidate) =>
        (candidate.key && incomingItem.key && identity(candidate.key) === identity(incomingItem.key)) ||
        (!candidate.key && identity(candidate.title) === identity(incomingItem.title))
      );
      if (!existing) section.items.push(JSON.parse(JSON.stringify(incomingItem)));
    }
  }
  return merged;
}
