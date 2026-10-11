import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import axios from 'axios';
import Header from '../components/Header';
import Footer from '../components/Footer';
import FullLookSections from '../components/FullLookSections';
import FullLookAddAll from '../components/FullLookAddAll';
import { applyRuntimeSeo, setCategorySeo } from '../lib/seo';
const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

// Kombin alt linki: /full-look/<kombin id> | /full-look/2 (sıra) | /full-look/<başlık-slug>
// Reklamdan (Meta/Google/TikTok) gelen müşteri doğrudan o kombine iner; kaydırmakla uğraşmaz.
export const lookSlug = (t) => String(t || '').toLocaleLowerCase('tr').replace(/[çğıöşü]/g, (c) => ({ ç: 'c', ğ: 'g', ı: 'i', ö: 'o', ş: 's', ü: 'u' })[c])
  .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
export function findLookIndex(looks, ref) {
  if (!ref || !Array.isArray(looks)) return -1;
  const r = decodeURIComponent(String(ref)).trim();
  let i = looks.findIndex((l) => String(l.id) === r);
  if (i < 0 && /^\d+$/.test(r)) i = Number(r) >= 1 && Number(r) <= looks.length ? Number(r) - 1 : -1;
  if (i < 0) i = looks.findIndex((l) => lookSlug(l.title) && lookSlug(l.title) === lookSlug(r));
  if (i < 0) i = looks.findIndex((l) => String(l.id).startsWith(r));
  return i;
}

export default function FullLook() {
  const { lookRef } = useParams();
  const [data, setData] = useState(null);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError(false); setData(null);
    axios.get(`${API}/full-look`, {signal: controller.signal}).then(r => { if (!controller.signal.aborted) setData(r.data); })
      .catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [retry]);
  useEffect(() => {
    // Alt link: hedef kombin yüklenince ona kaydır (header yüksekliği için scroll-margin CSS'te)
    if (!lookRef || !data?.looks?.length) return;
    const i = findLookIndex(data.looks, lookRef);
    if (i < 0) return;
    const el = document.getElementById(`look-${data.looks[i].id}`);
    if (!el) return;
    const t = setTimeout(() => el.scrollIntoView({ behavior: 'smooth', block: 'start' }), 60);
    return () => clearTimeout(t);
  }, [lookRef, data]);
  useEffect(() => {
    const controller = new AbortController();
    applyRuntimeSeo('/kategori/full-look', () => setCategorySeo(data?.title || 'Full Look', 'full-look', '', data?.description), { signal: controller.signal });
    return () => controller.abort();
  }, [data?.title, data?.description]);
  return <div className="sf-page min-h-screen bg-white"><Header /><main className="full-look-page">
    <header className="full-look-heading"><h1>{data?.title || 'Full Look'}</h1>{data?.description && <p>{data.description}</p>}</header>
    {error ? <div className="p-12 text-center" role="alert"><p>Kombinler şu an yüklenemedi.</p><button className="underline mt-4" onClick={() => setRetry(r => r + 1)}>Tekrar dene</button></div>
      : !data ? <p className="p-12 text-center" role="status">Kombinler yükleniyor…</p>
      : !data.looks?.length ? <p className="p-12 text-center text-stone-500">Yeni kombin seçkimiz yakında burada.</p>
      : <FullLookSections looks={data.looks} renderProducts={(look) => <FullLookAddAll products={look.products} />} />}
  </main><Footer /></div>;
}
