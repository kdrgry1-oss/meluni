# Meluni aktarımı ve tasarım önizlemesi

Çalışma dalı: `codex/meluni-white-label-storefront`.
Meluni başlangıcı: `a0e9beb8`.
Okunan Facette referansı: `d4a34ae5a61c95233e178ea833b9e43bde4742f3`.

Tüm kaynak değişiklikleri Meluni klasöründedir. Referans repo, canlı servisler ve veritabanlarına yazma yapılmaz. Deployment dosyaları Meluni kurulumuna aittir; Facette deployment dosyaları aktarılmadı.

## Önizleme

`previews/meluni-editorial` ayrı bir tasarım prototipidir. Gerçek sipariş, ödeme veya bülten kaydı oluşturmaz. Ürün adı, fiyat, görsel ve stokta bulunan bedenler 11 Ekim 2026 tarihinde Meluni'nin herkese açık ürün API'sinden alınmıştır. `catalog-source.json` seçkinin kaynağını saklar; üretim uygulaması için statik katalog değildir.

Çalıştırma: `python3 -m http.server 4173 --bind 127.0.0.1 --directory previews/meluni-editorial`.

## Aktarım sınırları

Yeni tema galerisi, kombin editörü, rapor ve pazaryeri ekranları, ödeme ve yetki denetimleri kaynak olarak aktarıldı. Meluni XML tedarikçi ve AI görsel modülleri korunur. Marka yapılandırması ortam ve panel ayarlarından okunur. Kaynak markanın firma, adres, müşteri ve kampanya bilgilerini yeni kurulum varsayılanlarına taşımamak için marka seed'i ve kurulum düzeltme görevleri ayrıştırıldı.

Kaynak kurulumuna özel 39 otomatik scheduler düzeltme görevi çıkarıldı. Sipariş/müşteri kimliklerine bağlı startup düzeltmeleri ve örnek kupon istisnaları taşınmaz. Genel entegrasyon ve güvenlik görevleri korunur. Pasif/silinmiş Meluni ürünleri Trendyol senkronunda sıfır adet gönderir; tedarikçi renk eşlemeleri korunur.

## Doğrulama

- Ön yüz: 23 test paketi, 105 test geçti.
- Marka ve SEO: 11 test geçti.
- Python derleme ve diff boşluk denetimi geçti.
- Pasif/silinmiş ürünlerin Trendyol stok sıfırlaması: 1 regresyon testi geçti; zorlanmış miktar verilse de sıfır stok korunur.
- Tasarım önizlemesi mobil ve masaüstünde kontrol edildi; Meluni ürün görselleri yüklendi.
- Üretim derlemesi geçti. Yerel derlemede canlı ortam değişkenleri bulunmadığından yer tutucular temizlendi; canlı derleme Meluni ortam değerleriyle yapılmalıdır. Ödeme webhook doğrulaması, ödeme defteri, kupon ve hediye kartı iade zinciri, XML URL gizleme, iade hold kurtarma ve MFA değişikliği testleri: 86 test geçti. Tam referans test takımının tamamı çalıştırılmadı; canlı yayın öncesinde Meluni deployment/env ve tema aktivasyonu ayrıca doğrulanmalıdır.

Henüz push veya canlı yayın yapılmadı. Canlı veritabanında marka/tema migration çalıştırılmadı.

Referans test takımındaki `test_alert_instant_sms.py` Facette'ye özel otomatik telefon ayarı görevini, `test_audit_f05_f07_f20_f22_release_static.py` ise Facette Codemagic/backup dağıtım dosyalarını bekler. Bu dosyalar bilinçli olarak aktarılmadığı için ilk taramada bu testler başarısız oldu (24 geçti, 5 başarısız). Uygulanabilir sunucu testlerinin sonraki koşusunda bu iki referans kurulum testi hariç tutulur; Meluni deployment dosyaları ayrıca değerlendirilmelidir.
