"""Run the real orchestrator/math without external DB or coupon redemption writes."""
import ast
import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def evaluate(coupons, entered='WELCOME', excluded=None, cap=70, items=None):
    source = Path(__file__).parents[1] / 'routes/coupons.py'
    names = {'evaluate_cart_promotions', '_compute_discount', 'fold_code',
             '_item_category_set', '_item_in_scope', '_allocate_discount'}
    nodes = [n for n in ast.parse(source.read_text()).body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    assert len(nodes) == len(names)
    class Cursor:
        def __aiter__(self):
            async def rows():
                for c in coupons:
                    if c.get('auto_apply'):
                        yield c
            return rows()
    db = SimpleNamespace(settings=SimpleNamespace(find_one=AsyncMock(return_value={})),
                         coupons=SimpleNamespace(find=lambda *a: Cursor()))
    scope = {'db': db, 're': re, '_promo_cap_pct': AsyncMock(return_value=cap),
             '_log_coupon_attempt': AsyncMock()}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), scope)
    async def enrich(value):
        return value
    async def validate(c, total, lines, *args):
        return {'valid': True, 'discount': scope['_compute_discount'](c, total, lines),
                'free_shipping': c.get('free_shipping', False)}
    async def resolve(code):
        c = next((c for c in coupons if c['code'] == code), None)
        return c, code
    scope.update(_enrich_items_category_ids=enrich, _evaluate_single=validate, resolve_coupon_code=resolve)
    _items = items or [{'product_id': 'p', 'price': 3790, 'qty': 1}]
    return asyncio.run(scope['evaluate_cart_promotions'](
        round(sum(i['price'] * i['qty'] for i in _items), 2), _items,
        entered_code=entered, excluded_ids=excluded))


def campaign(cid, value, **kwargs):
    return {'id': cid, 'code': cid, 'type': 'percent', 'value': value,
            'auto_apply': True, 'combinable': True, **kwargs}


def pair(**welcome):
    return [campaign('LAUNCH', 20), campaign('WELCOME', 10, first_order_only=True, **welcome)]


@pytest.mark.parametrize('entered', ['WELCOME', ''])
def test_launch_before_welcome_even_when_welcome_has_higher_priority(entered):
    r = evaluate(pair(priority=100), entered)
    assert [(a['code'], a['discount']) for a in r['applied']] == [('LAUNCH', 758), ('WELCOME', 303.2)]
    assert r['total_discount'] == 1061.2
    net = round(3790 - r['total_discount'], 2)
    bank = round(net * .05, 2)
    assert bank == 136.44
    assert round(net - bank + 99, 2) == 2691.36


def test_entered_coupon_still_wins_noncombinable_selection():
    r = evaluate(pair(combinable=False))
    assert [(a['code'], a['discount']) for a in r['applied']] == [('WELCOME', 379)]
    assert not r['rejected']


def test_one_sided_permission_and_group_exclusion_preserved():
    coupons = pair(combinable_with=['LAUNCH'])
    assert len(evaluate(coupons)['applied']) == 2
    for c in coupons:
        c['stack_group'] = 'only-one'
    assert [a['code'] for a in evaluate(coupons)['applied']] == ['WELCOME']


def test_removed_campaign_not_subtracted_and_zero_shipping_processed_last():
    coupons = pair() + [campaign('SHIPPING', 0, free_shipping=True, min_cart_total=3000, priority=999)]
    assert [a['code'] for a in evaluate(coupons)['applied']] == ['LAUNCH', 'WELCOME']
    r = evaluate(coupons, excluded=['LAUNCH'])
    assert [(a['code'], a['discount']) for a in r['applied']] == [('WELCOME', 379), ('SHIPPING', 0)]


def test_scoped_campaign_then_coupon_and_cap_reconcile():
    coupons = pair()
    coupons[0]['products'] = ['p']
    lines = [{'product_id': 'p', 'price': 2000, 'qty': 1}, {'product_id': 'q', 'price': 1790, 'qty': 1}]
    r = evaluate(coupons, items=lines)
    assert [a['discount'] for a in r['applied']] == [400, 339]
    capped = evaluate(coupons, cap=10, items=lines)
    assert capped['capped']
    assert round(sum(a['discount'] for a in capped['applied']), 2) == capped['total_discount'] == 379


@pytest.mark.parametrize('prio', [10, 0])
def test_3al2_with_scoped_percent_free_item_fully_free(prio):
    """3 AL 2 ÖDE (Body) + Lansman %20 (yalnız Elen): sıra ne olursa olsun en ucuz ürün TAM
    bedava, %20 yalnız Elen'in tam fiyatından. Eski global ölçekleme 690+231,02 veriyordu."""
    coupons = [
        {'id': 'B3', 'code': 'B3', 'type': 'nth_discount', 'buy_quantity': 3, 'free_quantity': 1,
         'get_discount': 100, 'categories': ['5378'], 'skip_discounted': False,
         'auto_apply': True, 'combinable': True, 'priority': prio},
        campaign('LAUNCH', 20, categories=['2867']),
    ]
    lines = [{'product_id': 'elen', 'price': 1490, 'qty': 1, 'category_ids': ['5378', '2867']},
             {'product_id': 'body', 'price': 890, 'qty': 1, 'category_ids': ['5378']},
             {'product_id': 'bluz', 'price': 690, 'qty': 1, 'category_ids': ['5378']}]
    r = evaluate(coupons, entered='', items=lines)
    got = {a['code']: a['discount'] for a in r['applied']}
    assert got == {'B3': 690, 'LAUNCH': 298}
    assert r['total_discount'] == 988


def test_3al2_six_units_two_cheapest_free_then_cartwide_percent():
    coupons = [
        {'id': 'B3', 'code': 'B3', 'type': 'nth_discount', 'buy_quantity': 3, 'free_quantity': 1,
         'get_discount': 100, 'categories': ['5378'], 'skip_discounted': False,
         'auto_apply': True, 'combinable': True, 'priority': 10},
        campaign('ALL10', 10),
    ]
    lines = [{'product_id': 'a', 'price': 890, 'qty': 3, 'category_ids': ['5378']},
             {'product_id': 'b', 'price': 690, 'qty': 3, 'category_ids': ['5378']}]
    r = evaluate(coupons, entered='', items=lines)
    got = {a['code']: a['discount'] for a in r['applied']}
    assert got['B3'] == 1380                       # 2 × 690 bedava
    assert got['ALL10'] == round((4740 - 1380) * .10, 2)   # kalan tutarın %10'u


def test_two_nth_on_multi_qty_line_per_unit():
    """4 × aynı ürün; iki örtüşen 5-al-3 kampanyası: bedava birim bir kez bedava olur,
    ikinci kampanya aynı birimleri tekrar bedava yapamaz (satır-düzeyi yayma hatası)."""
    nth = lambda cid, cats: {'id': cid, 'code': cid, 'type': 'nth_discount', 'buy_quantity': 3,
                             'free_quantity': 1, 'get_discount': 100, 'categories': cats,
                             'skip_discounted': False, 'auto_apply': True, 'combinable': True}
    lines = [{'product_id': 'p', 'price': 500, 'qty': 4, 'category_ids': ['c1', 'c2']}]
    r = evaluate([nth('A', ['c1']), nth('B', ['c2'])], entered='', items=lines)
    got = sorted(a['discount'] for a in r['applied'])
    # A: 4 birimden 1 bedava (500). Bedava birim B'de sayılmaz; kalan 3 ÜCRETLİ birimden
    # B 1'ini bedava yapar (500). Aynı birim iki kez bedava/indirimli OLMAZ.
    assert got == [500, 500]
    assert r['total_discount'] == 1000


def test_nth_and_percent_same_total_any_priority_tie():
    """Eşit öncelikte nth önce hesaplanır → sonuç kampanya id/tutar eşitlik bozucusuna bağlı değil."""
    nth = {'id': 'zz-nth', 'code': 'N', 'type': 'nth_discount', 'buy_quantity': 5, 'free_quantity': 2,
           'get_discount': 100, 'categories': ['c1'], 'skip_discounted': False,
           'auto_apply': True, 'combinable': True}
    pct = campaign('aa-pct', 20, products=['p0', 'p1'])
    lines = [{'product_id': 'p0', 'price': 1632.08, 'qty': 4, 'category_ids': ['c1']},
             {'product_id': 'p1', 'price': 900, 'qty': 1, 'category_ids': ['c1']}]
    r = evaluate([pct, nth], entered='', items=lines)
    assert [a['code'] for a in r['applied']] == ['N', 'aa-pct']
    # 5 birim: en ucuz 2 (900 + 1632,08) bedava; %20 kalan 3 × 1632,08 üzerinden
    assert {a['code']: a['discount'] for a in r['applied']} == {'N': 2532.08, 'aa-pct': 979.25}


def test_percent_then_nth_then_nth_no_fractional_leftover():
    """%12 → 3 al 2 öde → 2. ürüne %50 (hepsi sepet-geneli): bedava birimde kuruş-altı artık
    kalıp sonraki kampanyada 'en ucuz' seçilmemeli (denetim #389)."""
    pct = campaign('P12', 12, priority=30)
    n1 = {'id': 'N1', 'code': 'N1', 'type': 'nth_discount', 'buy_quantity': 3, 'free_quantity': 1,
          'get_discount': 100, 'auto_apply': True, 'combinable': True, 'priority': 20}
    n2 = {'id': 'N2', 'code': 'N2', 'type': 'nth_discount', 'buy_quantity': 2, 'free_quantity': 1,
          'get_discount': 50, 'auto_apply': True, 'combinable': True, 'priority': 10}
    lines = [{'product_id': 'a', 'price': 333.33, 'qty': 3}, {'product_id': 'b', 'price': 777.77, 'qty': 2}]
    r = evaluate([pct, n1, n2], entered='', items=lines)
    got = {a['code']: a['discount'] for a in r['applied']}
    a12, b12 = 333.33 * .88, 777.77 * .88
    assert got['N1'] == round(a12, 2)                       # 5 birimden en ucuz 1 bedava
    # kalan 4 ücretli birim: 2 × a, 2 × b → 2 grup → en ucuz 2 birime (a, a) %50
    assert abs(got['N2'] - round(a12, 2)) <= 0.01
