#!/usr/bin/env python3
"""Publish one reviewed partial-view batch into the live Meluni catalog."""
from __future__ import annotations
import asyncio, hashlib, json, os, sys, uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.getcwd())
from routes.deps import db, client
from services import r2_storage as r2

VIEWS = ('front', 'back', 'fashion_model', 'size_chart')
ROLE = {'front':'front','back':'back','fashion_model':'model','size_chart':'size-chart'}

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()

def canon(v):
    return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str)

async def main(payload_file, assets_root, result_file):
    payload=json.loads(payload_file.read_text())
    pub=payload.get('publication_id','')
    products=payload.get('products') or []
    if not pub.startswith('meluni-images-20261110-v3-') or not 1<=len(products)<=40 or len({p.get('product_id') for p in products})!=len(products) or payload.get('publish_authorized_by_user') is not True:
        raise RuntimeError('unexpected publication scope')
    eligible={}; already={}; skipped={}
    for p in products:
        pid=p['product_id']; assets=p.get('assets') or {}; views=tuple(v for v in VIEWS if v in assets)
        if not views or len(assets)!=len(views): raise RuntimeError(f'bad view set: {pid}')
        for view in views:
            a=assets[view]; qa=str(a.get('qa_status','')).lower(); f=assets_root/a['file']
            if a.get('sample')!=p.get('sample') or not any(s in qa for s in ('pass','verified')) or any(s in qa for s in ('fail','open','unverified','insufficient')) or not f.is_file() or digest(f)!=a.get('sha256'):
                raise RuntimeError(f'asset or QA mismatch: {pid} {view}')
        doc=await db.products.find_one({'id':pid},{'_id':0,'id':1,'name':1,'is_active':1,'active':1,'stock':1,'images':1,'images_custom':1,'ai_images_publication_id':1,'ai_images_published_assets':1})
        if not doc:
            skipped[pid]='product_not_found';continue
        if doc.get('ai_images_publication_id')==pub:
            urls=doc.get('ai_images_published_assets') or {}
            if set(urls)!=set(views):raise RuntimeError(f'incomplete publication marker: {pid}')
            already[pid]=urls;continue
        if doc.get('is_active',doc.get('active')) is not True or float(doc.get('stock') or 0)<=0:
            skipped[pid]='inactive_or_out_of_stock';continue
        if canon(doc.get('name'))!=canon(p.get('name')):
            skipped[pid]='name_changed';continue
        if canon(doc.get('images') or [])!=canon(p.get('expected_original_images') or []):
            skipped[pid]='gallery_changed';continue
        eligible[pid]=doc
    if not eligible and not already: raise RuntimeError('no eligible product remains')
    if eligible:
        if not r2.is_enabled() or not r2.health_check().get('ok') or not (os.environ.get('R2_PUBLIC_URL') or '').startswith('https://'):
            raise RuntimeError('public R2 storage unavailable')
    now=datetime.now(timezone.utc).isoformat(); staged={}
    for p in products:
        pid=p['product_id']
        if pid not in eligible:continue
        staged[pid]={}
        for view in VIEWS:
            if view not in p['assets']:continue
            a=p['assets'][view];f=assets_root/a['file'];key=f"ai-products/{pid}-{ROLE[view]}-{a['sha256'][:12]}.jpg"
            data=f.read_bytes();url=r2.put_object(key,data,'image/jpeg')
            staged[pid][view]=url
            staged[pid][f'_record_{view}']={'id':str(uuid.uuid5(uuid.NAMESPACE_URL,f'meluni:{pub}:{pid}:{view}')),'storage_path':key.rsplit('/',1)[-1],'r2_key':key,'r2_url':url,'original_filename':f.name,'content_type':'image/jpeg','size':len(data),'source':'ai_generated','product_id':pid,'pose':ROLE[view],'is_deleted':False,'created_at':now}
    if eligible:
        async with await client.start_session() as session:
            async with session.start_transaction():
                for p in products:
                    pid=p['product_id']
                    if pid not in eligible:continue
                    doc=eligible[pid];urls=staged[pid]; old=list(doc.get('images') or [])
                    new=[urls[v] for v in ('front','back','fashion_model') if v in urls]+old
                    if 'size_chart' in urls:
                        new.append({'id':str(uuid.uuid5(uuid.NAMESPACE_URL,f'meluni:{pub}:{pid}:size_chart_gallery')),'url':urls['size_chart'],'is_size_table':True,'alt':'Ölçü Tablosu','created_at':now})
                    filt={'id':pid,'images':old,'stock':{'$gt':0}}
                    if doc.get('is_active') is not None:filt['is_active']=True
                    else:filt['active']=True
                    public_urls={v:urls[v] for v in VIEWS if v in urls}
                    res=await db.products.update_one(filt,{'$set':{'images':new,'images_custom':True,'images_custom_at':now,'ai_images_generated_at':now,'ai_images_publication_id':pub,'ai_images_published_assets':public_urls}},session=session)
                    if res.modified_count!=1:raise RuntimeError(f'concurrent gallery change: {pid}')
                    for view in VIEWS:
                        if view in urls:
                            rec=urls[f'_record_{view}'];await db.files.update_one({'r2_key':rec['r2_key']},{'$setOnInsert':rec},upsert=True,session=session)
                    await db.ai_image_jobs.update_one({'id':f'{pub}:{pid}'},{'$setOnInsert':{'id':f'{pub}:{pid}','product_id':pid,'poses':list(public_urls),'generated':public_urls,'errors':{},'model':'reviewed-product-image-draft','size':'1200x1800','publication_id':pub,'created_at':now}},upsert=True,session=session)
    done=[]
    for p in products:
        pid=p['product_id']
        if pid in skipped:continue
        doc=await db.products.find_one({'id':pid},{'_id':0,'images':1,'ai_images_publication_id':1,'ai_images_published_assets':1})
        expected=already.get(pid) or {v:staged[pid][v] for v in VIEWS if v in staged[pid]}
        if not doc or doc.get('ai_images_publication_id')!=pub or doc.get('ai_images_published_assets')!=expected:
            raise RuntimeError(f'post-write verification failed: {pid}')
        done.append({'product_id':pid,'sample':p['sample'],'name':p['name'],'urls':expected,'gallery_image_count':len(doc.get('images') or [])})
    result={'status':'published_and_verified' if not skipped else 'published_partial_and_verified','publication_id':pub,'published_at_utc':now,'products':done,'skipped':[{'product_id':p['product_id'],'sample':p['sample'],'reason':skipped[p['product_id']]} for p in products if p['product_id'] in skipped]}
    result_file.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'products':len(done),'assets':sum(len(p['urls']) for p in done),'skipped':len(skipped)}))

if __name__=='__main__':
    try:asyncio.run(main(Path(sys.argv[1]),Path(sys.argv[2]),Path(sys.argv[3])))
    finally:client.close()
