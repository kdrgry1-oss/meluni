# Cloudflare DNS ve Yonlendirmeler

Alan adi Cloudflare nameserver'larina tasindiktan sonra yapilacaklar.
Asagida `<SUNUCU_IP>` yazan yere kendi sunucu IP'nizi yazin.

---

## 1. NS gecisi tamamlandi mi?

`.tr` uzantilarinda nameserver degisikligi bazen 24 saati bulur.
Kontrol:

```bash
dig NS meluni.com.tr +short
```

Cikti Cloudflare nameserver'larini (`*.ns.cloudflare.com`) gosteriyorsa gecis tamam.
Cloudflare panelinde de zone durumu **Active** olmali; **Pending Nameserver Update**
kaldigi surece asagidaki kayitlarin hicbiri yayina cikmaz.

---

## 2. Temel DNS kayitlari

Cloudflare > ilgili domain > **DNS** > **Records**

| Tip | Name | Content | Proxy | Aciklama |
|---|---|---|---|---|
| `A` | `@` | `<SUNUCU_IP>` | **Proxied** (turuncu) | Ana alan adi |
| `CNAME` | `www` | `meluni.com.tr` | **Proxied** (turuncu) | www surumu |
| `A` | `sunucu` | `<SUNUCU_IP>` | **DNS only** (gri) | SSH/panel icin dogrudan erisim |

**`sunucu` kaydi neden gri olmali:** Cloudflare proxy'si sadece HTTP/HTTPS
trafigini tasir. Turuncu buluttaki bir isim uzerinden SSH (port 22) calismaz.
Bu yuzden yonetim icin proxy'siz ayri bir isim birakiyoruz.

> Gri bulutlu kayit gercek sunucu IP'nizi herkese acar. Sunucu IP'sini gizli
> tutmak istiyorsaniz bu kaydi hic olusturmayin ve SSH'a dogrudan IP ile baglanin.

Gecis oncesi test icin kendi bilgisayarinizda `hosts` dosyasina
`<SUNUCU_IP> meluni.com.tr` satirini ekleyerek siteyi DNS'i beklemeden gorebilirsiniz.

---

## 3. SSL/TLS ayarlari

Cloudflare > **SSL/TLS**

1. **Overview > Encryption mode: `Full (strict)`**
   - `Flexible` **kullanmayin**: Cloudflare ile sunucunuz arasi trafik sifresiz kalir
     ve cogu uygulamada yonlendirme dongusu (redirect loop) uretir.
   - `Full (strict)` gecerli bir sertifika ister; bir sonraki adim onu kuruyor.

2. **Origin Server > Create Certificate**
   - Hostname: `meluni.com.tr, *.meluni.com.tr`
   - 15 yillik sertifika uretir. Ciktiyi sunucuya kaydedin:
     ```
     /etc/ssl/cloudflare/meluni.pem      # certificate
     /etc/ssl/cloudflare/meluni.key      # private key (chmod 600)
     ```
   - Bu sertifika **sadece** Cloudflare uzerinden gelen istekler icin gecerlidir;
     tarayicidan dogrudan IP ile girildiginde gecersiz gorunur, normaldir.

3. **Edge Certificates**
   - `Always Use HTTPS`: **On** (http -> https)
   - `Automatic HTTPS Rewrites`: **On**
   - `Minimum TLS Version`: `TLS 1.2`
   - `HSTS`: once site sorunsuz calissin, sonra acin (geri almasi zordur)

### Nginx ornegi (sunucu tarafi)

```nginx
server {
    listen 443 ssl http2;
    server_name meluni.com.tr www.meluni.com.tr;

    ssl_certificate     /etc/ssl/cloudflare/meluni.pem;
    ssl_certificate_key /etc/ssl/cloudflare/meluni.key;

    root /home/<KULLANICI>/apps/meluni/current/public;
    index index.html;

    location / {
        try_files $uri $uri/ =404;
        # Uygulama sunucusu varsa yerine:
        # proxy_pass http://127.0.0.1:3000;
        # proxy_set_header Host $host;
        # proxy_set_header X-Forwarded-Proto $scheme;
        # proxy_set_header X-Forwarded-For $http_cf_connecting_ip;
    }
}

server {
    listen 80;
    server_name meluni.com.tr www.meluni.com.tr;
    return 301 https://$host$request_uri;
}
```

> **Gercek ziyaretci IP'si:** Proxy arkasinda `$remote_addr` Cloudflare'in IP'sini
> gosterir. Ziyaretcinin gercek IP'si `CF-Connecting-IP` basligindadir.
> Nginx'te `ngx_http_realip_module` + Cloudflare IP araliklarini tanimlayin.

---

## 4. Yonlendirmeler (Redirect Rules)

Cloudflare > **Rules** > **Redirect Rules** > **Create rule**

### www -> koksuz (apex) yonlendirme

| Alan | Deger |
|---|---|
| Rule name | `www to apex` |
| When incoming requests match | Custom filter expression |
| Field / Operator / Value | `Hostname` `equals` `www.meluni.com.tr` |
| Then / Type | Dynamic |
| Expression | `concat("https://meluni.com.tr", http.request.uri.path)` |
| Status code | `301` |
| Preserve query string | **acik** |

Tersini isterseniz (apex -> www) hostname'i `meluni.com.tr` yapip hedefi
`https://www.meluni.com.tr` olarak yazin. **Ikisini ayni anda kurmayin**,
sonsuz dongu olusur.

### Eski yol -> yeni yol yonlendirmesi

Tek tek adresler icin **Bulk Redirects** (Rules > Bulk Redirects) daha uygundur;
binlerce satiri liste halinde yukleyebilirsiniz.

---

## 5. E-posta kayitlari

### Bu alan adiyla e-posta **kullanacaksaniz**

Saglayicinizin (Google Workspace, Yandex, hosting paneli vb.) verdigi
`MX`, `SPF`, `DKIM` kayitlarini girin. `MX` kayitlari **her zaman DNS only (gri)**
olmalidir — proxy'lenemezler.

| Tip | Name | Content | Proxy |
|---|---|---|---|
| `MX` | `@` | saglayicinin sunucusu (oncelik degeriyle) | DNS only |
| `TXT` | `@` | `v=spf1 include:<saglayici> ~all` | DNS only |
| `TXT` | `<secici>._domainkey` | saglayicinin DKIM degeri | DNS only |
| `TXT` | `_dmarc` | `v=DMARC1; p=quarantine; rua=mailto:you@meluni.com.tr` | DNS only |

### Bu alan adiyla e-posta **kullanmayacaksaniz**

Adiniza sahte e-posta gonderilmesini engellemek icin:

| Tip | Name | Content |
|---|---|---|
| `MX` | `@` | `.` (oncelik `0`) — null MX |
| `TXT` | `@` | `v=spf1 -all` |
| `TXT` | `_dmarc` | `v=DMARC1; p=reject;` |

---

## 6. CAA kaydi (onerilir)

Sadece izin verdiginiz otoritelerin sertifika uretebilmesini saglar:

| Tip | Name | Flags | Tag | Value |
|---|---|---|---|---|
| `CAA` | `@` | `0` | `issue` | `pki.goog` |
| `CAA` | `@` | `0` | `issue` | `letsencrypt.org` |

Cloudflare Universal SSL kullaniyorsaniz panel gerekli otoriteleri zaten onerir.

---

## 7. Kontrol listesi

```bash
# NS gecisi
dig NS meluni.com.tr +short

# A kaydi (proxy'liyse Cloudflare IP'si doner, bu normaldir)
dig A meluni.com.tr +short

# SSH icin ayirdiginiz isim gercek IP'yi vermeli
dig A sunucu.meluni.com.tr +short

# HTTPS ve yonlendirme zinciri
curl -sSI https://meluni.com.tr | head -n 1
curl -sSI http://meluni.com.tr | grep -i '^location'
curl -sSI https://www.meluni.com.tr | grep -i '^location'
```

Beklenen sonuc:
- `http://` istekleri `301` ile `https://`'e gider
- `www.` istekleri `301` ile koksuz adrese gider
- `https://meluni.com.tr` `200` doner

---

## 8. Sik karsilasilan sorunlar

| Belirti | Sebep / cozum |
|---|---|
| `ERR_TOO_MANY_REDIRECTS` | SSL modu `Flexible`. `Full (strict)` yapin |
| `Error 521 Web server is down` | Sunucuda 443 kapali veya servis calismiyor. Firewall'da 80/443'u acin |
| `Error 526 Invalid SSL certificate` | Origin sertifikasi yok/gecersiz. Adim 3.2'yi uygulayin |
| SSH baglanmiyor | Turuncu bulutlu isme baglaniyorsunuz. Gri bulutlu isim veya dogrudan IP kullanin |
| Loglarda hep ayni IP goruluyor | `CF-Connecting-IP` / real_ip yapilandirmasi eksik |
| Degisiklik gorunmuyor | Cloudflare cache. **Caching > Purge Everything** |
