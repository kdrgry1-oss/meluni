# meluni

Sunucuya SSH uzerinden otomatik deploy edilen proje deposu.

## Durum

Uygulama kodu henuz eklenmedi. Repoda su an sadece **deploy altyapisi** var:
sunucu baglantisi, surumleme (release) yapisi ve otomatik yayina alma.

## Icerik

| Yol | Aciklama |
|---|---|
| `.github/workflows/deploy.yml` | `main`'e push'ta SSH ile otomatik deploy |
| `scripts/deploy.sh` | Deploy'un kendisi; CI'dan da yerelden de calisir |
| `scripts/post-deploy.sh` | Sunucuda calisan build / migration / restart adimlari |
| `scripts/server-setup.sh` | Sunucuda bir kez calistirilan ilk kurulum |
| `docs/SUNUCU-ENTEGRASYON.md` | **Adim adim sunucu kurulum rehberi** |
| `docs/DNS-CLOUDFLARE.md` | Cloudflare DNS kayitlari, SSL ve yonlendirmeler |
| `.env.example` | Ortam degiskeni sablonu |

## Hizli baslangic

1. Deploy anahtarini uretin ve sunucuya tanitin.
2. GitHub'da `SSH_HOST`, `SSH_USER`, `SSH_PRIVATE_KEY`, `SSH_KNOWN_HOSTS`
   secret'larini tanimlayin.
3. `main` branch'ine push edin.

Detaylar: [`docs/SUNUCU-ENTEGRASYON.md`](docs/SUNUCU-ENTEGRASYON.md)

Alan adi ve yonlendirmeler: [`docs/DNS-CLOUDFLARE.md`](docs/DNS-CLOUDFLARE.md)

## Sunucudaki dizin yapisi

```
~/apps/meluni/
├── current -> releases/<en-son>   # aktif surum
├── releases/                      # son 5 surum (geri donus icin)
├── shared/.env                    # gercek gizli degerler, repoya girmez
└── tmp/
```

Deploy sirasinda `post-deploy.sh` hata verirse `current` otomatik olarak
bir onceki release'e geri doner.

## Guvenlik

- Parola, anahtar, token gibi hicbir gizli deger repoya commit edilmez.
- Sunucu erisimi parola ile degil, deploy'a ozel SSH anahtari ile yapilir.
- Gercek ortam degiskenleri yalnizca sunucudaki `shared/.env` dosyasinda durur.
