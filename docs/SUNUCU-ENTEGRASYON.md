# Sunucu Entegrasyonu (SSH Deploy)

Bu dokuman `meluni` reposunu kendi sunucunuza baglamak icin gereken
tum adimlari icerir. Kurulum bir kez yapilir; sonrasinda `main` branch'ine
her push otomatik deploy tetikler.

---

## 0. Once guvenlik

> **Onemli:** Sunucu parolasi bir sohbet penceresine duz metin olarak yazildiysa,
> o parolayi artik "sizmis" kabul edin. Ilk is olarak sunucuda degistirin:
>
> ```bash
> passwd
> ```
>
> Ayrica bu kurulum **parola yerine SSH anahtari** kullanir. Anahtar kurulumu
> bittikten sonra parolayla SSH girisini tamamen kapatmaniz onerilir (Adim 6).
>
> Parola, IP, anahtar gibi hicbir gizli bilgi bu repoya yazilmaz. Hepsi
> GitHub repository secret'i veya sunucudaki `shared/.env` dosyasinda durur.

---

## 1. Deploy'a ozel SSH anahtari uretin

Kendi bilgisayarinizda (sunucuda degil):

```bash
ssh-keygen -t ed25519 -C "meluni-deploy" -f ~/.ssh/meluni_deploy -N ""
```

Iki dosya olusur:

| Dosya | Ne ise yarar | Nereye gider |
|---|---|---|
| `~/.ssh/meluni_deploy` | **ozel** anahtar | GitHub secret `SSH_PRIVATE_KEY` |
| `~/.ssh/meluni_deploy.pub` | **acik** anahtar | Sunucudaki `~/.ssh/authorized_keys` |

Ozel anahtari asla repoya, mesaja veya ekran goruntusune koymayin.

---

## 2. Sunucuyu hazirlayin

Acik anahtari sunucuya tanitin:

```bash
ssh-copy-id -i ~/.ssh/meluni_deploy.pub -p 22 <KULLANICI>@<SUNUCU_IP>
```

`ssh-copy-id` yoksa `scripts/server-setup.sh` ayni isi yapar. Sunucuda:

```bash
# scripts/server-setup.sh dosyasini sunucuya kopyalayip:
DEPLOY_PUBKEY="$(cat ~/meluni_deploy.pub)" bash server-setup.sh
```

Script sunucuda su yapiyi kurar:

```
/home/<KULLANICI>/apps/meluni/
├── current -> releases/<en-son>   # aktif surum (symlink)
├── releases/                      # son 5 surum saklanir
├── shared/
│   └── .env                       # gercek ortam degiskenleri (repoya girmez)
└── tmp/
```

Anahtarin calistigini dogrulayin:

```bash
ssh -i ~/.ssh/meluni_deploy -p 22 <KULLANICI>@<SUNUCU_IP> "echo baglanti-ok"
```

---

## 3. GitHub secret'larini tanimlayin

Repo > **Settings** > **Secrets and variables** > **Actions** > **New repository secret**

| Secret | Zorunlu | Deger |
|---|---|---|
| `SSH_HOST` | evet | Sunucu IP veya hostname |
| `SSH_USER` | evet | Deploy kullanicisi |
| `SSH_PRIVATE_KEY` | evet | `cat ~/.ssh/meluni_deploy` ciktisinin **tamami** (`-----BEGIN`/`-----END` satirlari dahil) |
| `SSH_KNOWN_HOSTS` | onerilir | `ssh-keyscan -p 22 -H <SUNUCU_IP>` ciktisi |
| `SSH_PORT` | hayir | SSH portu, varsayilan `22` |
| `DEPLOY_PATH` | hayir | Varsayilan `/home/<SSH_USER>/apps/meluni` |

`SSH_KNOWN_HOSTS` tanimlanmazsa workflow host anahtarini ilk baglantida
dogrulamadan alir ve uyari basar. Ortadaki-adam saldirisina karsi bu secret'i
tanimlamak onemlidir.

---

## 4. Deploy'u calistirin

**Otomatik:** `main` branch'ine push edin.

**Elle:** GitHub > **Actions** > **Deploy** > **Run workflow**.

**Yerelden (GitHub'a ugramadan):**

```bash
DEPLOY_HOST=<SUNUCU_IP> \
DEPLOY_USER=<KULLANICI> \
SSH_KEY=~/.ssh/meluni_deploy \
./scripts/deploy.sh
```

### Deploy nasil isliyor

1. `git archive HEAD` ile bir paket uretilir (sadece commit'lenmis dosyalar).
2. Paket sunucudaki `tmp/` altina kopyalanir.
3. `releases/<tarih>-<sha>` dizinine acilir.
4. `shared/.env` yeni release'e symlink'lenir.
5. `current` symlink'i **atomik** olarak yeni release'e cevrilir.
6. `scripts/post-deploy.sh` calisir (bagimlilik, build, servis restart).
7. Bu adim hata verirse **otomatik olarak onceki release'e geri donulur**.
8. En son 5 release disindakiler silinir.

---

## 5. Uygulamaya ozel adimlar

`scripts/post-deploy.sh` yigini **kendisi tespit eder**; ayrica bir sey
yazmaniza gerek yok:

| Repoda bulunan dosya | Calisan adimlar |
|---|---|
| `package.json` | `npm ci --omit=dev`, `build` script'i varsa `npm run build` |
| `composer.json` | `composer install --no-dev -o`, `artisan` varsa `migrate --force` + cache |
| `requirements.txt` | `shared/venv` olusturur, `pip install -r requirements.txt` |
| hicbiri | Statik site kabul edilir, sadece dosyalar kopyalanir |

Tespiti ezmek isterseniz sunucudaki `shared/.env` icine `APP_STACK=node`
gibi bir satir yazin.

### Servisin yeniden baslatilmasi

Kod kopyalandiktan sonra uygulamanin yeni surumle calismasi icin
`shared/.env` dosyasina **birini** ekleyin:

```bash
APP_SERVICE=meluni      # systemd kullaniyorsaniz
# veya
PM2_APP=meluni          # PM2 kullaniyorsaniz
```

Ikisi de bossa deploy basarili sayilir ama servis eski kodu calistirmaya
devam eder; script bu durumda uyari basar. Statik sitelerde bu normaldir.

### Saglik kontrolu (onerilir)

```bash
HEALTH_URL=http://127.0.0.1:3000/
```

Bu satir varsa deploy sonrasi adres 15 kez denenir. Cevap vermezse
**release otomatik olarak geri alinir** ve site ayakta kalir.

### Parolasiz sudo

`APP_SERVICE` kullanacaksaniz deploy kullanicisina yetki verin.
Sunucuda `root` olarak:

```bash
echo '<KULLANICI> ALL=(root) NOPASSWD: /bin/systemctl restart meluni, /bin/systemctl reload meluni' \
  > /etc/sudoers.d/meluni-deploy
chmod 440 /etc/sudoers.d/meluni-deploy
visudo -c
```

---

## 6. Sunucuyu sikilastirin (onerilir)

Anahtarla giris calistigini **dogruladiktan sonra** sunucuda `/etc/ssh/sshd_config`:

```
PasswordAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
```

```bash
sudo sshd -t && sudo systemctl reload ssh
```

> Mevcut SSH oturumunuzu kapatmadan once **yeni bir terminalden** giris yapip
> kilitlenmediginizi dogrulayin.

Ek olarak `fail2ban` kurmak ve gereksiz portlari firewall'da kapatmak faydali:

```bash
sudo apt install -y fail2ban
sudo ufw allow OpenSSH && sudo ufw enable
```

---

## 7. Sorun giderme

| Belirti | Sebep / cozum |
|---|---|
| `Permission denied (publickey)` | Acik anahtar sunucudaki `authorized_keys` icinde degil, ya da izinler yanlis: `chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys` |
| `Host key verification failed` | `SSH_KNOWN_HOSTS` secret'i eksik/eski. `ssh-keyscan -p <port> -H <host>` ile yenileyin |
| `Eksik repository secret'lari` | Adim 3'teki zorunlu secret'lardan biri tanimli degil |
| Deploy gecti ama site eski | `scripts/post-deploy.sh` icinde servis restart adimi yok |
| `post-deploy basarisiz, onceki release'e donuluyor` | Hook hata dondu; Actions loglarindan hangi komutun patladigina bakin |
| Baglanti timeout | Sunucu firewall'u GitHub runner IP'lerini engelliyor olabilir; ya SSH portunu acin ya da self-hosted runner kullanin |
