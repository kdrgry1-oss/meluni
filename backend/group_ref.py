"""
group_ref.py — Renk-kardeşi grup kodunun geçerliliği.

NEDEN: Renk kardeşleri `urun_karti_id` (tedarikçi beslemesindeki grup kodu)
üzerinden bağlanıyor. Besleme bu alanı bazı üründe EKSİK gönderiyor: değer
yalnız önekten ibaret kalıyor ("MLN-"). Kod bunu geçerli bir kimlik sanıp
olduğu gibi yazınca, o değeri taşıyan BÜTÜN ürünler tek gruba düşüyor ve
birbirinin renk kardeşi görünüyor — tulumun altında bluz, elbise, takım
çıkıyor. Canlıda yaşandı.

KURAL: grup kodu, ayırıcılar atıldıktan sonra en az 4 harf/rakam taşımalı ve
tek karakterin tekrarı olmamalı. Taşımıyorsa KİMLİK DEĞİLDİR — boş sayılır.
Boş grup kodunda ürün kendi başına kalır; yanlış kardeş göstermektense hiç
kardeş göstermemek doğrudur.
"""
from __future__ import annotations

import re

_SADE = re.compile(r"[^0-9A-Za-zÇĞİÖŞÜçğıöşü]+")


def gecerli_grup(deger) -> bool:
    """Grup kodu gerçek bir kimlik mi?"""
    ozet = _SADE.sub("", str(deger or ""))
    if len(ozet) < 4:
        return False
    return len(set(ozet.lower())) > 1        # "0000", "----" gibi doldurmalar


def temiz_grup(deger) -> str:
    """Geçerliyse kırpılmış kodu, değilse boş dize döner."""
    d = str(deger or "").strip()
    return d if gecerli_grup(d) else ""
