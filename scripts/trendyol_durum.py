"""
trendyol_durum.py — Trendyol entegrasyon ayarını gösterir, gerekirse açar.

NEDEN: Ürün gönderimi, kategori özelliklerinin canlı çekilmesi ve otomatik
eşleştirme kurulumu — üçü de `settings.trendyol.is_active` bayrağına bakıyor.
Bayrak kapalıyken gönderim daha ilk satırda 400 dönüyor ve `trendyol_sync_logs`
koleksiyonuna HİÇBİR kayıt düşmüyor; panelde yalnız "0 başarı" görünüyor,
sebebi görünmüyor. Bu betik bayrağın ve anahtarların durumunu yazar.

Anahtar/gizli anahtar DEĞERLERİ basılmaz; yalnız var/yok ve son 4 hanesi.

Kullanım (sunucuda):
    cd ~/apps/meluni/current/backend
    ~/apps/meluni/shared/venv/bin/python ../scripts/trendyol_durum.py
    ~/apps/meluni/shared/venv/bin/python ../scripts/trendyol_durum.py --aktif-et
"""
import argparse
import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "backend"))


def _maske(v) -> str:
    v = str(v or "")
    if not v:
        return "YOK"
    return f"var (…{v[-4:]}, {len(v)} hane)"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aktif-et", action="store_true",
                    help="Entegrasyonu açar (is_active=True).")
    ap.add_argument("--canli", action="store_true",
                    help="Ortamı canlıya alır (mode=live).")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.getcwd(), ".env"))
    except Exception:
        pass
    from routes.deps import db                                  # noqa: E402
    from routes.integrations_trendyol import get_trendyol_config  # noqa: E402

    ham = await db.settings.find_one({"id": "trendyol"}, {"_id": 0}) or {}
    if not ham:
        print("settings.trendyol kaydı YOK — panelden Ayarlar > Trendyol'u bir kez kaydedin.")
        return 1

    cfg = await get_trendyol_config()
    print("kayıtlı ayar (settings.trendyol)")
    print(f"  is_active        : {ham.get('is_active')}")
    print(f"  mode (kayıtlı)   : {ham.get('mode')!r}")
    print(f"  supplier_id      : {ham.get('supplier_id') or 'YOK'}")
    print(f"  api_key          : {_maske(ham.get('api_key'))}")
    print(f"  api_secret       : {_maske(ham.get('api_secret'))}")
    print()
    print("uygulamanın kullandığı (get_trendyol_config)")
    print(f"  mode (çözülmüş)  : {cfg.get('mode')!r}   → {cfg.get('base_url')}")
    print(f"  api_secret çözüldü: {_maske(cfg.get('api_secret'))}")
    print()

    engel = []
    if not ham.get("is_active"):
        engel.append("is_active kapalı → ürün gönderimi 400 döner, "
                     "kategori özellikleri canlı çekilmez")
    if not str(ham.get("supplier_id") or "").strip():
        engel.append("supplier_id boş")
    if not str(ham.get("api_key") or "").strip():
        engel.append("api_key boş")
    if not str(cfg.get("api_secret") or "").strip():
        engel.append("api_secret boş ya da çözülemedi")
    if cfg.get("mode") != "live":
        engel.append(f"mode={cfg.get('mode')!r} → istekler TEST ağ geçidine gidiyor")

    if engel:
        print("ENGELLER:")
        for e in engel:
            print(f"  - {e}")
    else:
        print("Engel yok: entegrasyon açık, anahtarlar dolu, ortam canlı.")

    # MARKA — gönderim sırasıyla: üründeki trendyol_brand_id → Marka
    # Eşleştirme kaydı → ayarlardaki varsayılan. Hangisinin dolu olduğunu
    # görmeden "marka hatası"nın nerede kırıldığı anlaşılmıyor.
    print("marka")
    print(f"  ayarlardaki varsayılan : {ham.get('default_brand_id') or 'YOK'}")
    esleme = await db.brand_mappings.find({"marketplace": "trendyol"}, {"_id": 0}).to_list(50)
    if not esleme:
        print("  Marka Eşleştirme kaydı : YOK")
    for m in esleme:
        mid = m.get("marketplace_brand_id")
        sayisal = str(mid or "").strip().isdigit()
        print(f"  {str(m.get('brand_name') or '?')[:20]:<22} → "
              f"pazaryeri id={mid!r} "
              f"({'sayısal, kullanılabilir' if sayisal else 'SAYISAL DEĞİL — gönderimde kullanılamaz'})"
              f" · ad={m.get('marketplace_brand_name') or '-'}")
    urun_markalari = await db.products.distinct("brand", {"is_deleted": {"$ne": True}})
    print(f"  ürünlerdeki marka adları: {[str(x)[:18] for x in urun_markalari[:6]]}")
    print()

    guncelle = {}
    if args.aktif_et:
        guncelle["is_active"] = True
    if args.canli:
        guncelle["mode"] = "live"
    if guncelle:
        await db.settings.update_one({"id": "trendyol"}, {"$set": guncelle})
        print("\nyazıldı:", guncelle)
    elif engel:
        print("\nAçmak için: --aktif-et   (ortamı canlıya almak için: --canli)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
