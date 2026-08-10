/**
 * Instagram.jsx — Admin > Instagram Akışı
 * @facette gönderilerini Graph API token'ı ile otomatik çeker VEYA elle ekler.
 * Anasayfadaki "#FACETTE × YOU" bölümü /api/instagram/feed'i gösterir.
 */
import { useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { Save, RefreshCw, Trash2, Plus, Instagram as IgIcon, ExternalLink, Unplug, Search, Tag, ShoppingBag, X } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

export default function AdminInstagram() {
  const [settings, setSettings] = useState(null);
  const [posts, setPosts] = useState([]);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ access_token: "", ig_user_id: "", source: "media", auto_sync: false });
  // Otomatik kurulum: App ID + Secret + KISA token → backend uzatır, IG hesabını bulur, senkronlar
  const [auto, setAuto] = useState({ app_id: "", app_secret: "", short_token: "", ig_user_id: "" });
  const [autoBusy, setAutoBusy] = useState(false);
  const [showTokenFlow, setShowTokenFlow] = useState(false); // alternatif (token yapıştırma) akışı
  const [showAdvanced, setShowAdvanced] = useState(false);   // gelişmiş: manuel Graph API formu

  // TEK TIK: App ID+Secret kaydedilir → Facebook onay ekranına yönlendirilir → dönüşte
  // backend code'u token'a çevirir, IG hesabını bulur, senkronlar (token yapıştırmak GEREKMEZ).
  const startOAuth = async () => {
    if (!settings?.app_secret_set && (!auto.app_id.trim() || !auto.app_secret.trim())) {
      toast.error("İlk bağlantı için App ID ve App Secret girin (bir kez)"); return;
    }
    setAutoBusy(true);
    try {
      const t = localStorage.getItem("token");
      const r = await axios.post(`${API}/admin/instagram/oauth-start`,
        { app_id: auto.app_id.trim(), app_secret: auto.app_secret.trim() },
        { headers: { Authorization: `Bearer ${t}` } });
      if (r.data?.auth_url) window.location.href = r.data.auth_url;
      else { toast.error("Yönlendirme URL'i alınamadı"); setAutoBusy(false); }
    } catch (e) { toast.error(e.response?.data?.detail || "Bağlantı başlatılamadı"); setAutoBusy(false); }
  };

  // OAuth dönüşü: /admin/instagram?ig_connected=1&ig_user=... veya ?ig_error=...
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    if (q.get("ig_connected") === "1") {
      toast.success(`@${q.get("ig_user") || "hesap"} bağlandı · ${q.get("ig_synced") || 0} gönderi çekildi 🎉`);
    } else if (q.get("ig_error")) {
      toast.error(`Instagram bağlantısı: ${q.get("ig_error")}`, { duration: 10000 });
    } else return;
    window.history.replaceState({}, "", window.location.pathname);
    loadSettings(); loadPosts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const runAutoSetup = async () => {
    if (!auto.app_id.trim() || !auto.app_secret.trim() || !auto.short_token.trim()) {
      toast.error("App ID, App Secret ve kısa token üçü de gerekli"); return;
    }
    setAutoBusy(true);
    try {
      const token = localStorage.getItem("token");
      const r = await axios.post(`${API}/admin/instagram/auto-setup`, auto,
        { headers: { Authorization: `Bearer ${token}` } });
      toast.success(r.data?.message || "Instagram bağlandı");
      setAuto({ app_id: "", app_secret: "", short_token: "", ig_user_id: "" });
      load();
    } catch (e) { toast.error(e.response?.data?.detail || "Otomatik kurulum başarısız"); }
    finally { setAutoBusy(false); }
  };
  const [manual, setManual] = useState({ image: "", permalink: "", product_link: "", caption: "" });
  const token = localStorage.getItem("token");
  const auth = { headers: { Authorization: `Bearer ${token}` } };

  const loadSettings = async () => {
    try {
      const r = await axios.get(`${API}/admin/instagram/settings`, auth);
      setSettings(r.data);
      setForm((f) => ({
        ...f,
        ig_user_id: r.data.ig_user_id || "",
        source: r.data.source || "media",
        auto_sync: !!r.data.auto_sync,
        access_token: "",
      }));
    } catch { toast.error("Ayarlar alınamadı"); }
  };
  const loadPosts = async () => {
    try {
      const r = await axios.get(`${API}/admin/instagram/posts?limit=60`, auth);
      setPosts(r.data?.posts || []);
    } catch { /* sessiz */ }
  };
  useEffect(() => { loadSettings(); loadPosts(); }, []);

  const saveSettings = async () => {
    setBusy(true);
    try {
      const payload = { ig_user_id: form.ig_user_id, source: form.source, auto_sync: form.auto_sync };
      if (form.access_token.trim()) payload.access_token = form.access_token.trim();
      await axios.put(`${API}/admin/instagram/settings`, payload, auth);
      toast.success("Ayarlar kaydedildi");
      await loadSettings();
    } catch { toast.error("Kaydetme başarısız"); }
    finally { setBusy(false); }
  };

  const syncNow = async () => {
    setBusy(true);
    try {
      const r = await axios.post(`${API}/admin/instagram/sync`, {}, auth);
      toast.success(`${r.data?.saved || 0} gönderi güncellendi (kendi: ${r.data?.media || 0}, etiketli: ${r.data?.tagged || 0})`);
      if (r.data?.tags_note) {
        toast.error(`Etiketli gönderiler çekilemedi: ${r.data.tags_note}`, { duration: 12000 });
      }
      await loadPosts(); await loadSettings();
    } catch (e) { toast.error(e?.response?.data?.detail || "Senkron başarısız"); }
    finally { setBusy(false); }
  };

  const disconnect = async () => {
    if (!window.confirm("Token'ı silip bağlantıyı kesmek istiyor musunuz? (Gönderiler silinmez)")) return;
    setBusy(true);
    try { await axios.post(`${API}/admin/instagram/disconnect`, {}, auth); toast.success("Bağlantı kesildi"); await loadSettings(); }
    catch { toast.error("İşlem başarısız"); }
    finally { setBusy(false); }
  };

  const addManual = async () => {
    if (!manual.image.trim()) { toast.error("Görsel URL'si zorunlu"); return; }
    setBusy(true);
    try {
      await axios.post(`${API}/admin/instagram/posts`, manual, auth);
      toast.success("Gönderi eklendi");
      setManual({ image: "", permalink: "", product_link: "", caption: "" });
      await loadPosts();
    } catch (e) { toast.error(e?.response?.data?.detail || "Eklenemedi"); }
    finally { setBusy(false); }
  };

  const toggleActive = async (p) => {
    try { await axios.put(`${API}/admin/instagram/posts/${p.id}`, { active: !(p.active !== false) }, auth); await loadPosts(); }
    catch { toast.error("Güncellenemedi"); }
  };
  const del = async (p) => {
    if (!window.confirm("Gönderi silinsin mi?")) return;
    try { await axios.delete(`${API}/admin/instagram/posts/${p.id}`, auth); await loadPosts(); }
    catch { toast.error("Silinemedi"); }
  };

  // ---- InstaShop: kaynak filtresi + gönderiye ürün bağlama ----
  const [filter, setFilter] = useState("all"); // all | media | tagged | manual
  const [pickerFor, setPickerFor] = useState(null); // ürün seçici açık olan gönderi id'si
  const [pQuery, setPQuery] = useState("");
  const [pResults, setPResults] = useState([]);
  const [pBusy, setPBusy] = useState(false);
  const searchProducts = async (q) => {
    setPQuery(q);
    if (!q.trim() || q.trim().length < 2) { setPResults([]); return; }
    setPBusy(true);
    try {
      const r = await axios.get(`${API}/products?search=${encodeURIComponent(q.trim())}&limit=10`, auth);
      setPResults((r.data?.products || r.data || []).slice(0, 10));
    } catch { setPResults([]); }
    finally { setPBusy(false); }
  };
  const saveProducts = async (post, products) => {
    setPosts((prev) => prev.map((x) => (x.id === post.id ? { ...x, products } : x)));
    try { await axios.put(`${API}/admin/instagram/posts/${post.id}`, { products }, auth); }
    catch { toast.error("Ürünler kaydedilemedi"); await loadPosts(); }
  };
  const addProductToPost = (post, prod) => {
    const cur = post.products || [];
    if (cur.some((c) => String(c.id) === String(prod.id))) { toast.info("Zaten ekli"); return; }
    if (cur.length >= 8) { toast.error("En fazla 8 ürün"); return; }
    const item = {
      id: prod.id,
      title: prod.name || prod.title || "",
      image: (Array.isArray(prod.images) && prod.images[0]) || prod.image || "",
      price: prod.price, old_price: prod.old_price,
      url: `/${prod.slug || prod.id}`,
    };
    saveProducts(post, [...cur, item]);
    setPQuery(""); setPResults([]);
  };
  const removeProductFromPost = (post, pid) =>
    saveProducts(post, (post.products || []).filter((c) => String(c.id) !== String(pid)));

  const kindLabel = (p) => (p.source === "manual" || p.kind === "manual") ? "elle"
    : (p.kind === "tagged" || p.source === "tags") ? "etiketli" : "kendi";
  const filteredPosts = posts.filter((p) => {
    const k = kindLabel(p);
    return filter === "all" ? true : filter === "manual" ? k === "elle" : filter === "tagged" ? k === "etiketli" : k === "kendi";
  });

  if (!settings) return <div className="p-10 text-center text-gray-400">Yükleniyor...</div>;

  return (
    <div data-testid="admin-instagram">
      <div className="flex items-center justify-between mb-5">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2"><IgIcon size={22} /> Instagram Akışı</h1>
          <p className="text-sm text-gray-500 mt-1">Anasayfadaki “#FACETTE × YOU” bölümünü besler.</p>
        </div>
        <div className="flex gap-2">
          {settings.token_set && (
            <button onClick={syncNow} disabled={busy} className="flex items-center gap-1.5 px-4 py-2 bg-black text-white rounded-lg text-sm disabled:opacity-50">
              <RefreshCw size={14} className={busy ? "animate-spin" : ""} /> Şimdi Çek
            </button>
          )}
        </div>
      </div>

      {/* Bağlantı durumu */}
      <div className="grid md:grid-cols-3 gap-3 mb-4">
        <div className="bg-white border rounded-xl p-4">
          <p className="text-xs uppercase tracking-wider text-gray-500">Durum</p>
          <p className={`text-lg font-bold mt-1 ${settings.connected ? "text-green-600" : "text-gray-400"}`}>
            {settings.connected ? "Bağlı" : "Bağlı değil"}
          </p>
        </div>
        <div className="bg-white border rounded-xl p-4">
          <p className="text-xs uppercase tracking-wider text-gray-500">Gönderi Sayısı</p>
          <p className="text-lg font-bold mt-1">{settings.post_count}</p>
        </div>
        <div className="bg-white border rounded-xl p-4">
          <p className="text-xs uppercase tracking-wider text-gray-500">Son Senkron</p>
          <p className="text-sm mt-1 text-gray-700">{settings.last_sync ? new Date(settings.last_sync).toLocaleString("tr-TR") : "—"}</p>
        </div>
      </div>
      {settings.last_error && (
        <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-lg p-3 mb-4">Son hata: {settings.last_error}</div>
      )}

      {/* ⚡ TEK TIK BAĞLANTI — token almak GEREKMEZ: Facebook onay ekranına gider, gerisini sunucu yapar */}
      <div className="bg-emerald-50 border-2 border-emerald-200 rounded-xl p-4 mb-4">
        <h2 className="text-sm font-bold uppercase tracking-wider mb-1 text-emerald-800">⚡ Tek Tık Bağlantı (Önerilen — token gerekmez)</h2>
        <p className="text-xs text-emerald-700 mb-3">
          Token/Explorer ile uğraşmayın: aşağıya <b>bir kez</b> App ID + App Secret girin
          (<a className="underline font-semibold" href="https://developers.facebook.com/apps" target="_blank" rel="noopener noreferrer">developers.facebook.com/apps</a> →
          uygulamanız → <b>Ayarlar → Temel</b>) ve "Facebook ile Bağlan"a basın. Facebook'ta kendi hesabınızla
          onay verirsiniz; token alma, uzatma, Instagram hesabını bulma ve ilk senkronu sistem kendisi yapar.
        </p>
        <div className="bg-white/70 border border-emerald-200 rounded-lg p-2.5 mb-3 text-[11px] text-gray-600">
          <b>Bir kez yapılacak app ayarı:</b> uygulamanızda <b>Facebook Login</b> ürünü ekli olmalı ve
          <b> Facebook Login → Settings → Valid OAuth Redirect URIs</b> alanına şu adres eklenmeli:
          <div className="flex items-center gap-2 mt-1">
            <code className="bg-gray-100 px-2 py-1 rounded text-[10px] break-all">{settings.oauth_redirect_uri || "https://api.facette.com.tr/api/instagram/oauth/callback"}</code>
            <button type="button" className="text-emerald-700 font-bold underline shrink-0"
              onClick={() => { navigator.clipboard?.writeText(settings.oauth_redirect_uri || "https://api.facette.com.tr/api/instagram/oauth/callback"); toast.success("Kopyalandı"); }}>
              kopyala
            </button>
          </div>
        </div>
        <div className="grid md:grid-cols-2 gap-3 mb-3">
          <input value={auto.app_id} onChange={(e) => setAuto({ ...auto, app_id: e.target.value })}
            placeholder={settings.app_id ? `App ID (kayıtlı: ${settings.app_id})` : "App ID (ör. 1234567890)"}
            className="border px-3 py-2 rounded text-sm" />
          <input type="password" value={auto.app_secret} onChange={(e) => setAuto({ ...auto, app_secret: e.target.value })}
            placeholder={settings.app_secret_set ? "App Secret (kayıtlı — değişmeyecekse boş bırakın)" : "App Secret"}
            className="border px-3 py-2 rounded text-sm" />
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          <button onClick={startOAuth} disabled={autoBusy}
            className="px-5 py-2.5 bg-[#1877F2] text-white rounded-lg text-sm font-bold hover:bg-[#0f66d6] disabled:opacity-50">
            {autoBusy ? "Yönlendiriliyor…" : "🔗 Facebook ile Bağlan"}
          </button>
          <button type="button" onClick={() => setShowTokenFlow(v => !v)} className="text-xs text-emerald-700 underline">
            {showTokenFlow ? "token akışını gizle" : "Alternatif: elimde token var"}
          </button>
        </div>
        {showTokenFlow && (
          <div className="mt-3 pt-3 border-t border-emerald-200">
            <p className="text-xs text-emerald-700 mb-2">
              <a className="underline font-semibold" href="https://developers.facebook.com/tools/explorer" target="_blank" rel="noopener noreferrer">Graph API Explorer</a>'dan
              (izinler: <b>instagram_basic</b> + <b>pages_show_list</b>) alınan KISA ömürlü token'ı yapıştırın — sistem 60 günlük
              token'a çevirir, doğrudan Instagram'dan çeker. <b>Instagram User ID</b> girerseniz (sayfa bağınız görünmese bile)
              hesabı doğrudan bulur — Business Suite → Instagram hesapları'nda "Kod:" ile yazan sayıdır.
            </p>
            <div className="grid md:grid-cols-2 gap-3 mb-2">
              <input type="password" value={auto.short_token} onChange={(e) => setAuto({ ...auto, short_token: e.target.value })}
                placeholder="Kısa ömürlü token (EAAB...)" className="border px-3 py-2 rounded text-sm" />
              <input value={auto.ig_user_id} onChange={(e) => setAuto({ ...auto, ig_user_id: e.target.value })}
                placeholder="Instagram User ID (ops. — ör. 17841410045037513)" className="border px-3 py-2 rounded text-sm" />
            </div>
            <button onClick={runAutoSetup} disabled={autoBusy}
              className="px-4 py-2 bg-emerald-600 text-white rounded-lg text-sm font-bold hover:bg-emerald-700 disabled:opacity-50">
              {autoBusy ? "Bağlanıyor…" : "⚡ Bağla ve Senkronla"}
            </button>
          </div>
        )}
      </div>

      {/* Token / Ayarlar — TEK YOL sadeleştirmesi: yukarıdaki "Facebook ile Bağlan" zaten token
          alma + 60 güne uzatma + Instagram hesabını bulma + ilk senkronu KENDİ yapıyor. Bu manuel
          form yan yana durunca "hangisini kullanacağım?" karışıklığı yaratıyordu → varsayılan
          KAPALI bir "Gelişmiş" alanına alındı. Sık kullanılan iki şey (senkron açık/kapalı ve
          Bağlantıyı Kes) dışarıda, her zaman görünür kaldı. */}
      <div className="bg-white border rounded-xl p-4 mb-4">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={form.auto_sync}
              onChange={(e) => { const v = e.target.checked; setForm({ ...form, auto_sync: v }); }} />
            Otomatik senkron (her 30 dk)
          </label>
          <div className="flex gap-2">
            <button onClick={saveSettings} disabled={busy}
              className="flex items-center gap-1.5 px-4 py-2 bg-black text-white rounded-lg text-sm disabled:opacity-50">
              <Save size={14} /> Kaydet
            </button>
            {settings.token_set && (
              <button onClick={disconnect} disabled={busy}
                className="flex items-center gap-1.5 px-4 py-2 border border-gray-200 rounded-lg text-sm hover:bg-gray-50">
                <Unplug size={14} /> Bağlantıyı Kes
              </button>
            )}
          </div>
        </div>
        <button type="button" onClick={() => setShowAdvanced(v => !v)}
          className="mt-3 text-xs text-gray-500 underline">
          {showAdvanced ? "Gelişmiş ayarları gizle" : "Gelişmiş: token'ı elle gir / kaynak seç"}
        </button>
      </div>

      {showAdvanced && (
      <div className="bg-white border rounded-xl p-4 mb-4">
        <h2 className="text-sm font-bold uppercase tracking-wider mb-1">Graph API Bağlantısı (gelişmiş)</h2>
        <p className="text-xs text-gray-500 mb-3">
          <b>Normalde buraya dokunmanıza gerek yok</b> — yukarıdaki “Facebook ile Bağlan” token'ı kendisi alır,
          60 güne uzatır ve Instagram hesabını bulur. Bu alan yalnızca elinizde hazır bir token varsa ya da
          kaynağı değiştirmek istediğinizde kullanılır. “Etiketli gönderiler” için token'ın <b>tags</b> izni olmalıdır.
        </p>
        <div className="grid md:grid-cols-2 gap-3">
          <div>
            <label className="block text-xs uppercase tracking-wider text-gray-500 mb-1">Access Token {settings.token_set && <span className="text-green-600">(kayıtlı)</span>}</label>
            <input type="password" value={form.access_token} onChange={(e) => setForm({ ...form, access_token: e.target.value })}
              placeholder={settings.token_set ? "•••••• (değiştirmek için yeni token yazın)" : "EAAB..."}
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm" />
          </div>
          <div>
            <label className="block text-xs uppercase tracking-wider text-gray-500 mb-1">Instagram User ID</label>
            <input value={form.ig_user_id} onChange={(e) => setForm({ ...form, ig_user_id: e.target.value })}
              placeholder="1784xxxxxxxxxxx" className="w-full border border-gray-300 rounded px-3 py-2 text-sm" />
          </div>
          <div>
            <label className="block text-xs uppercase tracking-wider text-gray-500 mb-1">Kaynak</label>
            <select value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })}
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm">
              <option value="media">Kendi Gönderilerim (media)</option>
              <option value="tags">Etiketlendiğim Gönderiler (tags)</option>
            </select>
          </div>
        </div>
        <div className="flex gap-2 mt-4">
          <button onClick={saveSettings} disabled={busy} className="flex items-center gap-1.5 px-4 py-2 bg-black text-white rounded-lg text-sm disabled:opacity-50">
            <Save size={14} /> Kaydet
          </button>
        </div>
      </div>
      )}

      {/* Elle gönderi ekle */}
      <div className="bg-white border rounded-xl p-4 mb-4">
        <h2 className="text-sm font-bold uppercase tracking-wider mb-1">Elle Gönderi Ekle</h2>
        <p className="text-xs text-gray-500 mb-3">Token olmadan da gönderi ekleyebilirsiniz (görsel URL + Instagram linki).</p>
        <div className="grid md:grid-cols-2 gap-3">
          <input value={manual.image} onChange={(e) => setManual({ ...manual, image: e.target.value })} placeholder="Görsel URL (zorunlu)" className="border border-gray-300 rounded px-3 py-2 text-sm" />
          <input value={manual.permalink} onChange={(e) => setManual({ ...manual, permalink: e.target.value })} placeholder="Instagram gönderi linki" className="border border-gray-300 rounded px-3 py-2 text-sm" />
          <input value={manual.product_link} onChange={(e) => setManual({ ...manual, product_link: e.target.value })} placeholder="Ürün linki (opsiyonel — tıklayınca ürüne gider)" className="border border-gray-300 rounded px-3 py-2 text-sm" />
          <input value={manual.caption} onChange={(e) => setManual({ ...manual, caption: e.target.value })} placeholder="Açıklama (opsiyonel)" className="border border-gray-300 rounded px-3 py-2 text-sm" />
        </div>
        <button onClick={addManual} disabled={busy} className="flex items-center gap-1.5 px-4 py-2 mt-3 bg-gray-900 text-white rounded-lg text-sm disabled:opacity-50">
          <Plus size={14} /> Ekle
        </button>
      </div>

      {/* Gönderiler + InstaShop ürün bağlama */}
      <div className="bg-white border rounded-xl p-4">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-1">
          <h2 className="text-sm font-bold uppercase tracking-wider">InstaShop Gönderileri ({filteredPosts.length}/{posts.length})</h2>
          <div className="flex items-center gap-1 text-xs">
            {[["all", "Tümü"], ["media", "Kendi"], ["tagged", "Etiketli"], ["manual", "Elle"]].map(([k, lbl]) => (
              <button key={k} onClick={() => setFilter(k)}
                className={`px-2.5 py-1 rounded-full border ${filter === k ? "bg-black text-white border-black" : "border-gray-200 text-gray-500 hover:bg-gray-50"}`}>
                {lbl}
              </button>
            ))}
          </div>
        </div>
        <p className="text-xs text-gray-500 mb-3">
          <b>Göster</b> = anasayfa “#FACETTE × YOU” bölümünde çıkar. Her gönderiye <b>ürün bağla</b> → müşteri anasayfada
          görselin üzerine gelince (mobilde dokununca) o ürünler şık kartlarla belirir.
        </p>
        {filteredPosts.length === 0 ? (
          <p className="text-sm text-gray-400 py-6 text-center">Bu filtrede gönderi yok. Token ile “Şimdi Çek” veya elle ekleyin.</p>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
            {filteredPosts.map((p) => {
              const prods = p.products || [];
              const isOpen = pickerFor === p.id;
              const kl = kindLabel(p);
              return (
                <div key={p.id} className={`border rounded-lg overflow-hidden flex flex-col ${p.active === false ? "opacity-50" : ""}`}>
                  <div className="relative aspect-square bg-gray-100">
                    <img src={p.image} alt="" className="w-full h-full object-cover" loading="lazy" />
                    <span className={`absolute top-1 left-1 text-[9px] px-1.5 py-0.5 rounded text-white ${kl === "etiketli" ? "bg-fuchsia-600/80" : kl === "elle" ? "bg-gray-700/80" : "bg-black/60"}`}>
                      {kl}
                    </span>
                    {prods.length > 0 && (
                      <span className="absolute top-1 right-1 text-[9px] px-1.5 py-0.5 rounded bg-emerald-600 text-white flex items-center gap-0.5">
                        <ShoppingBag size={9} /> {prods.length}
                      </span>
                    )}
                  </div>
                  <div className="p-2 space-y-2 flex-1 flex flex-col">
                    {/* Bağlı ürün çipleri */}
                    {prods.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {prods.map((pr) => (
                          <span key={pr.id} className="inline-flex items-center gap-1 bg-gray-100 rounded-full pl-1 pr-1.5 py-0.5 text-[10px] max-w-full">
                            {pr.image && <img src={pr.image} alt="" className="w-4 h-4 rounded-full object-cover" />}
                            <span className="truncate max-w-[90px]">{pr.title || pr.id}</span>
                            <button onClick={() => removeProductFromPost(p, pr.id)} className="text-gray-400 hover:text-red-600"><X size={10} /></button>
                          </span>
                        ))}
                      </div>
                    )}
                    {/* Ürün ekle */}
                    <div className="relative">
                      <button onClick={() => { setPickerFor(isOpen ? null : p.id); setPQuery(""); setPResults([]); }}
                        className="w-full flex items-center justify-center gap-1 border border-dashed border-gray-300 rounded px-2 py-1 text-[11px] text-gray-600 hover:border-black hover:text-black">
                        <Tag size={11} /> {isOpen ? "Kapat" : "Ürün ekle"}
                      </button>
                      {isOpen && (
                        <div className="absolute z-20 left-0 right-0 mt-1 bg-white border rounded-lg shadow-lg p-2">
                          <div className="flex items-center gap-1 border rounded px-2 py-1 mb-1">
                            <Search size={12} className="text-gray-400" />
                            <input autoFocus value={pQuery} onChange={(e) => searchProducts(e.target.value)}
                              placeholder="Ürün ara (min 2 harf)…" className="flex-1 text-[11px] outline-none" />
                          </div>
                          <div className="max-h-52 overflow-auto">
                            {pBusy && <p className="text-[11px] text-gray-400 py-2 text-center">Aranıyor…</p>}
                            {!pBusy && pQuery.length >= 2 && pResults.length === 0 && (
                              <p className="text-[11px] text-gray-400 py-2 text-center">Sonuç yok</p>
                            )}
                            {pResults.map((pr) => (
                              <button key={pr.id} onClick={() => addProductToPost(p, pr)}
                                className="w-full flex items-center gap-2 p-1 hover:bg-gray-50 rounded text-left">
                                <img src={(Array.isArray(pr.images) && pr.images[0]) || pr.image || ""} alt="" className="w-8 h-8 rounded object-cover bg-gray-100 shrink-0" />
                                <span className="flex-1 min-w-0">
                                  <span className="block text-[11px] truncate">{pr.name}</span>
                                  <span className="block text-[10px] text-gray-500">{pr.price != null ? `${Number(pr.price).toLocaleString("tr-TR")} TL` : ""}</span>
                                </span>
                                <Plus size={12} className="text-emerald-600 shrink-0" />
                              </button>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                    <div className="flex items-center justify-between mt-auto pt-1">
                      {p.permalink ? (
                        <a href={p.permalink} target="_blank" rel="noreferrer" className="text-gray-400 hover:text-black" title="Instagram'da aç"><ExternalLink size={13} /></a>
                      ) : <span />}
                      <div className="flex items-center gap-2">
                        <button onClick={() => toggleActive(p)} title={p.active === false ? "Göster" : "Gizle"}
                          className={`text-[10px] font-semibold ${p.active === false ? "text-gray-400 hover:text-black" : "text-emerald-600 hover:text-emerald-800"}`}>
                          {p.active === false ? "Gizli" : "Göster ✓"}
                        </button>
                        <button onClick={() => del(p)} className="text-red-500 hover:text-red-700"><Trash2 size={13} /></button>
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
