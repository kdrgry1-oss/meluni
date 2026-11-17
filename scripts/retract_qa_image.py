#!/usr/bin/env python3
"""Atomically retract one QA-failed generated gallery image, preserving all others."""
import asyncio,json,os,sys
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,os.getcwd())
from routes.deps import db,client

def canon(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str)
async def main(src,dst):
 p=json.loads(Path(src).read_text());pid=p['product_id'];old=p['expected_images'];new=p['new_images'];url=p['retract_url'];pub=p['expected_publication_id']
 if p.get('authorized_by_user') is not True or not url.startswith(f'https://cdn.meluni.com.tr/ai-products/{pid}-front-') or old.count(url)!=1 or new!=[x for x in old if x!=url] or len(old)-len(new)!=1:raise RuntimeError('invalid targeted QA retraction')
 doc=await db.products.find_one({'id':pid},{'_id':0,'id':1,'is_active':1,'active':1,'stock':1,'images':1,'images_custom':1,'ai_images_publication_id':1,'ai_images_published_assets':1})
 if not doc or doc.get('is_active',doc.get('active')) is not True or float(doc.get('stock') or 0)<=0 or doc.get('images_custom') is not True or doc.get('ai_images_publication_id')!=pub or canon(doc.get('images'))!=canon(old) or doc.get('ai_images_published_assets',{}).get('front')!=url:raise RuntimeError('live gallery or protection changed')
 assets=dict(doc['ai_images_published_assets']);del assets['front'];now=datetime.now(timezone.utc).isoformat();marker=pub+'-qa-retract-front'
 async with await client.start_session() as session:
  async with session.start_transaction():
   filt={'id':pid,'images':old,'images_custom':True,'ai_images_publication_id':pub,'stock':{'$gt':0}}
   if doc.get('is_active') is not None:filt['is_active']=True
   else:filt['active']=True
   res=await db.products.update_one(filt,{'$set':{'images':new,'images_custom':True,'images_custom_at':now,'ai_images_publication_id':marker,'ai_images_published_assets':assets,'ai_images_qa_retracted':{'view':'front','url':url,'reason':p['reason'],'at':now,'previous_publication_id':pub}}},session=session)
   if res.modified_count!=1:raise RuntimeError('concurrent gallery change')
 check=await db.products.find_one({'id':pid},{'_id':0,'images':1,'images_custom':1,'ai_images_publication_id':1,'ai_images_published_assets':1})
 if not check or canon(check.get('images'))!=canon(new) or check.get('images_custom') is not True or check.get('ai_images_publication_id')!=marker or check.get('ai_images_published_assets')!=assets:raise RuntimeError('post-retraction verification failed')
 out={'status':'qa_front_retracted_and_verified','product_id':pid,'retracted_url':url,'remaining_gallery_count':len(new),'remaining_assets':assets,'at':now};Path(dst).write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'status':out['status'],'product_id':pid}))
if __name__=='__main__':
 try:asyncio.run(main(sys.argv[1],sys.argv[2]))
 finally:client.close()
