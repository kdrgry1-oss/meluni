import { useEffect, useState } from 'react';
import axios from 'axios';
import FullLookSections from '../../components/FullLookSections';
import { priceView, fmtTL } from '../../lib/price';
import './fullLookEditor.css';

const API = `${process.env.REACT_APP_BACKEND_URL}/api/full-look`;
const auth = () => ({ Authorization: `Bearer ${localStorage.getItem('token')}` });
const errorText = (error) => typeof error?.response?.data?.detail === 'string'
  ? error.response.data.detail : 'İşlem tamamlanamadı. Bağlantınızı ve yetkinizi kontrol edip tekrar deneyin.';
export const lookPayload = (page) => ({ title: page.title, description: page.description, revision: page.revision,
  looks: page.looks.map(({ id, title, source_product_id, image, product_ids, is_active }) =>
    ({ id, title, source_product_id, image, product_ids, is_active })) });
const move = (items, index, direction) => {
  const next = [...items], target = index + direction;
  if (target < 0 || target >= next.length) return next;
  [next[index], next[target]] = [next[target], next[index]];
  return next;
};

export function LookProductSearch({ label, onSelect, excluded = [] }) {
  const [query, setQuery] = useState('');
  const [products, setProducts] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    setProducts([]); setError('');
    if (query.trim().length < 2) { setBusy(false); return () => controller.abort(); }
    setBusy(true);
    const timer = setTimeout(() => {
      axios.get(`${API}/products`, { params: { search: query.trim() }, headers: auth(), signal: controller.signal })
        .then(({ data }) => { if (!controller.signal.aborted) setProducts(data.products || []); })
        .catch((err) => { if (!controller.signal.aborted) setError(errorText(err)); })
        .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    }, 300);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [query]);
  return <div className="look-product-search">
    <label>{label}<input type="search" value={query} maxLength={100} placeholder="Ürün adı veya stok kodu (en az 2 karakter)" onChange={e => setQuery(e.target.value)} /></label>
    {busy && <p role="status">Ürünler aranıyor…</p>}
    {error && <p role="alert">{error}</p>}
    {!busy && !error && query.trim().length >= 2 && !products.length && <p>Ürün bulunamadı.</p>}
    {!!products.length && <ul className="look-search-results" aria-label={`${label} sonuçları`}>
      {products.map(p => <li key={p.id}><button type="button" disabled={excluded.includes(p.id)} onClick={() => { onSelect(p); setQuery(''); setProducts([]); }}>
        {p.images?.[0] && <img src={p.images[0]} alt="" />}<span>{p.name}<small>{p.id} · {fmtTL(priceView(p).display)}{excluded.includes(p.id) ? ' · Eklendi' : ''}</small></span>
      </button></li>)}
    </ul>}
  </div>;
}

export default function FullLookEditor() {
  const [page, setPage] = useState(null);
  const [saved, setSaved] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [mobile, setMobile] = useState(false);
  const dirty = !!page && JSON.stringify(lookPayload(page)) !== saved;
  const load = () => {
    setLoading(true); setError('');
    axios.get(`${API}/admin`, { headers: auth() }).then(({ data }) => {
      setPage(data); setSaved(JSON.stringify(lookPayload(data)));
    }).catch(e => setError(errorText(e))).finally(() => setLoading(false));
  };
  useEffect(() => { load(); }, []);
  useEffect(() => {
    if (!dirty) return undefined;
    const warn = e => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);
  const patch = (id, changes) => setPage(prev => ({ ...prev, looks: prev.looks.map(row => row.id === id ? { ...row, ...changes } : row) }));
  const save = async () => {
    setSaving(true); setError(''); setNotice('');
    try {
      const { data } = await axios.put(`${API}/admin`, lookPayload(page), { headers: auth() });
      setPage(data); setSaved(JSON.stringify(lookPayload(data))); setNotice('Kaydedildi. Yayın işaretli kombinler Full Look sayfasında görünür.');
    } catch (e) { setError(errorText(e)); } finally { setSaving(false); }
  };
  if (loading) return <p role="status">Full Look düzenleyici yükleniyor…</p>;
  if (!page) return <div role="alert">{error}<button type="button" onClick={load}>Tekrar dene</button></div>;
  return <div className="full-look-editor">
    <header className="look-editor-toolbar"><div><h1>Full Look Sayfası Düzenleme</h1><p>Bir görsel, onu tamamlayan ürünler. Kombinleri aşağıdaki sırayla yayınlayın.</p></div>
      <a href="/full-look" target="_blank" rel="noreferrer">Yayındaki sayfayı aç ↗</a>
      <button className="look-primary" type="button" onClick={save} disabled={saving || !dirty}>{saving ? 'Kaydediliyor…' : 'Değişiklikleri Kaydet'}</button>
    </header>
    <p className="look-editor-hint">{dirty ? 'Kaydedilmemiş değişiklikler var. ' : 'Kayıtlı sürüm. '}Önizleme taslağınızı gösterir. Fiyatlar ürün kayıtlarından ve geçerli ürün kampanyalarından gelir; kupon/havale gibi sepet indirimleri burada gösterilmez.</p>
    {error && <div className="look-editor-error" role="alert">{error} Taslağınız korunuyor.</div>}
    {notice && <p role="status">{notice}</p>}
    <fieldset disabled={saving}>
      <div className="look-page-fields"><label>Sayfa başlığı<input value={page.title} maxLength={120} onChange={e => setPage({ ...page, title: e.target.value })} /></label>
        <label>Sayfa açıklaması<textarea value={page.description} maxLength={500} onChange={e => setPage({ ...page, description: e.target.value })} /></label></div>
      <div className="look-editor-actions"><button type="button" disabled={page.looks.length >= 50} onClick={() => setPage({ ...page, looks: [{ id: crypto.randomUUID(), title: '', source_product_id: '', image: '', product_ids: [], products: [], source_product: null, is_active: false }, ...page.looks] })}>+ Kombin Ekle</button>
        <span>{page.looks.length} / 50 kombin</span><button type="button" aria-pressed={mobile} onClick={() => setMobile(!mobile)}>{mobile ? 'Masaüstü önizleme' : 'Mobil önizleme'}</button></div>
      {/* Yeni kombin LİSTENİN BAŞINA eklenir → vitrinde en son eklenen en üstte görünür (kullanıcı isteği).
          Sıra yine ↑/↓ ile elle değiştirilebilir; reklam linkleri kombin kimliğiyle çalıştığından sıra değişse de bozulmaz. */}
      {!page.looks.length && <div className="look-editor-empty">İlk kombininizi ekleyin. Fotoğraf ve ürünler seçilmeden yayınlanmaz.</div>}
      {page.looks.map((row, index) => <article className="look-editor-card" key={row.id}>
        <div className="look-card-heading"><h2>Kombin {index + 1}</h2>
          {/* Reklam alt linki: Meta/Google/TikTok reklamı bu linke verilir, müşteri doğrudan bu kombine iner */}
          <span className="look-ad-link" title="Reklam linki — bu kombine doğrudan iner">
            <code>{`${window.location.origin}/full-look/${row.id}`}</code>
            <button type="button" onClick={() => { navigator.clipboard?.writeText(`${window.location.origin}/full-look/${row.id}`); }}>Linki kopyala</button>
          </span>
          <label className="look-publish"><input type="checkbox" checked={row.is_active} onChange={e => patch(row.id, { is_active: e.target.checked })} /> Yayında</label>
          <button type="button" disabled={!index} aria-label={`Kombin ${index + 1} yukarı`} onClick={() => setPage({ ...page, looks: move(page.looks, index, -1) })}>↑</button>
          <button type="button" disabled={index === page.looks.length - 1} aria-label={`Kombin ${index + 1} aşağı`} onClick={() => setPage({ ...page, looks: move(page.looks, index, 1) })}>↓</button>
          <button type="button" onClick={() => { if (window.confirm('Bu kombin taslaktan kaldırılsın mı? Yayına yansıması için kaydetmeniz gerekir.')) setPage({ ...page, looks: page.looks.filter(r => r.id !== row.id) }); }}>Kombini kaldır</button>
        </div>
        <label>Kombin başlığı (isteğe bağlı)<input value={row.title} maxLength={120} onChange={e => patch(row.id, { title: e.target.value })} /></label>
        {row.warning && <p className="look-editor-hint">{row.warning}</p>}
        <div className="look-selection-columns"><section><h3>1. Soldaki kombin fotoğrafı</h3>
          <LookProductSearch label={`Kombin ${index + 1} kaynak ürün ara`} onSelect={p => patch(row.id, { source_product_id: p.id, source_product: p, image: '', warning: '' })} />
          {row.source_product && <><p>{row.source_product.name} — Hangi görseli kullanmak istiyorsunuz?</p>
            <div className="look-gallery">{(row.source_product.images || []).map((src, i) => <button type="button" key={`${src}-${i}`} aria-label={`Fotoğraf ${i + 1}`} aria-pressed={row.image === src} onClick={() => patch(row.id, { image: src })}><img src={src} alt={`${row.source_product.name} ${i + 1}`} /></button>)}</div>
            {!row.source_product.images?.length && <p>Bu ürünün galerisinde fotoğraf bulunmuyor. Başka bir ürün seçin.</p>}</>}
          {!row.source_product && row.source_product_id && <p role="alert">Kaynak ürün bulunamadı. Başka bir kaynak ürün seçin.</p>}
        </section><section><h3>2. Görseldeki ürünler ({row.product_ids.length}/8)</h3>
          {row.product_ids.length < 8 && <LookProductSearch label={`Kombin ${index + 1} sağ tarafa ürün ara`} excluded={row.product_ids} onSelect={p => {
            if (!row.product_ids.includes(p.id)) patch(row.id, { product_ids: [...row.product_ids, p.id], products: [...(row.products || []), p], warning: '' });
          }} />}
          <ol className="look-selected-products">{row.product_ids.map((pid, pi) => {
            const product = row.products?.find(p => p.id === pid);
            return <li key={pid}>{product?.images?.[0] && <img src={product.images[0]} alt="" />}<span>{product?.name || `Ürün bulunamadı: ${pid}`}<small>{product ? fmtTL(priceView(product).display) : 'Yayınlamadan önce kaldırın.'}</small></span>
              <button type="button" disabled={!pi} aria-label={`${product?.name || pid} yukarı`} onClick={() => patch(row.id, { product_ids: move(row.product_ids, pi, -1) })}>↑</button>
              <button type="button" disabled={pi === row.product_ids.length - 1} aria-label={`${product?.name || pid} aşağı`} onClick={() => patch(row.id, { product_ids: move(row.product_ids, pi, 1) })}>↓</button>
              <button type="button" aria-label={`${product?.name || pid} kaldır`} onClick={() => patch(row.id, { product_ids: row.product_ids.filter(id => id !== pid), products: row.products?.filter(p => p.id !== pid) })}>×</button></li>;
          })}</ol>
        </section></div>
        <details open><summary>Kombin önizlemesi · {row.is_active ? 'Kaydedince yayınlanır' : 'Taslak — müşterilere gösterilmez'}</summary>
          <div className={`full-look-editor-preview ${mobile ? 'is-mobile' : ''}`}><FullLookSections startIndex={index} interactive={false} looks={[{ ...row, products: row.product_ids.map(pid => row.products?.find(p => p.id === pid)).filter(Boolean) }]} /></div>
        </details>
      </article>)}
    </fieldset>
  </div>;
}
