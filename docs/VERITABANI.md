# Veritabanı Seçenekleri

Platformun tamamı MongoDB üzerine kurulu (motor/pymongo, ~100 route dosyası
`db.koleksiyon` kullanıyor). PostgreSQL/MySQL'e geçmek veri katmanının baştan
yazılması demektir — bu bir yapılandırma değişikliği değil, projenin yeniden
yazımıdır. Aşağıdaki seçeneklerin hepsi **MongoDB kalır**, yalnız nerede
çalıştığı değişir. Değişen tek şey `MONGO_URL`.

---

## Seçenek A — MongoDB Atlas M0 (ücretsiz katman)

Atlas'ın **süresiz ücretsiz** paketi. Kredi kartı istemez.

| | |
|---|---|
| Alan | 512 MB |
| Yedek | Yok |
| Kurulum | 10 dakika |
| Bakım | Yok |

```
MONGO_URL=mongodb+srv://<kullanici>:<parola>@<cluster>.mongodb.net/?retryWrites=true&w=majority
```

**Dikkat — 512 MB bu proje için dar.** 6.475 ürün + 19.242 varyant tek başına
~60-80 MB. Üstüne siparişler, denetim kayıtları (`audit_logs`), bildirim
kayıtları ve CAPI olayları sürekli büyür. Birkaç ay içinde dolması beklenir;
dolduğunda **yazma durur**.

Başlangıç için uygundur, kalıcı çözüm değildir.

---

## Seçenek B — Kendi sunucunuzda MongoDB (önerilen)

`83.150.214.157` şu an boşta: barındırma Railway + Cloudflare'e taşındı, o
sunucuda hiçbir şey çalışmıyor. Zaten ödediğiniz için **ek maliyeti yok** ve
alan sınırı diskiniz kadar.

### Kurulum (Ubuntu)

```bash
# 1) MongoDB deposu ve kurulum
curl -fsSL https://pgp.mongodb.com/server-8.0.asc | sudo gpg -o /usr/share/keyrings/mongodb.gpg --dearmor
echo "deb [signed-by=/usr/share/keyrings/mongodb.gpg] https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/8.0 multiverse" \
  | sudo tee /etc/apt/sources.list.d/mongodb.list
sudo apt update && sudo apt install -y mongodb-org
sudo systemctl enable --now mongod

# 2) Yönetici kullanıcı
mongosh <<'EOF'
use admin
db.createUser({user:"meluni_admin", pwd:"<GUCLU-PAROLA>", roles:["root"]})
EOF
```

### Kimlik doğrulama ve dış erişim

`/etc/mongod.conf`:

```yaml
security:
  authorization: enabled
net:
  port: 27017
  bindIp: 0.0.0.0        # Railway'den erişilecek
  tls:
    mode: requireTLS
    certificateKeyFile: /etc/ssl/mongodb/meluni.pem
```

```bash
sudo systemctl restart mongod
```

> **Kritik:** `bindIp: 0.0.0.0` + `authorization` kapalı = veritabanınız
> internete tamamen açık demektir. Otomatik tarayıcılar açık MongoDB'leri
> dakikalar içinde bulup siler ve fidye notu bırakır. `authorization: enabled`
> ve TLS **açılmadan** portu dışarı açmayın.

### Güvenlik duvarı

```bash
sudo ufw allow OpenSSH
sudo ufw allow 27017/tcp        # mümkünse Railway çıkış IP'lerine kısıtlayın
sudo ufw enable
```

### Yedekleme (zorunlu)

Atlas'ta yedek hazır gelir; kendi sunucunuzda **sizin işiniz**.

```bash
# /etc/cron.daily/meluni-mongodump
#!/bin/bash
set -euo pipefail
DEST=/var/backups/mongo
mkdir -p "$DEST"
mongodump --uri="mongodb://meluni_admin:<parola>@127.0.0.1:27017/?authSource=admin" \
  --archive="$DEST/meluni-$(date +%F).gz" --gzip
find "$DEST" -name 'meluni-*.gz' -mtime +14 -delete
```

`chmod +x` vermeyi unutmayın. Yedeği ayrıca sunucu dışına kopyalayın — disk
ölürse aynı diskteki yedek de gider.

### Bağlantı dizesi

```
MONGO_URL=mongodb://meluni_admin:<parola>@83.150.214.157:27017/meluni?authSource=admin&tls=true
```

### Tek gerçek dezavantajı: gecikme

Railway (çoğunlukla AB/ABD) ile Türkiye'deki sunucunuz arasında her sorgu
~40-60 ms yol gider. Bir sayfa 20 sorgu yapıyorsa bu ~1 saniye eder. Azaltmak
için Railway bölgesini Frankfurt'a alın veya sunucuyu AB'de bir sağlayıcıda
tutun.

---

## Karşılaştırma

| | Atlas M0 | Kendi sunucu |
|---|---|---|
| Ücret | 0 | 0 (sunucu zaten var) |
| Alan | 512 MB | disk kadar |
| Yedek | yok | siz kurarsınız |
| Bakım | yok | güncelleme, izleme sizde |
| Gecikme | düşük (bölge seçilebilir) | 40-60 ms |
| Güvenlik | hazır | auth + TLS + firewall sizde |

**Öneri:** Bugün Atlas M0 ile başlayın — 10 dakikada ayağa kalkar ve
kurulumun geri kalanını bloklamaz. Katalog ve sipariş hacmi 512 MB'a
yaklaşınca kendi sunucunuza taşıyın. Taşıma tek komuttur:

```bash
mongodump --uri="<atlas-uri>" --archive=gec.gz --gzip
mongorestore --uri="<kendi-sunucu-uri>" --archive=gec.gz --gzip
```

Kodda **hiçbir değişiklik gerekmez** — yalnız Railway'deki `MONGO_URL` değişir.

---

## Uygun olmayan seçenekler

| Seçenek | Neden olmaz |
|---|---|
| Supabase / Neon / PlanetScale | PostgreSQL/MySQL — veri katmanının baştan yazılmasını gerektirir |
| Firebase / Firestore | Farklı sorgu modeli; motor sürücüsü çalışmaz |
| Railway MongoDB eklentisi | Ücretsiz değil, plan kredinizi tüketir |
| SQLite / dosyaya yazma | Eşzamanlı yazma ve çok-instance desteği yok |
