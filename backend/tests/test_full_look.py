import ast
import asyncio
import copy
import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import Depends, HTTPException, Query, Request
from full_look_rules import clean_full_look, full_look_projection, gallery_images


def page(active=True):
    return {'title': 'Full Look', 'description': 'Kombinler', 'revision': 0, 'looks': [
        {'id': 'look1', 'title': 'Takım', 'source_product_id': 'jacket', 'image': '/jacket-2.jpg',
         'product_ids': ['jacket', 'pants'], 'is_active': active}]}


def product(pid):
    return {'id': pid, 'name': pid, 'is_active': True, 'price': 1000, 'images': [f'/{pid}.jpg', f'/{pid}-2.jpg']}


def _real_strip_internal_fields():
    """routes/products.py'deki GERÇEK public süzgeci (DB/route importu olmadan) yükler."""
    path = Path(__file__).parents[1] / 'routes/products.py'
    keep = {'_PRODUCT_INTERNAL_FIELDS', '_strip_internal_fields'}
    nodes = [n for n in ast.parse(path.read_text()).body
             if (isinstance(n, ast.FunctionDef) and n.name in keep)
             or (isinstance(n, ast.Assign) and any(getattr(t, 'id', None) in keep for t in n.targets))]
    ns = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), ns)
    return ns['_strip_internal_fields']


def scope():
    db = SimpleNamespace(full_look_pages=SimpleNamespace(find_one=AsyncMock(return_value=None),
                         update_one=AsyncMock(return_value=SimpleNamespace(matched_count=0, upserted_id='main'))),
                         categories=SimpleNamespace(find_one=AsyncMock(return_value={'is_active': True})))
    ns = dict(db=db, re=re, datetime=datetime, timezone=timezone, HTTPException=HTTPException,
              Request=Request, Query=Query, Depends=Depends, require_admin=lambda: None,
              require_permission=lambda key: lambda: None,
              clean_full_look=clean_full_look, full_look_projection=full_look_projection, gallery_images=gallery_images,
              record_admin_audit=AsyncMock(), _members_only_cat_ids=AsyncMock(return_value={'private'}),
              _auto_campaigns_for_badges=AsyncMock(return_value=[]), _apply_campaign_badge=Mock(),
              _strip_internal_fields=_real_strip_internal_fields())
    path = Path(__file__).parents[1] / 'routes/full_look.py'
    nodes = [n for n in ast.parse(path.read_text()).body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for n in nodes:
        n.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), ns)
    return ns, db


def run(awaitable):
    return asyncio.run(awaitable)


def test_only_content_ids_saved_no_price_snapshots():
    data = page()
    data['looks'][0].update(products=[{'id': 'jacket', 'price': 1}], price=1, source_product={'cost_price': 1})
    result = clean_full_look(data)
    assert set(result['looks'][0]) == {'id', 'title', 'source_product_id', 'image', 'product_ids', 'is_active'}
    # 6db4976c: kombin parçaları site geneli ProductCard ile çizilir → 'variants' bilerek projeksiyonda.
    # Koruma niyeti (maliyet/tedarikçi sızmasın) üst düzey projeksiyonda + varyant süzgecinde korunur
    # (bkz. test_public_query_excludes_private_inactive_deleted_products_and_refreshes_prices).
    assert not {'cost_price', 'purchase_price', 'supplier', 'internal_notes', 'member_price',
                'member_price_1', 'supplier_code', 'admin_notes'} & full_look_projection().keys()


def test_mixed_gallery_formats_normalized_without_size_charts_or_invalid_urls():
    assert gallery_images(['/front.jpg', {'url': '/outfit.jpg'}, {'url': '/chart.jpg', 'is_size_table': True},
                           None, 'javascript:alert(1)', '//evil.test', '/front.jpg']) == ['/front.jpg', '/outfit.jpg']


@pytest.mark.parametrize('change', [
    {'source_product_id': ''}, {'image': ''}, {'product_ids': []},
    {'image': 'javascript:alert(1)'}, {'image': '//evil.test/a'},
    {'product_ids': ['pants', 'pants']}, {'product_ids': [{'id': 'pants'}]},
    {'product_ids': [str(i) for i in range(9)]},
])
def test_invalid_published_content_rejected(change):
    data = page(); data['looks'][0].update(change)
    with pytest.raises(ValueError): clean_full_look(data)


def test_empty_drafts_and_limits():
    data = page(False); data['looks'][0].update(source_product_id='', image='', product_ids=[])
    assert not clean_full_look(data)['looks'][0]['is_active']
    for bad in [dict(data, revision=True), dict(data, revision=-1), dict(data, looks=data['looks'] * 51), dict(data, looks=data['looks'] * 2)]:
        with pytest.raises(ValueError): clean_full_look(bad)


def test_public_hides_drafts_missing_source_gallery_and_admin_metadata():
    ns, _ = scope()
    products = {'jacket': product('jacket'), 'pants': product('pants')}
    ns['_products'] = AsyncMock(return_value=products)
    data = page(); data['looks'].append(dict(data['looks'][0], id='draft', is_active=False))
    result = run(ns['_hydrate'](data, public=True))
    assert len(result['looks']) == 1
    assert [p['id'] for p in result['looks'][0]['products']] == ['jacket', 'pants']
    assert set(result['looks'][0]) == {'id', 'title', 'image', 'products'}
    assert 'revision' not in result
    # 05f96caf: görsel kaynak ürünün GÜNCEL galerisinden çıkınca kombin sessizce gizlenmez
    # ("LOOK 02 çıkmıyor" regresyonu) — görsel adresi kayıtta zaten doğrulanır.
    products['jacket']['images'] = []
    assert len(run(ns['_hydrate'](data, public=True))['looks']) == 1
    # Kaynak ürün yayından kalkarsa (public sorgu süzer) kombin gizlenir.
    ns['_products'].return_value = {'pants': product('pants')}
    assert run(ns['_hydrate'](data, public=True))['looks'] == []
    # Görsel yoksa ya da görünür ürün kalmadıysa kombin gizlenir.
    ns['_products'].return_value = {'jacket': product('jacket')}
    assert run(ns['_hydrate'](dict(data, looks=[dict(data['looks'][0], image='')]), public=True))['looks'] == []
    ns['_products'].return_value = {'jacket': product('jacket'), 'pants': product('pants')}
    assert run(ns['_hydrate'](dict(data, looks=[dict(data['looks'][0], product_ids=['gone'])]), public=True))['looks'] == []


def test_public_query_excludes_private_inactive_deleted_products_and_refreshes_prices():
    ns, db = scope()
    leaky = dict(product('jacket'), purchase_price=400, variants=[
        {'size': 'M', 'stock': 3, 'purchase_price': 400, 'cost_price': 380, 'member_price_1': 900, 'supplier': 'x'}])
    cursor = SimpleNamespace(to_list=AsyncMock(return_value=[leaky]))
    db.products = SimpleNamespace(find=Mock(return_value=cursor))
    result = run(ns['_products'](['jacket'], True))
    query, projection = db.products.find.call_args.args
    assert query['is_active'] is True and query['is_deleted'] == {'$ne': True}
    assert query['category_id'] == query['category_ids'] == {'$nin': ['private']}
    assert projection['price'] == 1 and 'cost_price' not in projection
    ns['_apply_campaign_badge'].assert_called_once()
    assert result['jacket']['price'] == 1000
    # Varyant iç/ticari alanları (alış/maliyet/üye fiyatı/tedarikçi) public yanıta sızmaz.
    assert 'purchase_price' not in result['jacket']
    assert result['jacket']['variants'] == [{'size': 'M', 'stock': 3}]


def test_inactive_full_look_category_hides_content():
    ns, db = scope(); db.categories.find_one.return_value = {'is_active': False}
    ns['_hydrate'] = AsyncMock()
    assert run(ns['public_full_look']())['looks'] == []
    ns['_hydrate'].assert_not_awaited()


@pytest.mark.parametrize('mutation', ['foreign_image', 'inactive', 'missing_product'])
def test_invalid_gallery_or_product_prevents_any_save(mutation):
    ns, db = scope(); data = page()
    products = {'jacket': product('jacket'), 'pants': product('pants')}
    if mutation == 'foreign_image': data['looks'][0]['image'] = '/unrelated.jpg'
    if mutation == 'inactive': products['pants']['is_active'] = False
    if mutation == 'missing_product': del products['pants']
    ns['_products'] = AsyncMock(return_value=products)
    with pytest.raises(HTTPException) as err: run(ns['save_full_look'](data, None, {'id': 'admin'}))
    assert err.value.status_code == 400
    db.full_look_pages.update_one.assert_not_awaited()


def test_save_uses_revision_check_audit_and_current_product_values():
    ns, db = scope(); data = page()
    ns['_products'] = AsyncMock(return_value={'jacket': product('jacket'), 'pants': product('pants')})
    result = run(ns['save_full_look'](data, None, {'id': 'admin'}))
    assert result['revision'] == 1 and result['looks'][0]['products'][0]['price'] == 1000
    args = db.full_look_pages.update_one.call_args
    assert args.args[0] == {'_id': 'main', 'revision': 0}
    assert 'products' not in args.args[1]['$set']['looks'][0]
    ns['record_admin_audit'].assert_awaited_once()


@pytest.mark.parametrize('initial', [True, False])
def test_conflicting_save_returns_409_never_overwrites(initial):
    ns, db = scope(); data = page(False); data['looks'] = []
    if initial:
        class DuplicateKey(Exception): code = 11000
        db.full_look_pages.update_one.side_effect = DuplicateKey()
    else:
        data['revision'] = 3
        db.full_look_pages.update_one.return_value = SimpleNamespace(matched_count=0, upserted_id=None)
    with pytest.raises(HTTPException) as err: run(ns['save_full_look'](data, None, {'id': 'admin'}))
    assert err.value.status_code == 409
    ns['record_admin_audit'].assert_not_awaited()


def test_all_management_routes_require_admin_auth():
    # 5fb9d812: ayrı 'tasarim.page_design' yetkisi bilerek kaldırıldı — giriş yapmış her yönetici
    # görüntüler/düzenler. Koruma niyeti: yönetim uçları ASLA kimliksiz/müşteri erişimine açık değil.
    source = (Path(__file__).parents[1] / 'routes/full_look.py').read_text()
    tree = ast.parse(source)
    seen = set()
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name in {'admin_full_look', 'save_full_look', 'search_products'}:
            seen.add(node.name)
            args = ast.unparse(node.args)
            assert 'Depends(require_admin)' in args or 'Depends(require_permission(' in args, node.name
    assert seen == {'admin_full_look', 'save_full_look', 'search_products'}
