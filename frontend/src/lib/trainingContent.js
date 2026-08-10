/**
 * Eğitim / Yardım paneli içeriği — tek kaynak.
 * Her bölüm bir yönetim menüsü grubuna karşılık gelir; her madde bir özelliği
 * "Ne / Nerede / Nasıl" formatında anlatır. TrainingPanel.jsx bunu okur.
 *
 * Yeni bir özellik eklediğinizde buraya da bir madde ekleyin — panel otomatik
 * olarak listeler ve aramaya dahil eder.
 *
 * item şeması:
 *   { title, path?, what, where, how: [adım...], tips?: [ipucu...] }
 */

export const TRAINING = [
  {
    key: "baslangic",
    title: "Başlangıç",
    icon: "LayoutDashboard",
    intro: "Panele ilk giriş, gösterge paneli ve genel kullanım.",
    items: [
      {
        title: "Yönetim paneline giriş",
        path: "/admin",
        what: "Mağazanı yönettiğin ana kontrol merkezi. Sol menüden tüm bölümlere ulaşırsın.",
        where: "Tarayıcıdan siteadresi/admin adresine git; e-posta ve şifrenle giriş yap.",
        how: [
          "Adres çubuğuna /admin yaz ve giriş ekranına ulaş.",
          "Yetkili e-posta ve şifreni gir.",
          "Sol taraftaki menüden çalışmak istediğin bölümü seç.",
        ],
        tips: [
          "Menü sırasını ve gizlenecek grupları Ayarlar → Menü Düzeni'nden kişiselleştirebilirsin.",
          "Her kullanıcının rolüne göre görebileceği bölümler değişir (Kullanıcılar & Roller).",
        ],
      },
      {
        title: "Gösterge paneli (Dashboard)",
        path: "/admin",
        what: "Günlük ciro, sipariş sayısı, bekleyen işler ve hızlı özet.",
        where: "Panele girişte açılan ilk ekran.",
        how: [
          "Üst karttan günün/ayın satış özetini gör.",
          "Bekleyen sipariş, iade ve soru sayılarına tıklayarak ilgili listeye geç.",
        ],
      },
    ],
  },
  {
    key: "siparisler",
    title: "Siparişler",
    icon: "ShoppingCart",
    intro: "Sipariş yönetimi, iade/iptal, havale bildirimleri ve ödeme takibi.",
    items: [
      {
        title: "Tüm Siparişler",
        path: "/admin/siparisler",
        what: "Site ve pazaryeri (Trendyol, Hepsiburada, Temu) siparişlerinin tamamı tek listede.",
        where: "Siparişler → Tüm Siparişler.",
        how: [
          "Üstteki durum sekmelerinden (Yeni, Onaylı, Kargoda, Teslim, İade...) filtrele.",
          "Sipariş numarası, müşteri adı veya telefonla ara.",
          "Bir siparişe tıklayıp detay/fatura/kargo/dosya sekmelerini aç.",
          "Sipariş onayla → fatura & kargo barkodu bu adımdan sonra üretilebilir.",
        ],
        tips: [
          "Fatura ve kargo barkodu yalnızca ONAYLANMIŞ siparişte oluşturulur.",
          "Fatura kesildiğinde link otomatik olarak Trendyol/Hepsiburada'ya iletilir.",
        ],
      },
      {
        title: "İadeler",
        path: "/admin/iadeler",
        what: "Müşteri iade taleplerini görüntüle, onayla/ret, kısmi iade ve kargo ücreti kararını ver.",
        where: "Siparişler → İadeler.",
        how: [
          "İade talebini aç; ürünleri ve nedeni incele.",
          "Tümünü ya da kısmi (seçili ürün) iadeyi onayla.",
          "Kısmi iadede kargo ücretinin iade edilip edilmeyeceğini seç.",
          "Onay sonrası iyzico iade tutarı işlenir ve stok geri alınır.",
        ],
        tips: [
          "Bedava kargolu siparişin kısmi iadesinde sistem gider pusulası gerekliliğini uyarır.",
          "İade penceresi (varsayılan 14 gün) Ayarlar → İşletme Kuralları'ndan değişir.",
        ],
      },
      {
        title: "İptaller",
        path: "/admin/iptaller",
        what: "İptal edilen siparişler; ödeme tipine göre filtreleme ve iptal ödemesi takibi.",
        where: "Siparişler → İptaller.",
        how: [
          "Ödeme tipine (kart, havale, kapıda) göre filtrele.",
          "Ödenmiş ve iptal edilen (mor) siparişte 'Ödemeli İptal Onaylandı' durumunu işaretle.",
        ],
        tips: ["Ödenip iptal edilen siparişin iptali muhasebe yetkisi ister."],
      },
      {
        title: "Havale/EFT Bildirimleri",
        path: "/admin/havale-bildirimleri",
        what: "Müşterilerin havale/EFT dekont bildirimleri; onaylayınca sipariş 'ödendi' olur.",
        where: "Siparişler → Havale/EFT Bildirimleri.",
        how: [
          "Bildirimi aç, dekontu ve tutarı kontrol et.",
          "Doğruysa onayla → sipariş ödemesi tamamlanır.",
        ],
        tips: ["Onaylanmayan havale siparişi belirlenen süre (varsayılan 72 saat) sonunda otomatik iptal edilir — süre İşletme Kuralları'nda."],
      },
      {
        title: "Ödeme Kaydı Bulunmayan Siparişler",
        path: "/admin/odeme-bekleyen-siparisler",
        what: "Ödemesi tamamlanmamış/başarısız siparişler.",
        where: "Siparişler → Ödeme Kaydı Bulunmayan.",
        how: ["Listeyi incele; gerekirse müşteriye ödeme hatırlatması gönder veya iptal et."],
      },
      {
        title: "Silinen Siparişler",
        path: "/admin/silinen-siparisler",
        what: "Silinmiş siparişlerin arşivi (geri getirme/inceleme için).",
        where: "Siparişler → Silinen Siparişler.",
        how: ["Kayıt aç, gerekirse ilgili bilgiyi incele."],
      },
    ],
  },
  {
    key: "katalog",
    title: "Katalog",
    icon: "Package",
    intro: "Ürünler, kategoriler, varyantlar, ölçü tabloları, toplu stok/fiyat.",
    items: [
      {
        title: "Tüm Ürünler",
        path: "/admin/urunler",
        what: "Ürün ekleme/düzenleme; fiyat, stok, varyant (beden/renk), görsel, açıklama, koleksiyon.",
        where: "Katalog → Tüm Ürünler.",
        how: [
          "Yeni ürün için 'Ekle'; mevcut için ürüne tıkla.",
          "Ad, açıklama, kategori, marka, fiyat ve indirimli fiyat (sale_price) gir.",
          "Varyantları (beden/renk) ve her varyantın stok/barkodunu tanımla.",
          "Görselleri yükle, kalıp bilgisini (dar/normal/bol) seç, kaydet.",
          "Stok hareketleri sekmesinden geçmiş giriş/çıkışı gör.",
        ],
        tips: [
          "Kalıp bilgisi ürün sayfasında beden önerisini besler (oversize→bir beden küçük vb.).",
          "Stok kodunun başı FcFw/FCss ise ürün raporunda koleksiyon otomatik ayrışır.",
        ],
      },
      {
        title: "Kategoriler",
        path: "/admin/kategoriler",
        what: "Kategori ağacı; sıralama, gizleme (gözü kapalı), üst/alt kategori.",
        where: "Katalog → Kategoriler.",
        how: [
          "Yeni kategori ekle veya mevcudu düzenle.",
          "Üst kategori seçerek alt kategori oluştur.",
          "Yayına hazır olmayan kategoriyi 'gizli' yaparak menüden kaldır.",
        ],
        tips: ["Bir kategoriyi hazırlarken 'gizli' tutup, tamamlanınca yayına alabilirsin (örn. Mont)."],
      },
      {
        title: "Markalar / Etiketler / Ürün Özellikleri",
        path: "/admin/markalar",
        what: "Ürünleri gruplayan marka, etiket ve özellik (renk, kumaş vb.) tanımları.",
        where: "Katalog → Markalar / Etiketler / Ürün Özellikleri.",
        how: ["İlgili ekrandan ekle/düzenle; ürün formunda bu değerleri seç."],
      },
      {
        title: "Varyantlar",
        path: "/admin/varyantlar",
        what: "Beden/renk gibi varyant setlerini merkezi yönetim.",
        where: "Katalog → Varyantlar.",
        how: ["Varyant tiplerini ve değerlerini tanımla; ürünlerde tekrar kullan."],
      },
      {
        title: "XML Feed'ler",
        path: "/admin/xml-feedler",
        what: "Google/Meta/pazaryeri ürün feed'leri (dışa aktarım).",
        where: "Katalog → XML Feed'ler.",
        how: ["Feed adresini kopyalayıp ilgili platforma tanıt; kapsamı ayarla."],
        tips: ["Stoğu biten varyantlar feed'de 'stokta yok' olarak işaretlenir."],
      },
      {
        title: "Ölçü Tabloları",
        path: "/admin/olcu-tablolari",
        what: "Ürün beden/ölçü tablosu; suud-tarzı görsel tablo ve 1200x1800 JPEG üretimi.",
        where: "Katalog → Ölçü Tabloları (veya ürün içinden).",
        how: [
          "Bedenleri ve ölçü sütunlarını (göğüs, bel, boy...) gir.",
          "Ürün bedeni ve manken boy/kilo bilgisini ekle.",
          "'Görsel Üret' ile 1200x1800 tabloyu oluştur; indir ve ürüne son resim olarak ata.",
        ],
        tips: ["Üretilen görsel indirilebilir; ürünün son görseli olarak da atanır."],
      },
      {
        title: "Stok & Fiyat Alarm / Stok Uyarıları",
        path: "/admin/stok-alarm",
        what: "Kritik stok ve fiyat değişimlerinde uyarı.",
        where: "Katalog → Stok & Fiyat Alarm / Stok Uyarıları.",
        how: ["Eşik değeri belirle; stok altına düşünce bildirim al."],
      },
      {
        title: "Toplu Fiyat/Stok (Excel)",
        path: "/admin/toplu-fiyat-stok",
        what: "Excel ile toplu fiyat ve stok güncelleme.",
        where: "Katalog → Toplu Fiyat/Stok (Excel).",
        how: [
          "Mevcut ürünleri Excel olarak dışa aktar.",
          "Fiyat/stok sütunlarını düzenle.",
          "Dosyayı geri yükle; değişiklikleri onayla.",
        ],
        tips: ["İçe aktarımdan önce önizleme ile değişecek satırları kontrol et."],
      },
    ],
  },
  {
    key: "raporlar",
    title: "Raporlar",
    icon: "TrendingUp",
    intro: "Satış, kârlılık, ürün, stok, üye ve gelişmiş analizler.",
    items: [
      {
        title: "Satış Raporları",
        path: "/admin/raporlar/satis",
        what: "Tarih aralığına göre ciro, sipariş ve sepet ortalaması; gün/hafta/ay kırılımı.",
        where: "Raporlar → Satış Raporları.",
        how: ["Tarih aralığı ve kaynağı (site/Trendyol/HB) seç; grafik ve tabloyu incele."],
        tips: ["İptal ve iade ciroya dahil edilmez; kırılım için 'ciro kırılımı' kartına bak."],
      },
      {
        title: "Ürün Raporları",
        path: "/admin/raporlar/urun",
        what: "Her ürünün adedi, cirosu, güncel stoğu, en çok satan bedeni, platform dağılımı, SATIŞ HIZI ve KOLEKSİYON.",
        where: "Raporlar → Ürün Raporları.",
        how: [
          "Tarih aralığını seç.",
          "Satış hızı renk koduna bak: 🟢 haftada 5+, 🟡 haftada 1-4, 🔴 ayda 0-2.",
          "Koleksiyon (FcFw/FCss) veya hız filtresiyle daralt.",
          "Kolon başlığına tıklayarak sırala; ürüne tıklayıp beden/platform dağılımını aç.",
        ],
        tips: ["Koleksiyon ayrımı stok kodunun başındaki FcFw/FCss ön ekinden gelir."],
      },
      {
        title: "Kârlılık Analizi & Pazaryeri Karlılık",
        path: "/admin/raporlar/karlilik",
        what: "Kategori × pazaryeri net kâr: komisyon, kargo, reklam, KDV, kurumlar vergisi dahil.",
        where: "Raporlar → Kârlılık Analizi / Pazaryeri Karlılık.",
        how: [
          "Komisyon/hizmet/reklam oranlarını ayar kartından gir (aylık reklam bütçesi tarih aralığına orantılanır).",
          "Net kârı kanal ve kategori bazında oku.",
        ],
      },
      {
        title: "Stok Raporu / Kâr & Stok Değer",
        path: "/admin/raporlar/stok",
        what: "Varyant bazında güncel stok ve stok değerlemesi.",
        where: "Raporlar → Stok Raporu / Kâr & Stok Değer.",
        how: ["Stok değerini maliyet veya satış fiyatı üzerinden görüntüle."],
      },
      {
        title: "Üye Raporu / Gelişmiş / İl-İlçe & Kanal / İade & Trend",
        path: "/admin/raporlar/uye",
        what: "Müşteri, konum, kanal ve iade trend analizleri.",
        where: "Raporlar → ilgili alt başlık.",
        how: ["Tarih ve kırılım seç; tabloyu dışa aktar."],
      },
    ],
  },
  {
    key: "uretim",
    title: "Üretim",
    icon: "Factory",
    intro: "İmalat takibi ve üretim planı.",
    items: [
      {
        title: "İmalat Takip & Üretim Planı",
        path: "/admin/imalat",
        what: "Üretime verilen ürünlerin aşama takibi ve planlama tablosu.",
        where: "Üretim → İmalat Takip / İmalat Planı.",
        how: [
          "Üretim emri oluştur; adet ve bedenleri gir.",
          "Aşamayı (kesim, dikim, ütü, hazır) güncelle.",
          "Tamamlanınca stoğa girişini yap.",
        ],
        tips: ["İmalattan stok girişi mükerrer sayılmaz; stok hareketi olarak işlenir."],
      },
    ],
  },
  {
    key: "tasarim",
    title: "Tasarım (İçerik)",
    icon: "PenTool",
    intro: "Tema, banner/slider, popup, duyuru, menü, sayfa ve footer tasarımı.",
    items: [
      {
        title: "Tema Yönetimi",
        path: "/admin/temalar",
        what: "Site renk, yazı tipi ve genel görünüm ayarları.",
        where: "Tasarım → Tema Yönetimi.",
        how: ["Renk/tipografi seç; kaydet; ön yüzde canlı gör."],
      },
      {
        title: "Bannerlar & Sliderlar",
        path: "/admin/bannerlar",
        what: "Anasayfa hero slider ve kampanya bannerları; masaüstü/mobil ayrı görsel.",
        where: "Tasarım → Bannerlar & Sliderlar.",
        how: [
          "Yeni slide ekle; masaüstü ve mobil görselini ayrı yükle.",
          "Bağlantı (kategori/ürün/kampanya) ve sırasını belirle.",
          "Yayın tarih aralığı ver.",
        ],
        tips: ["Mobil için ayrı görsel yüklemezsen masaüstü görseli kırpılabilir."],
      },
      {
        title: "Popuplar & Duyurular",
        path: "/admin/popuplar",
        what: "Site açılışında popup ve üst şerit duyuru.",
        where: "Tasarım → Popuplar / Duyurular.",
        how: ["Metin/görsel, gösterim koşulu ve tarih aralığı belirle; yayınla."],
      },
      {
        title: "Menü Yönetimi",
        path: "/admin/menu-yonetimi",
        what: "Ön yüz üst menü ve kategorilerin sıralaması/görünürlüğü.",
        where: "Tasarım → Menü Yönetimi.",
        how: ["Menü öğelerini sürükle-sırala; gizle/göster; alt menü ekle."],
        tips: ["Fular/aksesuar gibi kategorilerin menüdeki yerini buradan düzenlersin."],
      },
      {
        title: "Footer Tasarımı",
        path: "/admin/footer-tasarim",
        what: "Alt bilgi kolonları, linkler (KVKK, iletişim), bülten ve sosyal ikonlar.",
        where: "Tasarım → Footer Tasarımı.",
        how: ["Kolon başlıkları ve linklerini düzenle; KVKK/iletişim linklerini ekle."],
      },
      {
        title: "Instagram Akışı",
        path: "/admin/instagram",
        what: "Anasayfada Instagram gönderi akışı (dossha-tarzı ızgara).",
        where: "Tasarım → Instagram Akışı.",
        how: ["Hesabı bağla veya gönderi görsellerini/linklerini ekle; ızgarayı yayınla."],
      },
      {
        title: "Sayfalar (CMS) & Sayfa Tasarımı",
        path: "/admin/sayfalar",
        what: "Hakkımızda, iade koşulları, mesafeli satış vb. statik sayfalar.",
        where: "Tasarım → Sayfalar (CMS) / Sayfa Tasarımı.",
        how: ["Sayfa oluştur/düzenle; başlık, içerik ve SEO alanlarını doldur; yayınla."],
      },
    ],
  },
  {
    key: "uyeler",
    title: "Üyeler",
    icon: "Users",
    intro: "Üye listesi, B2B grupları, segmentler, sorular ve destek talepleri.",
    items: [
      {
        title: "Üye Listesi",
        path: "/admin/uyeler",
        what: "Kayıtlı müşteriler; sipariş geçmişi, adres ve iletişim.",
        where: "Üyeler → Üye Listesi.",
        how: ["Ad-soyad, e-posta veya telefonla ara; üyeye tıklayıp detay gör."],
        tips: ["Eksik ad-soyad bilgisi düzeltmesi bu ekrandan yapılır."],
      },
      {
        title: "Üye Grupları (B2B) & Segmentler (RFM)",
        path: "/admin/uye-gruplari",
        what: "Toptan/bayi fiyat grupları ve RFM tabanlı müşteri segmentleri.",
        where: "Üyeler → Üye Grupları / Müşteri Segmentleri.",
        how: ["Grup oluştur, fiyat çarpanı/indirim tanımla; segmentleri kampanyada hedefle."],
      },
      {
        title: "Müşteri Soruları & Destek Talepleri",
        path: "/admin/sorular",
        what: "Ürün soruları ve destek (ticket) yönetimi.",
        where: "Üyeler → Müşteri Soruları / Destek Talepleri.",
        how: ["Soruyu/talebi aç, yanıtla; ürün sorusunu yayınla veya gizle."],
      },
      {
        title: "Bloklu Müşteriler",
        path: "/admin/bloklu-musteriler",
        what: "Sipariş vermesi engellenen müşteriler.",
        where: "Üyeler → Bloklu Müşteriler.",
        how: ["Müşteriyi blokla/blok kaldır; neden ekle."],
      },
    ],
  },
  {
    key: "pazarlama",
    title: "Pazarlama",
    icon: "Megaphone",
    intro: "Kampanya, kupon, kargo/ödeme kuralları, toplu mail ve yorumlar.",
    items: [
      {
        title: "Kampanyalar",
        path: "/admin/kampanyalar",
        what: "İndirim kampanyaları (yüzde/tutar, kategori/ürün bazlı, X al Y öde).",
        where: "Pazarlama → Kampanyalar.",
        how: ["Kampanya tipi, kapsam (ürün/kategori) ve tarih aralığını belirle; yayınla."],
      },
      {
        title: "Kuponlar",
        path: "/admin/kuponlar",
        what: "İndirim kuponları; ilk sipariş, minimum tutar, bedava kargo koşulları.",
        where: "Pazarlama → Kuponlar.",
        how: ["Kod, indirim tipi, koşul ve son kullanım tarihi gir; oluştur."],
        tips: ["Bedava kargo kuponu sepette kargo ücretini sıfırlar."],
      },
      {
        title: "Kargo/Ödeme Kuralları",
        path: "/admin/kargo-odeme-kurallari",
        what: "Kargo ücreti, bedava kargo eşiği, kapıda ödeme ücreti ve ödeme yöntemi kuralları.",
        where: "Pazarlama → Kargo/Ödeme Kuralları.",
        how: ["Bedava kargo eşiği, kapıda ödeme ücreti ve bölge kurallarını gir."],
        tips: ["Kapıda ödeme ücreti gibi sabitler Ayarlar → İşletme Kuralları'nda da yönetilir."],
      },
      {
        title: "Toplu Mail",
        path: "/admin/toplu-mail",
        what: "Segment/gruba toplu e-posta gönderimi.",
        where: "Pazarlama → Toplu Mail.",
        how: ["Hedef kitleyi seç, şablonu hazırla, test gönder, yayınla."],
        tips: ["Gönderim için SMTP (Ayarlar → E-posta) ve gönderim kredisi gerekir."],
      },
      {
        title: "Ürün Yorumları & Trendyol Yorumları",
        path: "/admin/yorumlar",
        what: "Müşteri yorumları onayı ve Trendyol public yorumlarını (3★ ve üzeri) çekme.",
        where: "Pazarlama → Ürün Yorumları.",
        how: [
          "Bekleyen yorumları onayla/gizle.",
          "Trendyol Yorumları kartında Cloudflare Worker URL'sini kaydet.",
          "Alt yıldız eşiğini seç (varsayılan 3) ve 'Yorumları Çek'; haftalık otomatik de çalışır.",
        ],
        tips: ["Yorumlar gerçek yorum tarihiyle saklanır; tekrar çekimde mükerrer eklenmez."],
      },
      {
        title: "Terkedilmiş Sepetler / Kaynak & Funnel / Influencer",
        path: "/admin/terkedilmis-sepet",
        what: "Sepette bırakılan siparişler, trafik kaynağı ve iş birliği takibi.",
        where: "Pazarlama → ilgili başlık.",
        how: ["Terk edilen sepetlere hatırlatma; kaynak/funnel ile dönüşüm analizi."],
      },
    ],
  },
  {
    key: "seo",
    title: "SEO",
    icon: "FileText",
    intro: "Meta etiketleri ve 301 yönlendirmeler.",
    items: [
      {
        title: "Meta Yönetimi",
        path: "/admin/seo/meta",
        what: "Sayfa/ürün/kategori başlık ve açıklama (meta) etiketleri.",
        where: "SEO → Meta Yönetimi.",
        how: ["İlgili sayfayı seç; başlık ve açıklamayı SEO uyumlu yaz."],
      },
      {
        title: "301 Yönlendirmeler",
        path: "/admin/seo/yonlendirmeler",
        what: "Eski adresleri yeni adreslere kalıcı yönlendirme.",
        where: "SEO → 301 Yönlendirmeler.",
        how: ["Kaynak ve hedef adresi gir; kaydet."],
      },
    ],
  },
  {
    key: "entegrasyonlar",
    title: "Entegrasyonlar",
    icon: "Cable",
    intro: "Pazaryerleri, ödeme, kargo, e-fatura, güvenlik ve otomasyon.",
    items: [
      {
        title: "Pazaryerleri Hub",
        path: "/admin/pazaryerleri",
        what: "Trendyol, Hepsiburada, Temu, Amazon bağlantıları ve senkron durumu.",
        where: "Entegrasyonlar → Pazaryerleri Hub.",
        how: [
          "İlgili pazaryeri için API anahtarlarını gir.",
          "Ürün/sipariş/stok senkronunu başlat; aksiyon bekleyen sayısını izle.",
        ],
        tips: ["Faturalar sipariş onayı + fatura kesimiyle otomatik pazaryerine iletilir."],
      },
      {
        title: "Detaylı Aktarım & Eşleştirme",
        path: "/admin/entegrasyonlar",
        what: "Ürün/kategori/marka eşleştirme ve içe-dışa aktarım detayları.",
        where: "Entegrasyonlar → Detaylı Aktarım & Eşleştirme.",
        how: ["Kaynak alanları sistem alanlarıyla eşle; aktarımı çalıştır."],
      },
      {
        title: "Marka / Kategori Eşleştirme",
        path: "/admin/kategori-eslestir",
        what: "Pazaryeri kategori/marka değerlerini sistem karşılıklarına bağla.",
        where: "Entegrasyonlar → Marka/Kategori Eşleştirme.",
        how: ["Eşleşmeyen değeri seç, doğru karşılığı ata."],
      },
      {
        title: "Entegrasyon Logları & Aktarılamayanlar",
        path: "/admin/entegrasyon-loglari",
        what: "Senkron olayları ve aktarılamayan kayıtların nedenleri.",
        where: "Entegrasyonlar → Entegrasyon Logları / Aktarılamayanlar.",
        how: ["Hatalı kaydı aç, nedeni gör, düzelt ve yeniden dene."],
      },
      {
        title: "Güvenlik Paneli & Sistem Sağlığı",
        path: "/admin/guvenlik-paneli",
        what: "Güvenlik durumu, oturumlar ve servis sağlık kontrolleri.",
        where: "Entegrasyonlar → Güvenlik Paneli / Sistem Sağlığı.",
        how: ["Uyarıları incele; sağlık kontrolünü çalıştır."],
      },
      {
        title: "Secrets Vault",
        path: "/admin/secrets-vault",
        what: "API anahtarı ve şifrelerin şifreli saklandığı kasa.",
        where: "Entegrasyonlar → Secrets Vault.",
        how: ["Gizli değeri ekle/güncelle; değerler maskeli gösterilir."],
      },
      {
        title: "İYS (İzin Yönetim Sistemi)",
        path: "/admin/iys",
        what: "Ticari elektronik ileti izinlerinin İYS'ye bildirimi.",
        where: "Entegrasyonlar → İYS.",
        how: ["İzinleri senkronize et; onay/ret durumlarını gör."],
      },
      {
        title: "AI Asistan",
        path: "/admin/ai-asistan",
        what: "Ürün açıklaması, başlık ve içerik için yapay zeka desteği.",
        where: "Entegrasyonlar → AI Asistan.",
        how: ["İstemi yaz; öneriyi düzenleyip ürüne uygula."],
      },
    ],
  },
  {
    key: "ayarlar",
    title: "Ayarlar",
    icon: "Settings",
    intro: "İşletme kuralları, e-fatura, kargo, e-posta, kullanıcılar ve özel tema.",
    items: [
      {
        title: "İşletme Kuralları",
        path: "/admin/ayarlar/isletme-kurallari",
        what: "Koda gömülü olmayan tüm iş kuralları: havale iptal süresi, kapıda ödeme ücreti, iade penceresi, çalışma günleri, kargo saat sınırı, puan kullanımı vb.",
        where: "Ayarlar → İşletme Kuralları.",
        how: [
          "Kuralı bul (ör. havale iptal süresi 72 saat).",
          "Değeri değiştir veya hazır seçeneklerden birini işaretle.",
          "Kaydet — değişiklik anında geçerli olur.",
        ],
        tips: ["Resmî tatiller 5 yıl için tanımlı; kargo hesabı çalışma günü + 10:30 saat sınırını dikkate alır."],
      },
      {
        title: "Özel Tema (CSS/JS)",
        path: "/admin/ayarlar/ozel-tema",
        what: "Kendi CSS ve (güvenli) JS ile temayı özelleştirme.",
        where: "Ayarlar → Özel Tema (CSS/JS).",
        how: [
          "CSS düzenleyicisine stilini yaz.",
          "Gerekirse JS ekle ve 'JS etkin' anahtarını aç.",
          "Kaydet; değişiklik yalnızca ön yüzde uygulanır (yönetim paneli etkilenmez).",
        ],
        tips: ["JS varsayılan kapalıdır; güvenlik için yalnızca gerektiğinde ve kontrollü aç."],
      },
      {
        title: "E-Arşiv / E-Fatura",
        path: "/admin/ayarlar/e-fatura",
        what: "Doğan e-Dönüşüm bağlantısı; e-Arşiv ve e-Fatura kesimi ve link şablonları.",
        where: "Ayarlar → E-Arşiv / E-Fatura.",
        how: [
          "Doğan kullanıcı/şifre ve test/canlı modunu gir.",
          "e-Arşiv ve e-Fatura link şablonlarını tanımla.",
          "Sipariş onayında fatura otomatik kesilir ve pazaryerine iletilir.",
        ],
        tips: ["Faturası kesilmiş ama pazaryerine gitmemiş siparişler için re-push aracı vardır."],
      },
      {
        title: "Kargo Firması Ayarları & Gönderici Adresi",
        path: "/admin/ayarlar/kargo",
        what: "MNG/kargo entegrasyonu, barkod ve çıkış/depo adresi.",
        where: "Ayarlar → Kargo Firması Ayarları / Gönderici Adresi.",
        how: ["Kargo API bilgilerini ve gönderici adresini gir; barkod üretimini test et."],
      },
      {
        title: "E-posta (SMTP) & Bildirim Şablonları",
        path: "/admin/ayarlar/eposta",
        what: "Giden e-posta sunucusu ve sipariş/iade bildirim şablonları.",
        where: "Ayarlar → E-posta (SMTP) / Bildirim Şablonları.",
        how: ["SMTP bilgilerini gir, test e-postası gönder; şablon metinlerini düzenle."],
      },
      {
        title: "Sipariş Durumları & Bildirim Ayarları",
        path: "/admin/ayarlar/siparis-durumlari",
        what: "Sipariş durum akışı ve hangi durumda hangi bildirimin gideceği.",
        where: "Ayarlar → Sipariş Durumları / Bildirim Ayarları.",
        how: ["Durumları düzenle; her durum için e-posta/SMS bildirimini aç/kapat."],
      },
      {
        title: "Pazarlama Pixelleri & CAPI",
        path: "/admin/ayarlar/pixel",
        what: "Meta/TikTok/Google/Snapchat/Pinterest pixel ve sunucu-taraflı (CAPI) olayları.",
        where: "Ayarlar → Pazarlama Pixelleri / CAPI Loglar & Kuyruk.",
        how: ["Pixel ID ve erişim anahtarını gir; olay eşleşmelerini ve kuyruğu izle."],
      },
      {
        title: "Kullanıcılar & Roller",
        path: "/admin/kullanicilar",
        what: "Yönetim kullanıcıları ve rol bazlı yetkiler.",
        where: "Ayarlar → Kullanıcılar & Roller.",
        how: ["Kullanıcı ekle, rol ata; rolün erişebileceği bölümleri belirle."],
        tips: ["Muhasebe gibi hassas işlemler ayrı yetki ister; her kullanıcıya minimum yetki ver."],
      },
      {
        title: "Menü Düzeni",
        path: "/admin/ayarlar/menu-duzeni",
        what: "Yönetim menüsünün sırası ve gizlenecek gruplar (kullanıcı bazlı).",
        where: "Ayarlar → Menü Düzeni.",
        how: ["Grupları sürükle-sırala; kullanmadıklarını gizle; sıfırla ile varsayılana dön."],
      },
    ],
  },
];

/** Arama için düz metin — başlık + içerik. */
export function trainingSearchIndex() {
  const rows = [];
  for (const sec of TRAINING) {
    for (const it of sec.items) {
      const hay = [
        sec.title, it.title, it.what, it.where,
        ...(it.how || []), ...(it.tips || []),
      ].join(" ").toLocaleLowerCase("tr");
      rows.push({ sectionKey: sec.key, sectionTitle: sec.title, item: it, hay });
    }
  }
  return rows;
}
