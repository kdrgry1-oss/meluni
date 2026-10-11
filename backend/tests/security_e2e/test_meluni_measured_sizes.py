from conftest import kv_run


def test_estimated_body_sizes_are_not_published(kv_client, kv_db):
    kv_run(kv_db.size_tables.insert_one({'product_id':'estimated','source':'tahmin','sizes':['S'],'columns':['Göğüs'],'values':{'S':{'Göğüs':'88'}}}))
    response=kv_client.get('/api/size-tables-public/estimated')
    assert response.status_code==200
    assert response.json()=={'exists':False,'unverified':True}


def test_verified_product_measurements_remain_available(kv_client, kv_db):
    table={'product_id':'measured','source':'ocr','sizes':['S'],'columns':['Göğüs'],'values':{'S':{'Göğüs':'88'}}}
    kv_run(kv_db.size_tables.insert_one(table))
    response=kv_client.get('/api/size-tables-public/measured')
    assert response.status_code==200
    assert response.json()['exists'] is True
    assert response.json()['values']==table['values']


def test_automatic_table_does_not_invent_garment_measurements(kv_client, kv_db, monkeypatch):
    from routes import ai_product_images as images
    async def no_measurements(_pid):
        return {'olcu_adlari':[],'satirlar':[],'kaynak':''}
    monkeypatch.setattr(images,'_kredisiz_olcu_verisi',no_measurements)
    kv_run(kv_db.products.insert_one({'id':'no-table','name':'Kazak','description':'Göğüs: 90 Bel: 72 Kalça: 100. M/38 bedendir.','variants':[{'size':'S'},{'size':'M'},{'size':'L'}],'images':[]}))
    result=kv_run(images._olcu_otomatik_urun('no-table',gorsel=False,tahmin=True))
    assert result['durum']=='veri_yok'
    assert result['veri_yazildi'] is False
    assert kv_run(kv_db.size_tables.count_documents({'product_id':'no-table'}))==0


def test_missing_xml_variants_are_hidden_without_deleting_marketplace_identity(kv_client, kv_db):
    variants=[{'id':'real','size':'STANDART','stock':8,'barcode':'real-code'},
              {'id':'invented','size':'XL','stock':0,'barcode':'marketplace-code','feed_missing':True}]
    kv_run(kv_db.products.insert_one({'id':'xml-product','name':'Kazak','slug':'xml-product','is_active':True,'price':100,'stock':8,'variants':variants}))
    response=kv_client.get('/api/products/xml-product')
    assert response.status_code==200,response.text
    assert [v['size'] for v in response.json()['variants']]==['STANDART']
    stored=kv_run(kv_db.products.find_one({'id':'xml-product'}))
    assert len(stored['variants'])==2
    assert stored['variants'][1]['barcode']=='marketplace-code'


def test_single_remaining_xml_size_cannot_supply_discontinued_sizes(kv_client):
    from routes.supplier_xml import besleme_haritasi, varyant_bul
    source={'supplier_barcode':'SKU-S-M','supplier_stock_code':'SKU','size':'S-M','color':'Siyah','stock':9,'ref':'SKU-S-M'}
    mapping=besleme_haritasi([source],['real-code'])
    assert varyant_bul({'supplier_barcode':'SKU-S-M','size':'S-M','color':'Siyah'},mapping) is source
    assert varyant_bul({'supplier_barcode':'SKU-L-XL','supplier_stock_code':'SKU','size':'L-XL','color':'Siyah'},mapping) is None
    assert varyant_bul({'supplier_barcode':'SKU-S-M','size':'S-M','color':'Mavi'},mapping) is None


def test_stock_refresh_uses_actual_xml_without_minting_new_barcodes(kv_client,kv_db,monkeypatch):
    from routes import supplier_xml as sx
    import supplier_codes
    source={'model_ref':'model','name':'Kazak','price':100,'vat':10,'variants':[{'supplier_barcode':'supplier-M','supplier_stock_code':'supplier','size':'M','color':'Siyah','stock':12,'ref':'supplier-M','cost':100}]}
    async def feed():return [source],{}
    async def no_new_codes(*args,**kwargs):raise AssertionError('stock refresh must not mint codes')
    monkeypatch.setattr(sx,'load_items',feed)
    monkeypatch.setattr(supplier_codes,'get_or_create_variant_codes',no_new_codes)
    kv_run(kv_db.settings.insert_one({'id':'supplier_xml','supplier_name':'test','min_stock':5,'single_size_max_stock':0}))
    kv_run(kv_db.products.insert_one({'id':'stock-product','supplier_key':'test::model','name':'Kazak','variants':[{'id':'M','barcode':'own-barcode','stock_code':'own-stock-code','supplier_barcode':'supplier-M','size':'M','color':'Siyah','stock':1}]}))
    result=kv_run(sx.sync_stock())
    assert result['updated']==1 and result['failed']==0
    product=kv_run(kv_db.products.find_one({'id':'stock-product'}))
    assert product['stock']==12
    assert product['variants'][0]['barcode']=='own-barcode'
    assert kv_run(kv_db.supplier_code_map.count_documents({}))==0


def test_photo_output_keeps_entire_frame_without_stretching_edges(kv_client):
    import io
    from PIL import Image
    from routes.ai_product_images import _fit_target, ENHANCE_TARGET
    source=Image.new('RGB',(300,400),(220,20,20))
    raw=io.BytesIO();source.save(raw,'PNG')
    output=Image.open(io.BytesIO(_fit_target(raw.getvalue())))
    assert ENHANCE_TARGET==(1200,1800)
    assert output.size==(1200,1800)
    assert min(output.getpixel((600,20)))>245
    assert output.getpixel((600,300))[0]>200
    assert output.getpixel((600,300))[1]<40
    assert min(output.getpixel((600,1780)))>245


def test_whole_product_absence_requires_two_complete_xml_reads(kv_client,kv_db,monkeypatch):
    from routes import supplier_xml as sx
    import supplier_codes
    source={'model_ref':'present','name':'Kazak','price':100,'vat':10,'variants':[{'supplier_barcode':'present-M','size':'M','color':'Siyah','stock':12,'ref':'present-M'}]}
    async def feed():return [source],{}
    monkeypatch.setattr(sx,'load_items',feed)
    key=supplier_codes.supplier_key('modayakamoz','removed')
    original={'id':'removed','supplier':'modayakamoz','supplier_key':key,'is_active':True,'stock':9,'variants':[{'id':'historical','barcode':'keep-me','size':'L','stock':9}]}
    kv_run(kv_db.products.insert_one(original))
    kv_run(sx.sync_stock())
    assert kv_run(kv_db.products.find_one({'id':'removed'}))['stock']==9
    result=kv_run(sx.sync_stock())
    stored=kv_run(kv_db.products.find_one({'id':'removed'}))
    assert result['absent_products_zeroed']==1
    assert stored['stock']==0 and stored['variants'][0]['feed_missing']
    assert stored['variants'][0]['barcode']=='keep-me' and stored['is_active'] is True


def test_partial_xml_does_not_clear_absent_product_stock(kv_client,kv_db,monkeypatch):
    from routes import supplier_xml as sx
    import supplier_codes
    source={'model_ref':'only-one','name':'Kazak','variants':[]}
    async def feed():return [source],{}
    monkeypatch.setattr(sx,'load_items',feed)
    previous=[supplier_codes.supplier_key('modayakamoz',str(i)) for i in range(10)]
    kv_run(kv_db.settings.insert_one({'id':sx.SETTINGS_ID,'stock_feed_keys':previous}))
    kv_run(kv_db.products.insert_one({'id':'safe','supplier':'modayakamoz','supplier_key':supplier_codes.supplier_key('modayakamoz','not-seen'),'stock':7,'variants':[{'id':'S','size':'S','stock':7}]}))
    result=kv_run(sx.sync_stock())
    assert result['absent_products_zeroed']==0
    assert kv_run(kv_db.products.find_one({'id':'safe'}))['stock']==7
    assert kv_run(kv_db.settings.find_one({'id':sx.SETTINGS_ID}))['stock_feed_keys']==previous


def test_changed_source_product_key_does_not_erase_a_matching_supplier_barcode(kv_client,kv_db,monkeypatch):
    from routes import supplier_xml as sx
    import supplier_codes
    source={'model_ref':'renamed','name':'Kazak','variants':[{'supplier_barcode':'same-real-M','size':'M','stock':11}]}
    async def feed():return [source],{}
    monkeypatch.setattr(sx,'load_items',feed)
    kv_run(kv_db.settings.insert_one({'id':sx.SETTINGS_ID,'stock_feed_keys':[supplier_codes.supplier_key('modayakamoz','renamed')]}))
    kv_run(kv_db.products.insert_one({'id':'previous-key','supplier':'modayakamoz','supplier_key':supplier_codes.supplier_key('modayakamoz','old'),'stock':11,'variants':[{'id':'M','size':'M','supplier_barcode':'same-real-M','stock':11}]}))
    result=kv_run(sx.sync_stock())
    assert result['absent_alias_skipped']==1
    assert kv_run(kv_db.products.find_one({'id':'previous-key'}))['stock']==11
