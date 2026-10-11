#!/usr/bin/env node
/**
 * Derleme sonrası: index.html'de ÇÖZÜLMEMİŞ %REACT_APP_*% yer tutucularını temizler.
 *
 * NEDEN: CRA, tanımlı olmayan bir ortam değişkenini olduğu gibi bırakır. Değişken
 * geçilmeden derlenen pakette tarayıcı sekmesinde "%REACT_APP_SITE_TITLE%" yazıyor,
 * meta description ve canonical de yer tutucu olarak Google'a gidiyor. Canlıda tam
 * olarak bu oldu (yerelden elle derlenen pakette yalnız REACT_APP_BACKEND_URL
 * geçilmişti).
 *
 * Beyaz etiket kuralı gereği buraya firma-özel değer YAZILMAZ: yer tutucu boşa
 * çekilir, gerçek değerleri uygulama açılışta /api/settings'ten alıp yazar
 * (SeoManager). Boş bir başlık, yanlış bir başlıktan iyidir.
 *
 * Çıktı uyarı verir ama derlemeyi DÜŞÜRMEZ — eksik bir meta yüzünden deploy
 * durdurmak, sitenin hiç güncellenmemesinden kötüdür. BACKEND_URL ayrı:
 * onsuz paket zaten işe yaramaz, deploy akışı onu kendi kontrol ediyor.
 */
const fs = require("fs");
const path = require("path");

const dosya = path.join(__dirname, "..", "build", "index.html");
if (!fs.existsSync(dosya)) {
  console.warn("[strip-placeholders] build/index.html yok — atlandı.");
  process.exit(0);
}

const RX = /%REACT_APP_[A-Z0-9_]+%/g;
const once = fs.readFileSync(dosya, "utf8");
const bulunan = [...new Set(once.match(RX) || [])];

if (!bulunan.length) {
  console.log("[strip-placeholders] çözülmemiş yer tutucu yok.");
  process.exit(0);
}

fs.writeFileSync(dosya, once.replace(RX, ""), "utf8");
console.warn(
  "[strip-placeholders] UYARI — şu ortam değişkenleri derlemede tanımlı değildi, " +
  "yer tutucuları temizlendi:\n  " + bulunan.join("\n  ") +
  "\n  Doğru başlık/açıklama için bunları derleme ortamında tanımlayın."
);
