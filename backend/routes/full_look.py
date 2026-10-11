"""Shop-the-look content. Drafts/admin metadata never returned by the public route."""
import re
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from .deps import db, require_admin, require_permission
from activity_audit import record_admin_audit
from full_look_rules import clean_full_look, full_look_projection, gallery_images
from .products import _members_only_cat_ids, _auto_campaigns_for_badges, _apply_campaign_badge, _strip_internal_fields

router = APIRouter(prefix='/full-look', tags=['Full Look'])


async def _read():
    return await db.full_look_pages.find_one({'_id': 'main'}, {'_id': 0}) or {
        'title': 'Full Look', 'description': '', 'looks': [], 'revision': 0}


async def _products(ids, public=False):
    query = {'id': {'$in': list(ids)}, 'is_deleted': {'$ne': True}}
    if public:
        query['is_active'] = True
        restricted = list(await _members_only_cat_ids())
        if restricted:
            query['category_id'] = {'$nin': restricted}
            query['category_ids'] = {'$nin': restricted}
    docs = await db.products.find(query, {'_id': 0, **full_look_projection()}).to_list(450)
    campaigns = await _auto_campaigns_for_badges()
    for product in docs:
        product['images'] = gallery_images(product.get('images'))
        _apply_campaign_badge(product, campaigns)
        # GÜVENLİK: projeksiyon ürün kartı için 'variants' alır; varyantlarda alış fiyatı/üye
        # fiyatı/tedarikçi olabilir → public /products ile aynı süzgeç (kâr marjı sızmasın).
        _strip_internal_fields(product)
    return {p['id']: p for p in docs}


async def _hydrate(doc, public=False):
    rows = [r for r in doc.get('looks', []) if not public or r.get('is_active')]
    ids = {pid for r in rows for pid in [r.get('source_product_id'), *r.get('product_ids', [])] if pid}
    products = await _products(ids, public) if ids else {}
    result = []
    for row in rows:
        source = products.get(row.get('source_product_id'))
        cards = [products[pid] for pid in row.get('product_ids', []) if pid in products]
        strict_image = bool(source) and row.get('image') in source.get('images', [])
        # Yayın koşulu: kaynak ürün yayında (public sorguda süzüldü) + görsel var + en az bir görünür ürün.
        # Görselin kaynak ürünün GÜNCEL galerisinde olması artık şart değil (galeri değişince kombin
        # sessizce kaybolup "LOOK 02 çıkmıyor" oluyordu); görsel adresi kayıtta zaten doğrulanıyor.
        valid_image = bool(source) and bool(row.get('image'))
        if public and (not valid_image or not cards):
            continue
        if public:
            result.append({'id': row['id'], 'title': row.get('title', ''), 'image': row['image'], 'products': cards})
        else:
            result.append({**row, 'source_product': source, 'products': cards,
                           'warning': '' if strict_image and cards else 'Kaynak görseli ve ürünleri kontrol edin.'})
    out = {'title': doc.get('title', 'Full Look'), 'description': doc.get('description', ''), 'looks': result}
    if not public:
        out.update(revision=doc.get('revision', 0), updated_at=doc.get('updated_at'))
    return out


@router.get('')
async def public_full_look():
    category = await db.categories.find_one({'slug': 'full-look'}, {'_id': 0, 'is_active': 1, 'members_only': 1})
    if category and (category.get('is_active') is False or category.get('members_only')):
        return {'title': 'Full Look', 'description': '', 'looks': []}
    return await _hydrate(await _read(), public=True)


@router.get('/admin')
async def admin_full_look(current_user: dict = Depends(require_permission("tasarim.view"))):
    return await _hydrate(await _read())


@router.get('/products')
async def search_products(search: str = Query('', max_length=100), current_user: dict = Depends(require_permission("tasarim.view"))):
    query = {'is_deleted': {'$ne': True}, 'is_active': True}
    if search.strip():
        term = {'$regex': re.escape(search.strip()), '$options': 'i'}
        query['$or'] = [{'name': term}, {'stock_code': term}, {'id': search.strip()}]
    docs = await db.products.find(query, {'_id': 0, **full_look_projection()}).sort('updated_at', -1).to_list(20)
    campaigns = await _auto_campaigns_for_badges()
    for product in docs:
        product['images'] = gallery_images(product.get('images'))
        _apply_campaign_badge(product, campaigns)
    return {'products': docs}


@router.put('/admin')
async def save_full_look(payload: dict, request: Request, current_user: dict = Depends(require_permission("tasarim.page_design"))):
    try:
        clean = clean_full_look(payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    ids = {pid for r in clean['looks'] for pid in [r['source_product_id'], *r['product_ids']] if pid}
    products = await _products(ids) if ids else {}
    for row in clean['looks']:
        source = products.get(row['source_product_id'])
        if row['image'] and (not source or row['image'] not in source.get('images', [])):
            raise HTTPException(400, 'Seçilen fotoğraf kaynak ürünün galerisinde bulunamadı. Görseli yeniden seçin.')
        if row['is_active'] and (not source or source.get('is_active') is not True or
                any(pid not in products or products[pid].get('is_active') is not True for pid in row['product_ids'])):
            raise HTTPException(400, 'Yayındaki kombinlerde yalnız mevcut ve aktif ürünler kullanılabilir.')
    before = await _read()
    revision = clean['revision']
    clean.update(revision=revision + 1, updated_at=datetime.now(timezone.utc).isoformat())
    try:
        result = await db.full_look_pages.update_one({'_id': 'main', 'revision': revision}, {'$set': clean}, upsert=revision == 0)
    except Exception as error:
        if getattr(error, 'code', None) == 11000:
            raise HTTPException(409, 'Sayfa başka bir kullanıcı tarafından değiştirildi. Yenileyip tekrar deneyin.')
        raise
    if not result.matched_count and not result.upserted_id:
        raise HTTPException(409, 'Sayfa başka bir kullanıcı tarafından değiştirildi. Yenileyip tekrar deneyin.')
    await record_admin_audit(db, action='full_look.update', entity_type='content', entity_id='full_look',
                             before=before, after=clean, current_user=current_user, request=request, source='content.full_look')
    return await _hydrate(clean)
