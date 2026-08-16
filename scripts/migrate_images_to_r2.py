"""
migrate_images_to_r2.py — Ürün görsellerini R2'ye (cdn.meluni.com.tr) taşır.

NEDEN: Görsellerimizin bir kısmı API sunucusundan veriliyor
(api.meluni.com.tr/api/upload/files/...), bir kısmı R2'den
(cdn.meluni.com.tr/...). Trendyol'un görsel indiricisi API sunucusundan
ÇEKEMİYOR — canlıda kanıtlandı: aynı üründe R2'deki ölçü tablosu Trendyol'a
oturdu, API'deki ürün fotoğrafları oturmadı. R2 (Cloudflare) statik ve
korumasız servis ettiği için sorunsuz.

Betik ürün görsellerini indirip R2'ye yükler ve ürünün `images` listesindeki
adresi CDN adresiyle değiştirir. Dosya kaydına da r2_url yazılır, böylece eski
adres istense bile CDN'e yönlendirilir — mevcut bağlantılar kırılmaz.

`--tedarikci` ile TEDARİKÇİ sunucusundaki görseller de taşınır. Katalogda
15.526 görsel hâlâ modayakamoz.com'dan veriliyor ve 4.383'ü webp; Trendyol
webp kabul etmiyor, bu yüzden kopyalarken JPEG'e çevriliyor. Yapay zekâ
GEREKTİRMEZ (kalite artmaz, yalnız barındırma ve biçim düzelir) — görsel
iyileştirme kotası bittiğinde tek çıkış yolu budur.

Tedarikçi görseli taşınan üründe `images_custom` işaretlenir; aksi hâlde
ertesi günkü tedarikçi aktarımı adresleri eski dış adreslere geri yazar ve
taşıma boşa gider.

R2 kapalıysa (R2_* ortam değişkenleri yoksa) hiçbir şey yapmaz.

Kullanım (sunucuda):
    cd ~/apps/meluni/current/backend
    ~/apps/meluni/shared/venv/bin/python ../scripts/migrate_images_to_r2.py
    ~/apps/meluni/shared/venv/bin/python ../scripts/migrate_images_to_r2.py --uygula
    ... --uygula --kart MLN-69fb192da020b     # yalnız bir ürün kartı
    ... --uygula --kategori takım
    ... --uygula --tedarikci --kategori pantolon   # tedarikçi görselleri de
"""
import argparse
import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "backend"))

_API_YOLLARI = ("/api/upload/files/", "/api/files/")
# Zaten CDN'de olan görsel yeniden taşınmaz.
_CDN_IZLERI = ("cdn.meluni.com.tr",)


def _url(x):
    return (x.get("url") if isinstance(x, dict) else x) or ""


def _api_gorseli_mi(u: str) -> bool:
    return any(y in str(u) for y in _API_YOLLARI)


def _cdn_de_mi(u: str) -> bool:
    return any(y in str(u) for y in _CDN_IZLERI)


def _tedarikci_gorseli_mi(u: str) -> bool:
    """Dış (tedarikçi) sunucuda duran görsel."""
    u = str(u or "")
    return u.startswith("http") and not _cdn_de_mi(u) and not _api_gorseli_mi(u)


def _jpege_cevir(ham: bytes, tur: str):
    """webp/png → JPEG. (ham, tur, uzanti) döndürür.

    Trendyol webp KABUL ETMİYOR; kataloğun 4.383 görseli webp ve tedarikçi
    sunucusundan geliyor. Kopyalarken çevirmezsek adres CDN'e taşınmış olur
    ama görsel yine reddedilir.
    """
    if "webp" not in tur and "png" not in tur:
        return ham, tur, "jpg" if "jpeg" in tur or "jpg" in tur else "jpg"
    try:
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(ham))
        if im.mode not in ("RGB",):
            im = im.convert("RGB")
        tampon = io.BytesIO()
        im.save(tampon, format="JPEG", quality=90, optimize=True)
        return tampon.getvalue(), "image/jpeg", "jpg"
    except Exception:
        # Çeviremediysek olduğu gibi taşı — adres yine de CDN'e geçsin.
        return ham, tur, ("webp" if "webp" in tur else "png")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uygula", action="store_true", help="Taşımayı YAPAR.")
    ap.add_argument("--kart", default="", help="Yalnız bu ürün kartı kimliği.")
    ap.add_argument("--kategori", default="", help="Yalnız bu kategori.")
    ap.add_argument("--limit", type=int, default=0, help="En fazla N ürün.")
    ap.add_argument("--tedarikci", action="store_true",
                    help="Tedarikçi sunucusundaki görselleri de taşır "
                         "(webp/png → JPEG çevrilir).")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.getcwd(), ".env"))
    except Exception:
        pass
    from routes.deps import db                       # noqa: E402
    from services import r2_storage as r2            # noqa: E402
    import httpx                                     # noqa: E402

    if not r2.is_enabled():
        print("R2 kapalı (R2_ACCOUNT_ID / R2_BUCKET / R2_PUBLIC_URL eksik).")
        print("Ortam değişkenleri tanımlanmadan taşıma yapılamaz.")
        return 2

    sorgu = {"is_deleted": {"$ne": True}}
    if args.kart:
        sorgu["$or"] = [{"urun_karti_id": args.kart}, {"csv_card_id": args.kart}]
    elif args.kategori:
        sorgu["$or"] = [
            {"category_name": {"$regex": args.kategori, "$options": "i"}},
            {"category_path": {"$regex": args.kategori, "$options": "i"}}]

    taranan = tasinan = urun_degisen = hata = 0
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as istemci:
        async for p in db.products.find(sorgu, {"_id": 0, "id": 1, "name": 1, "images": 1}):
            if args.limit and taranan >= args.limit:
                break
            taranan += 1
            gorseller = list(p.get("images") or [])
            def _tasinsin(u: str) -> bool:
                return _api_gorseli_mi(u) or (args.tedarikci and _tedarikci_gorseli_mi(u))

            hedefler = [i for i, x in enumerate(gorseller) if _tasinsin(_url(x))]
            if not hedefler:
                continue
            print(f"  {p.get('name','')[:44]:<46} {len(hedefler)} görsel")
            if not args.uygula:
                urun_degisen += 1
                tasinan += len(hedefler)
                continue

            degisti = False
            tedarikci_tasindi = False
            for i in hedefler:
                eski = _url(gorseller[i])
                try:
                    r = await istemci.get(eski)
                    r.raise_for_status()
                    ham = r.content
                    tur = r.headers.get("content-type", "image/jpeg").split(";")[0]
                except Exception as e:
                    hata += 1
                    print(f"      indirilemedi: {str(e)[:70]}")
                    continue
                ham, tur, uzanti = _jpege_cevir(ham, tur)
                ad = eski.rstrip("/").split("/")[-1].split("?")[0]
                ad = (ad.rsplit(".", 1)[0] if "." in ad else ad) + "." + uzanti
                anahtar = f"products/{p['id']}/{ad}"
                try:
                    yeni = r2.put_object(anahtar, ham, tur)
                except Exception as e:
                    hata += 1
                    print(f"      R2'ye yüklenemedi: {str(e)[:70]}")
                    continue
                gorseller[i] = ({**gorseller[i], "url": yeni}
                                if isinstance(gorseller[i], dict) else yeni)
                tasinan += 1
                degisti = True
                if _tedarikci_gorseli_mi(eski):
                    tedarikci_tasindi = True
                # Eski adres istenirse CDN'e yönlensin (bağlantılar kırılmasın).
                # Yalnız BİZİM dosya kaydımız için: tedarikçi dosya adının
                # regex'i alakasız kayıtlara çarpabilir.
                if _api_gorseli_mi(eski):
                    try:
                        _dosya = ad.rsplit(".", 1)[0]
                        await db.files.update_many(
                            {"storage_path": {"$regex": _dosya}},
                            {"$set": {"r2_url": yeni, "r2_key": anahtar}})
                    except Exception:
                        pass
            if degisti:
                urun_degisen += 1
                yazilacak = {"images": gorseller}
                # KİLİT: tedarikçi görselini kendi kovamıza kopyaladıysak,
                # ertesi günkü tedarikçi aktarımı `images`i eski dış adreslere
                # geri yazar ve taşıma boşa gider. Kopya bizim olduğu an ürün
                # görselleri "özel" sayılmalı.
                if tedarikci_tasindi:
                    yazilacak["images_custom"] = True
                await db.products.update_one({"id": p["id"]}, {"$set": yazilacak})

    print()
    print(f"taranan ürün           : {taranan}")
    print(f"{'taşınan görsel' if args.uygula else 'taşınacak görsel'}       : {tasinan}")
    print(f"{'güncellenen ürün' if args.uygula else 'güncellenecek ürün'}     : {urun_degisen}")
    if hata:
        print(f"hata                   : {hata}")
    if not args.uygula:
        print("\nHiçbir şey yazılmadı. Uygulamak için: --uygula")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
