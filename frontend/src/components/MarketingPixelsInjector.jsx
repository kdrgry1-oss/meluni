/**
 * MarketingPixelsInjector — Sayfa yüklemesinde aktif pixel snippet'lerini
 * <head> içine enjekte eder. Public endpoint kullanır (auth gerektirmez).
 *
 * Kullanım: App.js içinde 1 kez render edilir.
 */
import { useEffect } from "react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

export default function MarketingPixelsInjector() {
  useEffect(() => {
    let cancelled = false;
    const run = () => {
      if (cancelled) return;
      fetch(`${API}/marketing-pixels/active-public`)
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (cancelled || !data) return;
        // KVKK gate bayrağı — dataLayer.js bu değere göre Meta/CAPI'yi pazarlama onayına bağlar
        // (varsayılan KAPALI = mevcut davranış). İşletme Kuralları'ndan yönetilir.
        try { window.__FACETTE_CONSENT_GATE__ = data.consent_gate === true; } catch (_) { /* noop */ }
        if (data.head) {
          const wrap = document.createElement("div");
          wrap.innerHTML = data.head;
          // script'ler innerHTML ile execute edilmez → yeniden oluştur
          wrap.querySelectorAll("script").forEach((s) => {
            const n = document.createElement("script");
            for (const attr of s.attributes) n.setAttribute(attr.name, attr.value);
            if (s.textContent) n.textContent = s.textContent;
            document.head.appendChild(n);
          });
          wrap.querySelectorAll("noscript, meta, link, style").forEach((el) => {
            document.head.appendChild(el.cloneNode(true));
          });
        }
        if (data.body) {
          const wrap = document.createElement("div");
          wrap.innerHTML = data.body;
          document.body.appendChild(wrap);
        }
      })
      .catch(() => { /* sessiz */ });
    };
    // Meta Pixel taban kodu erken yüklenmezse `_fbp` cookie'si ilk event'ten (ViewContent)
    // SONRA yazılıyor → CAPI ViewContent'te fbp boş gidiyordu (EMQ düşük). Bu yüzden pikselleri
    // ARTIK mount'ta bir sonraki tick'te (load+idle beklemeden) yüklüyoruz: _fbp ~1.5-3sn erken
    // set olur, ViewContent fbp kapsaması yükselir. TAVİZ: pikseller erken yüklendiği için
    // LCP/TBT'de küçük bir artış olabilir (ilk senkron boyama yine bloklanmaz — setTimeout 0).
    const t = setTimeout(run, 0);
    return () => { cancelled = true; clearTimeout(t); };
  }, []);
  return null;
}
