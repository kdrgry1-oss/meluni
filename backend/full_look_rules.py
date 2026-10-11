"""Bounded content schema; never accept client-supplied prices or product snapshots."""
from urllib.parse import urlsplit


def gallery_images(images):
    """Legacy galleries contain both URL strings and image objects/size charts."""
    result = []
    for item in images if isinstance(images, list) else []:
        if isinstance(item, dict) and item.get('is_size_table'):
            continue
        url = item.get('url') if isinstance(item, dict) else item
        if not isinstance(url, str):
            continue
        url = url.strip()
        if url and '\\' not in url and (url.startswith('/') and not url.startswith('//') or urlsplit(url).scheme in ('https', 'http')):
            if url not in result:
                result.append(url)
    return result


def clean_full_look(payload):
    def text(value, limit):
        if not isinstance(value, str):
            raise ValueError('Metin alanı geçersiz.')
        return value.strip()[:limit]
    if not isinstance(payload, dict):
        raise ValueError('Sayfa verisi geçersiz.')
    revision = payload.get('revision', 0)
    if type(revision) is not int or revision < 0:
        raise ValueError('Sayfa sürümü geçersiz.')
    rows = payload.get('looks', [])
    if not isinstance(rows, list) or len(rows) > 50:
        raise ValueError('En fazla 50 kombin eklenebilir.')
    looks, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Kombin verisi geçersiz.')
        rid = text(row.get('id', ''), 80)
        if not rid or rid in seen:
            raise ValueError('Kombin kimliği boş veya mükerrer.')
        seen.add(rid)
        source = text(row.get('source_product_id', ''), 100)
        image = text(row.get('image', ''), 2000)
        if image and not gallery_images([image]):
            raise ValueError('Görsel adresi geçersiz.')
        ids = row.get('product_ids', [])
        if not isinstance(ids, list) or len(ids) > 8:
            raise ValueError('Bir kombine en fazla 8 ürün eklenebilir.')
        ids = [text(pid, 100) for pid in ids]
        if any(not pid for pid in ids) or len(ids) != len(set(ids)):
            raise ValueError('Ürünler boş veya mükerrer olamaz.')
        active = row.get('is_active') is True
        if active and (not source or not image or not ids):
            raise ValueError('Yayındaki kombin için kaynak ürün, fotoğraf ve en az bir ürün seçin.')
        looks.append({'id': rid, 'title': text(row.get('title', ''), 120),
                      'source_product_id': source, 'image': image,
                      'product_ids': ids, 'is_active': active})
    return {'title': text(payload.get('title', 'Full Look'), 120) or 'Full Look',
            'description': text(payload.get('description', ''), 500),
            'looks': looks, 'revision': revision}


def full_look_projection():
    # Site genelindeki ürün kartı (ProductCard) ile AYNI görünüm için varyant/stok/renk/rozet
    # alanları da gelir (kullanıcı isteği: indirim rozeti, sepet ikonu, bedenler vb.).
    return {k: 1 for k in ('id', 'name', 'slug', 'images', 'price', 'sale_price',
                           'category_id', 'category_ids', 'is_active', 'is_deleted',
                           'variants', 'stock', 'color', 'colors', 'stock_code', 'created_at',
                           'is_new', 'badge', 'tags', 'brand', 'campaign_discount_percent')}
