import {useEffect,useState} from 'react';
import axios from 'axios';
import {Link} from 'react-router-dom';
import Header from '../components/Header';
import Footer from '../components/Footer';
import ProductCard from '../components/ProductCard';
import {firstImage} from '../lib/img';
import './editorialHome.css';
const API=`${process.env.REACT_APP_BACKEND_URL}/api`;
export default function EditorialHome(){
 const [products,setProducts]=useState([]),[status,setStatus]=useState('loading'),[category,setCategory]=useState(''),[retry,setRetry]=useState(0);
 useEffect(()=>{
  const controller=new AbortController(); setStatus('loading');
  (async()=>{
   try {
    const {data:categories}=await axios.get(`${API}/categories`,{signal:controller.signal});
    const roots=(Array.isArray(categories)?categories:[]).filter(c=>!c.parent_id&&['kadin','erkek'].includes(c.slug)&&c.is_active!==false);
    if (!roots.length) throw new Error('Koleksiyonlar bulunamadı');
    const requests=roots.flatMap(root=>['kazak','sweatshirt','ceket'].map(search=>
     axios.get(`${API}/products`,{params:{category_id:root.id,search,limit:20,min_stock:1},signal:controller.signal})
      .then(({data})=>(Array.isArray(data)?data:data.products||[]).map(p=>({...p,editorialGender:root.slug})))));
    const results=await Promise.allSettled(requests);
    if (controller.signal.aborted) return;
    if (results.every(r=>r.status==='rejected')) throw new Error('Ürünler yüklenemedi');
    const seen=new Set();
    const groups=results.filter(r=>r.status==='fulfilled').map(r=>r.value);
    const collection=Array.from({length:Math.max(0,...groups.map(g=>g.length))},(_,i)=>groups.map(g=>g[i])).flat().filter(Boolean).filter(p=>{
     if(seen.has(p.id)||p.is_active===false||p.is_deleted||!(Number(p.stock)>0)||!firstImage(p)) return false;
     seen.add(p.id); return true;
    });
    // Aynı renk/model tekrarları yerine farklı sonbahar parçalarını öne çıkar.
    const models=new Set();
    const unique=collection.filter(p=>{const key=`${p.editorialGender}:${p.model_code||p.name}`;if(models.has(key))return false;models.add(key);return true});
    const women=unique.filter(p=>p.editorialGender==='kadin'),men=unique.filter(p=>p.editorialGender==='erkek');
    setProducts(Array.from({length:Math.max(women.length,men.length)},(_,i)=>[women[i],men[i]]).flat().filter(Boolean));
    setStatus('ready');
   } catch(e) {if(!controller.signal.aborted) setStatus('error');}
  })();
  return()=>controller.abort();
 },[retry]);
 const kadin=products.find(p=>p.editorialGender==='kadin'),erkek=products.find(p=>p.editorialGender==='erkek');
 const heroes=[kadin,erkek].filter(Boolean);
 const selected=products.filter(p=>!category||p.editorialGender===category).slice(0,8);
 const featured=kadin;
 return <div className="meluni-editorial-shell"><Header/><main className="meluni-editorial"><section className="me-hero" aria-label="Meluni koleksiyonu">{heroes.map((p,i)=><Link key={p.id} to={`/${p.slug||p.id}`} className={`me-hero-photo me-photo-${i}`} aria-label={p.name}><img src={firstImage(p)} alt={p.name} fetchPriority={i===0?'high':'auto'}/></Link>)}<div className="me-hero-copy"><p>MELUNI / SONBAHAR</p><h1>Sonbaharın ritmi.<br/>Senin stilin.</h1><Link className="me-light-link" to="/en-yeniler">KOLEKSİYONU KEŞFET <span>↗</span></Link></div><span className="me-hero-caption">YUMUŞAK DOKULAR · YENİ MEVSİM</span></section>
 <nav className="me-gender-nav" aria-label="Kadın ve erkek koleksiyonları"><Link to="/kadin">KADIN KOLEKSİYONU ↗</Link><Link to="/erkek">ERKEK KOLEKSİYONU ↗</Link></nav><div className="me-collectionbar"><span>KADIN VE ERKEK · SONBAHAR SEÇKİSİ</span><Link to="/en-yeniler">MELUNI SEÇKİSİ ↗</Link></div>
 <section className="me-section"><div className="me-section-head"><div><p className="me-eyebrow">GARDIROBUNUN YENİ FAVORİLERİ</p><h2>Sonbahar seçkisi</h2></div><Link className="me-line-link" to="/en-yeniler">TÜMÜNÜ GÖR ↗</Link></div><div className="me-tabs" role="group" aria-label="Ürün kategorileri">{[['','Tümü'],['kadin','Kadın'],['erkek','Erkek']].map(([v,label])=><button key={v} aria-pressed={category===v} className={category===v?'selected':''} onClick={()=>setCategory(v)}>{label}</button>)}</div>
 {status==='loading'?<div className="me-products" aria-label="Ürünler yükleniyor">{[0,1,2,3].map(i=><div key={i} className="me-skeleton"/>)}</div>:status==='error'?<p className="me-empty" role="status">Ürünler yüklenemedi. <button onClick={()=>setRetry(v=>v+1)}>Tekrar dene</button></p>:selected.length?<div className="me-products">{selected.map((p,i)=><ProductCard key={p.id} product={p} index={i} listId="meluni_editorial" listName="Sonbahar seçkisi"/>)}</div>:<p className="me-empty">Bu seçkide henüz ürün yok. <Link to="/en-yeniler">Tüm koleksiyonu keşfet ↗</Link></p>}</section>
 {(kadin||erkek)&&<section className="me-collections" aria-label="Koleksiyonları keşfet">{[[kadin,'KADIN / SONBAHAR','Mevsimin yumuşak hali.','KADIN KOLEKSİYONU','/kadin'],[erkek,'ERKEK / SONBAHAR','Şehre hazır.','ERKEK KOLEKSİYONU','/erkek']].filter(([p])=>p).map(([p,eyebrow,title,cta,url])=><Link key={url} className="me-collection" to={url}><img src={firstImage(p)} alt={p.name} loading="lazy"/><div><p>{eyebrow}</p><h2>{title}</h2><span>{cta} ↗</span></div></Link>)}</section>}
 <section className="me-story"><p className="me-eyebrow">MELUNI DÜNYASI</p><h2>Bir trendden fazlası.<br/>Kendin olmanın zarafeti.</h2><p>Hareketin içinden gelen, her gün yeniden keşfedilen bir stil.<br/>Güçlü çizgiler, yumuşak dokular ve sana ait bir duruş.</p><Link className="me-line-link" to="/en-yeniler">SEÇKİYİ KEŞFET ↗</Link></section>
 {featured&&<section className="me-section"><div className="me-section-head"><div><p className="me-eyebrow">THE MELUNI EDIT</p><h2>Günün seçkisi</h2></div></div><div className="me-look"><Link to={`/${featured.slug||featured.id}`}><img src={firstImage(featured)} alt={featured.name} loading="lazy"/></Link><div className="me-look-copy"><p className="me-eyebrow">01 / GÜNÜN SEÇKİSİ</p><h3>Çabasız bir uyum.</h3><p>Meluni koleksiyonundan, gününe eşlik eden parçalar.</p><Link className="me-dark-link" to={`/${featured.slug||featured.id}`}>ÜRÜNÜ İNCELE ↗</Link></div></div></section>}</main><Footer/></div>;
}
