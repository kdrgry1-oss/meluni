/**
 * =============================================================================
 * Account.jsx — Üye Hesabım Sayfası (Suud / Zara stili)
 * =============================================================================
 * Mulish font + minimal siyah/beyaz tasarım. Mobile-first; pill tab navigation.
 *
 * Sekmeler: Profil, Siparişlerim, Adreslerim, Favorilerim
 *
 * BACKEND uçları (değişmedi):
 *   - GET    /api/my-orders                — Üye siparişleri (paginated)
 *   - GET    /api/my-addresses             — Üye adresleri
 *   - PUT    /api/users/me                 — Profil güncelle
 *   - POST   /api/addresses                — Adres ekle
 *   - PUT    /api/addresses/{id}           — Adres güncelle
 *   - DELETE /api/addresses/{id}           — Adres sil
 *
 * Yan modüller: ../components/ProvinceDistrictSelect, ../context/AuthContext
 * =============================================================================
 */
import { useState, useEffect } from "react";
import { Navigate, useSearchParams } from "react-router-dom";
import {
  User, Package, MapPin, Bookmark, LogOut, ChevronRight, ChevronDown,
  Eye, Truck, CheckCircle, Clock, X, Edit2, Trash2, Plus, Star,
  ShoppingBag, Calendar, Mail, Phone, Lock, Gift, Copy
} from "lucide-react";
import axios from "axios";
import { toast } from "sonner";
import Header from "../components/Header";
import Footer from "../components/Footer";
import ProvinceDistrictSelect from "../components/ProvinceDistrictSelect";
import ProductCard from "../components/ProductCard";
import { useAuth } from "../context/AuthContext";
import { useFavorites } from "../context/FavoritesContext";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const MENU_ITEMS = [
  { id: "profile",   label: "Profil",       icon: User },
  { id: "orders",    label: "Siparişlerim", icon: Package },
  { id: "addresses", label: "Adreslerim",   icon: MapPin },
  { id: "favorites", label: "Kaydedilenler", icon: Bookmark },
  { id: "referral",  label: "Davet Et",     icon: Gift },
  { id: "security",  label: "Şifre",        icon: Lock },
];

const ORDER_STATUS = {
  pending:     { label: "Onay Bekliyor",  cls: "bg-yellow-50  text-yellow-800  border-yellow-200",  icon: Clock },
  confirmed:   { label: "Onaylandı",      cls: "bg-blue-50    text-blue-800    border-blue-200",    icon: CheckCircle },
  processing:  { label: "Hazırlanıyor",   cls: "bg-indigo-50  text-indigo-800  border-indigo-200",  icon: Package },
  shipped:     { label: "Kargoda",        cls: "bg-purple-50  text-purple-800  border-purple-200",  icon: Truck },
  delivered:   { label: "Teslim Edildi",  cls: "bg-emerald-50 text-emerald-800 border-emerald-200", icon: CheckCircle },
  awaiting_payment: { label: "Ödeme Bekleniyor", cls: "bg-amber-50 text-amber-800 border-amber-200", icon: Clock },
  payment_notified: { label: "Ödeme Bildirimi Alındı", cls: "bg-amber-50 text-amber-800 border-amber-200", icon: Clock },
  preparing:   { label: "Hazırlanıyor",   cls: "bg-indigo-50  text-indigo-800  border-indigo-200",  icon: Package },
  ready_to_ship: { label: "Kargoya Hazır", cls: "bg-indigo-50 text-indigo-800 border-indigo-200", icon: Package },
  in_transit:  { label: "Yolda",          cls: "bg-purple-50 text-purple-800 border-purple-200", icon: Truck },
  out_for_delivery: { label: "Dağıtımda", cls: "bg-purple-50 text-purple-800 border-purple-200", icon: Truck },
  undelivered: { label: "Teslim Edilemedi", cls: "bg-orange-50 text-orange-800 border-orange-200", icon: Truck },
  return_requested: { label: "İade Talebi Alındı", cls: "bg-rose-50 text-rose-800 border-rose-200", icon: Package },
  return_approved: { label: "İade Onaylandı", cls: "bg-rose-50 text-rose-800 border-rose-200", icon: CheckCircle },
  return_rejected: { label: "İade Değerlendirildi", cls: "bg-gray-50 text-gray-700 border-gray-200", icon: X },
  return_in_transit: { label: "İade Kargoda", cls: "bg-pink-50 text-pink-800 border-pink-200", icon: Truck },
  returned:    { label: "İade Tamamlandı", cls: "bg-red-50 text-red-700 border-red-200", icon: CheckCircle },
  refunded:    { label: "İade Bedeli Ödendi", cls: "bg-red-50 text-red-700 border-red-200", icon: CheckCircle },
  partial_refunded: { label: "Kısmi İade Yapıldı", cls: "bg-rose-50 text-rose-700 border-rose-200", icon: CheckCircle },
  cancel_requested: { label: "İptal Talebi Alındı", cls: "bg-orange-50 text-orange-800 border-orange-300", icon: Clock },
  cancelled:   { label: "İptal Edildi",   cls: "bg-red-50     text-red-700     border-red-200",     icon: X },
};

const formatDate = (str, opts = { day: "numeric", month: "long", year: "numeric" }) => {
  if (!str) return "-";
  try { return new Date(str).toLocaleDateString("tr-TR", opts); } catch { return "-"; }
};

const initialsOf = (u) => {
  const a = (u?.first_name || "").trim();
  const b = (u?.last_name  || "").trim();
  if (a || b) return ((a[0] || "") + (b[0] || "")).toUpperCase();
  const e = u?.email || "";
  return e ? e.slice(0, 2).toUpperCase() : "FA";
};

const greeting = () => {
  const h = new Date().getHours();
  if (h < 6)  return "İyi geceler";
  if (h < 12) return "Günaydın";
  if (h < 18) return "İyi günler";
  return "İyi akşamlar";
};

export default function Account() {
  const { user, logout, loading: authLoading, setUser } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [activeTab, setActiveTab] = useState(searchParams.get("tab") || "profile");

  const [orders, setOrders] = useState([]);
  const [addresses, setAddresses] = useState([]);
  const [loading, setLoading] = useState(false);
  const [expandedOrder, setExpandedOrder] = useState(null);
  const [editingProfile, setEditingProfile] = useState(false);
  const [editingAddress, setEditingAddress] = useState(null);

  const [profileForm, setProfileForm] = useState({ first_name: "", last_name: "", phone: "", height_cm: "", weight_kg: "" });
  const [addressForm, setAddressForm] = useState({
    title: "", first_name: "", last_name: "", phone: "",
    address: "", city: "", district: "", postal_code: "", is_default: false,
    is_corporate: false, company_name: "", tax_no: "", tax_office: "",
  });

  const [mkt, setMkt] = useState({ email: false, sms: false });
  const [refData, setRefData] = useState(null);
  const [refCopied, setRefCopied] = useState(false);
  useEffect(() => {
    if (user) {
      setProfileForm({
        first_name: user.first_name || "",
        last_name:  user.last_name  || "",
        phone:      user.phone      || "",
        height_cm:  user.height_cm  ?? "",
        weight_kg:  user.weight_kg  ?? "",
      });
      const mp = user.marketing_prefs || {};
      setMkt({ email: !!mp.email, sms: !!mp.sms });
    }
  }, [user]);

  // İYS pazarlama izni aç/kapa → backend hem kaydeder hem İYS'ye ONAY/RET bildirir.
  const saveMkt = async (next) => {
    setMkt(next);
    try {
      const token = localStorage.getItem("token");
      await axios.post(`${API}/iys/consent/update`, next, { headers: { Authorization: `Bearer ${token}` } });
      setUser?.((u) => ({ ...(u || {}), marketing_prefs: next, accepts_marketing: next.email || next.sms }));
      toast.success("Tercihiniz güncellendi");
    } catch {
      toast.error("Güncellenemedi");
    }
  };

  useEffect(() => {
    if (activeTab === "orders") fetchOrders();
    else if (activeTab === "addresses") fetchAddresses();
    else if (activeTab === "referral" && !refData) fetchReferral();
  }, [activeTab]);

  const fetchReferral = async () => {
    try {
      const token = localStorage.getItem("token");
      const res = await axios.get(`${API}/referrals/my`, { headers: { Authorization: `Bearer ${token}` } });
      setRefData(res.data);
    } catch { setRefData({ enabled: false }); }
    // C3: puan bakiyesi + kademe (aynı sekmede gösterilir)
    try {
      const token = localStorage.getItem("token");
      const r = await axios.get(`${API}/loyalty/me`, { headers: { Authorization: `Bearer ${token}` } });
      setLoyData(r.data);
    } catch { setLoyData(null); }
  };
  const [loyData, setLoyData] = useState(null);

  useEffect(() => {
    const orderNum = searchParams.get("order");
    if (orderNum) {
      setActiveTab("orders");
      toast.success(`Siparişiniz oluşturuldu: ${orderNum}`);
      setSearchParams({});
    }
  }, [searchParams]);

  const fetchOrders = async () => {
    setLoading(true);
    try {
      const token = localStorage.getItem("token");
      const res = await axios.get(`${API}/my-orders`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      setOrders(res.data.orders || []);
    } catch (err) {
      console.error(err);
      setOrders([]);
    } finally {
      setLoading(false);
    }
  };

  const fetchAddresses = async () => {
    setLoading(true);
    try {
      const token = localStorage.getItem("token");
      const res = await axios.get(`${API}/my-addresses`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      setAddresses(res.data.addresses || []);
    } catch (err) {
      setAddresses([]);
    } finally {
      setLoading(false);
    }
  };

  const handleUpdateProfile = async (e) => {
    e.preventDefault();
    try {
      const token = localStorage.getItem("token");
      await axios.put(`${API}/users/me`, profileForm, {
        headers: { Authorization: `Bearer ${token}` },
      });
      toast.success("Profil güncellendi");
      // Context'i güncelle → beden önerisi anında yeni boy/kiloyla çalışır (yeniden yükleme gerekmez)
      setUser?.((u) => ({ ...(u || {}), ...profileForm }));
      setEditingProfile(false);
    } catch {
      toast.error("Güncelleme başarısız");
    }
  };

  const handleSaveAddress = async (e) => {
    e.preventDefault();
    try {
      const token = localStorage.getItem("token");
      const headers = { Authorization: `Bearer ${token}` };
      if (editingAddress?.id) {
        await axios.put(`${API}/addresses/${editingAddress.id}`, addressForm, { headers });
        toast.success("Adres güncellendi");
      } else {
        await axios.post(`${API}/addresses`, addressForm, { headers });
        toast.success("Adres eklendi");
      }
      setEditingAddress(null);
      setAddressForm({
        title: "", first_name: "", last_name: "", phone: "",
        address: "", city: "", district: "", postal_code: "", is_default: false,
        is_corporate: false, company_name: "", tax_no: "", tax_office: "",
      });
      fetchAddresses();
    } catch {
      toast.error("İşlem başarısız");
    }
  };

  const handleDeleteAddress = async (id) => {
    if (!await window.appConfirm("Adresi silmek istediğinize emin misiniz?")) return;
    try {
      const token = localStorage.getItem("token");
      await axios.delete(`${API}/addresses/${id}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      toast.success("Adres silindi");
      fetchAddresses();
    } catch {
      toast.error("Silme başarısız");
    }
  };

  const switchTab = (id) => {
    setActiveTab(id);
    setSearchParams({ tab: id });
  };

  if (authLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-white">
        <div className="animate-spin rounded-full h-8 w-8 border-2 border-black border-t-transparent" />
      </div>
    );
  }
  if (!user) return <Navigate to="/giris" />;

  return (
    <div className="min-h-screen bg-[#fafafa]" data-testid="account-page">
      <Header />

      {/* ───────────────────── Hero / Welcome ───────────────────── */}
      <section className="bg-white border-b border-gray-100">
        <div className="max-w-screen-xl mx-auto px-4 py-8 md:py-12">
          <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-6">
            <div className="flex items-center gap-4 md:gap-6">
              <div
                className="w-16 h-16 md:w-20 md:h-20 rounded-full bg-black text-white flex items-center justify-center text-xl md:text-2xl font-light tracking-wider shrink-0"
                data-testid="account-avatar"
                aria-hidden
              >
                {initialsOf(user)}
              </div>
              <div>
                <p className="text-xs uppercase tracking-[0.2em] text-gray-400 mb-1">{greeting()}</p>
                <h1 className="text-2xl md:text-3xl font-light tracking-wide text-black" data-testid="account-greeting">
                  {user.first_name || (user.email ? user.email.split("@")[0] : "Hesabım")}
                </h1>
                <p className="text-xs md:text-sm text-gray-500 mt-1 flex items-center gap-2 flex-wrap">
                  <Mail size={12} /> {user.email}
                  {user.created_at && (
                    <>
                      <span className="text-gray-300">•</span>
                      <Calendar size={12} /> Üye: {formatDate(user.created_at, { month: "short", year: "numeric" })}
                    </>
                  )}
                </p>
              </div>
            </div>
            <button
              onClick={logout}
              className="flex items-center gap-2 text-xs uppercase tracking-[0.2em] text-gray-500 hover:text-black transition-colors self-start md:self-center"
              data-testid="logout-btn"
            >
              <LogOut size={14} /> Çıkış Yap
            </button>
          </div>
        </div>
      </section>

      {/* ───────────────────── Tab Pills ───────────────────── */}
      <nav className="sticky top-0 z-10 bg-white border-b border-gray-100">
        <div className="max-w-screen-xl mx-auto px-4">
          <div className="flex overflow-x-auto scrollbar-hide gap-1 py-2">
            {MENU_ITEMS.map((m) => {
              const Icon = m.icon;
              const active = activeTab === m.id;
              return (
                <button
                  key={m.id}
                  onClick={() => switchTab(m.id)}
                  data-testid={`tab-${m.id}`}
                  className={`flex items-center gap-2 px-4 py-2.5 text-xs uppercase tracking-[0.15em] whitespace-nowrap transition-all border-b-2 ${
                    active
                      ? "text-black border-black font-semibold"
                      : "text-gray-400 border-transparent hover:text-black"
                  }`}
                >
                  <Icon size={14} />
                  {m.label}
                </button>
              );
            })}
          </div>
        </div>
      </nav>

      {/* ───────────────────── Content ───────────────────── */}
      <main className="max-w-screen-xl mx-auto px-4 py-8 md:py-10">
        {activeTab === "profile"   && <ProfilePane user={user} editing={editingProfile} setEditing={setEditingProfile} form={profileForm} setForm={setProfileForm} onSubmit={handleUpdateProfile} mkt={mkt} saveMkt={saveMkt} />}
        {activeTab === "orders"    && <OrdersPane loading={loading} orders={orders} expandedOrder={expandedOrder} setExpandedOrder={setExpandedOrder} onChanged={fetchOrders} />}
        {activeTab === "addresses" && <AddressesPane loading={loading} addresses={addresses} editing={editingAddress} setEditing={setEditingAddress} form={addressForm} setForm={setAddressForm} onSubmit={handleSaveAddress} onDelete={handleDeleteAddress} />}
        {activeTab === "favorites" && <FavoritesPane />}
        {activeTab === "referral"  && (
          <div className="bg-white">
            {/* C3: Sadakat puanı kartı — bakiye + kademe + kazanım oranı */}
            {loyData && loyData.enabled !== false && (
              <div className="max-w-xl mx-auto mb-6 border border-gray-100 rounded-lg p-5" data-testid="loyalty-card">
                <div className="flex items-center justify-between flex-wrap gap-3">
                  <div>
                    <p className="text-[11px] tracking-[0.2em] uppercase text-gray-500 mb-1">Puanlarım</p>
                    <p className="text-2xl font-light text-black">{Number(loyData.points || 0).toFixed(2)} ₺</p>
                  </div>
                  <div className="text-right">
                    <span className={`inline-block text-[11px] font-semibold px-2.5 py-1 rounded-full border ${
                      loyData.tier === "platinum" ? "bg-slate-100 text-slate-700 border-slate-300"
                      : loyData.tier === "gold" ? "bg-amber-50 text-amber-700 border-amber-200"
                      : "bg-gray-50 text-gray-500 border-gray-200"}`}>
                      {loyData.tier === "platinum" ? "PLATINUM" : loyData.tier === "gold" ? "GOLD" : "SILVER"} ÜYE
                    </span>
                    <p className="text-[11px] text-gray-500 mt-1.5">Her siparişte %{loyData.effective_earn_pct} puan kazanırsın</p>
                  </div>
                </div>
                {loyData.next_threshold && (
                  <p className="text-[11px] text-gray-400 mt-3">
                    Son 12 ay harcaman: {Number(loyData.spend_12m || 0).toFixed(0)} ₺ — bir üst kademe için {Math.max(0, loyData.next_threshold - (loyData.spend_12m || 0)).toFixed(0)} ₺ kaldı
                  </p>
                )}
                <p className="text-[11px] text-gray-400 mt-1">1 puan = 1 TL · Ödemede "Puan Kullan" ile harcanır (sepetin en fazla %{loyData.redeem_max_pct}'i).</p>
              </div>
            )}
            {refData && refData.enabled === false ? (
              <div className="text-center text-gray-500 py-16 text-sm">Referans programı şu anda aktif değil.</div>
            ) : !refData ? (
              <div className="text-center text-gray-400 py-16 text-sm">Yükleniyor…</div>
            ) : (
              <div className="max-w-xl mx-auto text-center py-6">
                <div className="inline-flex items-center justify-center w-14 h-14 rounded-full bg-stone-100 mb-4">
                  <Gift size={24} strokeWidth={1.5} className="text-stone-800" />
                </div>
                <h2 className="text-xl font-light text-black mb-2">Arkadaşını davet et, kazan</h2>
                <p className="text-sm text-gray-600 leading-relaxed mb-6">
                  Davet linkini paylaş. Arkadaşın ilk alışverişini yaptığında
                  {refData.referee_reward?.value ? (
                    <> hem sana hem ona <strong>{refData.referee_reward.type === "percent" ? `%${refData.referee_reward.value}` : `${refData.referee_reward.value}₺`} indirim</strong> kuponu</>
                  ) : <> ikinize de indirim kuponu</>} verilir.
                </p>
                <div className="flex items-stretch gap-2 max-w-md mx-auto mb-3">
                  <input readOnly value={refData.share_url || ""}
                    className="flex-1 bg-gray-50 border border-gray-200 px-4 py-3 text-sm text-gray-700 focus:outline-none" />
                  <button onClick={() => {
                    try { navigator.clipboard.writeText(refData.share_url || ""); setRefCopied(true); setTimeout(() => setRefCopied(false), 1800); } catch { /* yok */ }
                  }} className="bg-stone-900 hover:bg-stone-800 text-white px-4 flex items-center gap-1.5 text-xs tracking-wider uppercase">
                    <Copy size={14} /> {refCopied ? "Kopyalandı" : "Kopyala"}
                  </button>
                </div>
                <p className="text-xs text-gray-500 mb-8">
                  Referans kodun: <span className="font-mono font-semibold text-black">{refData.code}</span>
                </p>
                <div className="grid grid-cols-2 gap-4 max-w-xs mx-auto">
                  <div className="border border-gray-100 rounded p-4">
                    <div className="text-2xl font-light text-black">{refData.invited ?? 0}</div>
                    <div className="text-[11px] tracking-wide uppercase text-gray-500 mt-1">Davet edilen</div>
                  </div>
                  <div className="border border-gray-100 rounded p-4">
                    <div className="text-2xl font-light text-emerald-700">{refData.rewarded ?? 0}</div>
                    <div className="text-[11px] tracking-wide uppercase text-gray-500 mt-1">Ödül kazanılan</div>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
        {activeTab === "security"  && <SecurityPane />}
      </main>

      <Footer />
    </div>
  );
}

/* ═══════════════════════════════ PROFILE ═══════════════════════════════ */

function ProfilePane({ user, editing, setEditing, form, setForm, onSubmit, mkt = {}, saveMkt = () => {} }) {
  return (
    <div className="grid lg:grid-cols-3 gap-6 max-w-4xl">
      <div className="lg:col-span-2 bg-white border border-gray-100 p-6 md:p-8">
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-sm uppercase tracking-[0.2em] text-gray-700">Profil Bilgileri</h2>
          {!editing && (
            <button onClick={() => setEditing(true)} data-testid="edit-profile-btn"
              className="text-xs uppercase tracking-[0.15em] text-black underline-offset-4 hover:underline flex items-center gap-1">
              <Edit2 size={12} /> Düzenle
            </button>
          )}
        </div>

        {editing ? (
          <form onSubmit={onSubmit} className="space-y-5">
            <div className="grid md:grid-cols-2 gap-5">
              <Field label="Ad" value={form.first_name} onChange={(v) => setForm({ ...form, first_name: v })} />
              <Field label="Soyad" value={form.last_name} onChange={(v) => setForm({ ...form, last_name: v })} />
              <Field label="E-posta" value={user.email} disabled />
              <Field label="Telefon" value={form.phone} onChange={(v) => setForm({ ...form, phone: v })} type="tel" />
              <Field label="Boy (cm)" value={form.height_cm} onChange={(v) => setForm({ ...form, height_cm: v })} type="number" />
              <Field label="Kilo (kg)" value={form.weight_kg} onChange={(v) => setForm({ ...form, weight_kg: v })} type="number" />
            </div>
            <p className="text-[11px] text-gray-400 -mt-2">Boy/kilo, ürün sayfasında size en uygun bedeni önermek için kullanılır.</p>
            <div className="flex gap-3 pt-2">
              <button type="submit" data-testid="save-profile-btn"
                className="bg-black text-white px-8 py-3 text-xs uppercase tracking-[0.2em] hover:bg-gray-800 transition-colors">
                Kaydet
              </button>
              <button type="button" onClick={() => setEditing(false)}
                className="border border-black text-black px-8 py-3 text-xs uppercase tracking-[0.2em] hover:bg-black hover:text-white transition-colors">
                İptal
              </button>
            </div>
          </form>
        ) : (
          <dl className="grid md:grid-cols-2 gap-5">
            <Row k="Ad Soyad"      v={`${user.first_name || "-"} ${user.last_name || ""}`.trim()} />
            <Row k="E-posta"       v={user.email} />
            <Row k="Telefon"       v={user.phone || "-"} icon={Phone} />
            <Row k="Üyelik Tarihi" v={formatDate(user.created_at)} icon={Calendar} />
          </dl>
        )}

        {/* Pazarlama İzinleri (İYS) — aç/kapa; kapatınca İYS'ye RED bildirilir */}
        <div className="mt-6 pt-6 border-t border-gray-100">
          <p className="text-[10px] uppercase tracking-[0.2em] text-gray-500 mb-3">Pazarlama İzinleri</p>
          <div className="space-y-2.5">
            <label className="flex items-center justify-between text-sm cursor-pointer">
              <span className="text-gray-700">E-posta ile kampanya/fırsat</span>
              <input type="checkbox" checked={mkt.email} onChange={(e) => saveMkt({ ...mkt, email: e.target.checked })} className="accent-black w-4 h-4" data-testid="mkt-email" />
            </label>
            <label className="flex items-center justify-between text-sm cursor-pointer">
              <span className="text-gray-700">SMS ile kampanya/fırsat</span>
              <input type="checkbox" checked={mkt.sms} onChange={(e) => saveMkt({ ...mkt, sms: e.target.checked })} className="accent-black w-4 h-4" data-testid="mkt-sms" />
            </label>
          </div>
          <p className="text-[11px] text-gray-400 mt-2 leading-relaxed">
            İzinleriniz İYS'ye işlenir. Kapattığınızda ticari ileti gönderimi durur.
          </p>
        </div>
      </div>

      <aside className="bg-black text-white p-6 md:p-8 flex flex-col justify-between">
        <div>
          <p className="text-[10px] uppercase tracking-[0.3em] text-gray-400 mb-3">Facette Üye Avantajı</p>
          <h3 className="text-lg font-light leading-snug mb-4">
            Yeni koleksiyona üyelere özel <span className="font-semibold">erken erişim</span>.
          </h3>
          <p className="text-xs text-gray-300 leading-relaxed">
            Ücretsiz kargo, kişisel kombin önerileri ve tüm sezonlara özel kampanyalardan ilk siz haberdar olun.
          </p>
        </div>
        <a href="/" className="text-xs uppercase tracking-[0.2em] mt-6 inline-flex items-center gap-2 group">
          Yeni Sezonu Keşfet
          <ChevronRight size={14} className="transition-transform group-hover:translate-x-1" />
        </a>
      </aside>
    </div>
  );
}

function Field({ label, value, onChange, type = "text", disabled = false }) {
  return (
    <div>
      <label className="block text-[10px] uppercase tracking-[0.2em] text-gray-500 mb-2">{label}</label>
      <input
        type={type}
        value={value || ""}
        disabled={disabled}
        autoComplete={type === "password" ? "off" : undefined}
        onChange={onChange ? (e) => onChange(e.target.value) : undefined}
        className={`w-full border-0 border-b ${disabled ? "border-gray-200 text-gray-400" : "border-gray-300 focus:border-black"} bg-transparent py-2 text-sm focus:outline-none transition-colors`}
      />
    </div>
  );
}

function Row({ k, v, icon: Icon }) {
  return (
    <div>
      <dt className="text-[10px] uppercase tracking-[0.2em] text-gray-500 mb-1.5 flex items-center gap-1.5">
        {Icon && <Icon size={11} />}
        {k}
      </dt>
      <dd className="text-sm text-black font-light">{v}</dd>
    </div>
  );
}

/* ═══════════════════════════════ ORDERS ═══════════════════════════════ */

function OrdersPane({ loading, orders, expandedOrder, setExpandedOrder, onChanged }) {
  if (loading) {
    return (
      <div className="space-y-3">
        {[1, 2, 3].map((i) => (
          <div key={i} className="bg-white border border-gray-100 h-32 animate-pulse" />
        ))}
      </div>
    );
  }

  if (orders.length === 0) {
    return (
      <div className="bg-white border border-gray-100 py-20 px-6 text-center" data-testid="orders-empty">
        <div className="w-12 h-12 rounded-full bg-gray-100 flex items-center justify-center mx-auto mb-4">
          <ShoppingBag size={20} className="text-gray-400" />
        </div>
        <p className="text-sm text-gray-500 mb-6 tracking-wide">Henüz siparişiniz bulunmuyor</p>
        <a href="/" className="inline-block bg-black text-white px-8 py-3 text-xs uppercase tracking-[0.2em] hover:bg-gray-800 transition-colors">
          Alışverişe Başla
        </a>
      </div>
    );
  }

  return (
    <div className="space-y-3 max-w-4xl">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm uppercase tracking-[0.2em] text-gray-700">
          Siparişlerim <span className="text-gray-400">({orders.length})</span>
        </h2>
        <a href="/siparis-takip" className="text-xs uppercase tracking-[0.15em] text-gray-500 hover:text-black underline-offset-4 hover:underline">
          Sipariş Takip
        </a>
      </div>

      {orders.map((order) => (
        <OrderCard
          key={order.id || order.order_number}
          order={order}
          expanded={expandedOrder === (order.id || order.order_number)}
          onToggle={() => setExpandedOrder(expandedOrder === (order.id || order.order_number) ? null : (order.id || order.order_number))}
          onChanged={onChanged}
        />
      ))}
    </div>
  );
}

function OrderCard({ order, expanded, onToggle, onChanged }) {
  const status = ORDER_STATUS[order.status] || ORDER_STATUS.pending;
  const _deliveredAt = order.delivered_at ? new Date(order.delivered_at) : null;
  const canReturn = order.status === "delivered" && _deliveredAt && (Date.now() - _deliveredAt.getTime()) <= 14 * 24 * 3600 * 1000;
  // Müşteri iptali: yalnızca "Hazırlanıyor" durumuna geçmeden. Backend guard ile birebir aynı set.
  const CANCELLABLE = ["pending", "awaiting_payment", "payment_notified", "confirmed"];
  const canCancel = CANCELLABLE.includes(order.status);
  const [cancelling, setCancelling] = useState(false);
  const handleCancel = async () => {
    if (!window.confirm("Siparişinizi iptal etmek istediğinize emin misiniz?")) return;
    setCancelling(true);
    try {
      const token = localStorage.getItem("token");
      await axios.post(`${API}/my-orders/${order.id || order.order_number}/cancel`, {}, {
        headers: { Authorization: `Bearer ${token}` },
      });
      toast.success("Siparişiniz iptal edildi");
      onChanged && onChanged();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Sipariş iptal edilemedi");
    } finally {
      setCancelling(false);
    }
  };
  const StatusIcon = status.icon;
  const items = order.items || [];
  const itemCount = items.reduce((s, i) => s + (i.quantity || 1), 0);
  const previewImages = items.slice(0, 4);
  const trackingUrl = order.cargo?.tracking_url || order.cargo_tracking_link || order.cargo_tracking_url;
  const trackingNumber = order.cargo?.tracking_number || order.cargo_tracking_number;

  return (
    <article className="bg-white border border-gray-100 transition-shadow hover:shadow-sm" data-testid={`order-card-${order.id}`}>
      {/* Header row */}
      <button
        onClick={onToggle}
        className="w-full px-4 md:px-6 py-4 flex items-center gap-4 text-left"
      >
        {/* Image stack */}
        <div className="flex -space-x-2 shrink-0">
          {previewImages.length > 0 ? previewImages.map((it, i) => (
            <div
              key={i}
              className="w-12 h-14 md:w-14 md:h-16 bg-gray-100 border-2 border-white overflow-hidden"
              style={{ zIndex: previewImages.length - i }}
            >
              {it.image ? (
                <img src={it.image} alt={it.name || it.product_name || ""} className="w-full h-full object-contain" />
              ) : (
                <div className="w-full h-full bg-gradient-to-br from-gray-50 to-gray-200" />
              )}
            </div>
          )) : (
            <div className="w-12 h-14 md:w-14 md:h-16 bg-gray-100 flex items-center justify-center">
              <Package size={18} className="text-gray-300" />
            </div>
          )}
          {items.length > 4 && (
            <div className="w-12 h-14 md:w-14 md:h-16 bg-black text-white text-[10px] flex items-center justify-center border-2 border-white">
              +{items.length - 4}
            </div>
          )}
        </div>

        {/* Info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2 flex-wrap">
            <span className="text-sm font-medium tracking-wide truncate">{order.order_number}</span>
            <span className="text-[11px] text-gray-400">•</span>
            <span className="text-[11px] text-gray-500">{formatDate(order.created_at)}</span>
          </div>
          <p className="text-xs text-gray-500 mt-1">
            {itemCount} ürün · <span className="text-black font-medium">{(order.total ?? 0).toFixed(2)} ₺</span>
          </p>
        </div>

        {/* Status + chevron */}
        <div className="flex items-center gap-3 shrink-0">
          <span className={`hidden sm:inline-flex items-center gap-1 px-2.5 py-1 text-[10px] uppercase tracking-[0.15em] border ${status.cls}`}>
            <StatusIcon size={11} /> {status.label}
          </span>
          <ChevronDown size={16} className={`text-gray-400 transition-transform ${expanded ? "rotate-180" : ""}`} />
        </div>
      </button>

      {/* Mobile-only status */}
      <div className="sm:hidden px-4 pb-3 -mt-1">
        <span className={`inline-flex items-center gap-1 px-2.5 py-1 text-[10px] uppercase tracking-[0.15em] border ${status.cls}`}>
          <StatusIcon size={11} /> {status.label}
        </span>
      </div>

      {/* Expanded body */}
      {expanded && (
        <div className="border-t border-gray-100 px-4 md:px-6 py-5 space-y-5 bg-gray-50/40">
          {order.status === "awaiting_payment" && (
            <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 flex items-center justify-between gap-3 flex-wrap">
              <span className="text-sm text-amber-900">Siparişiniz ödeme bekliyor. Havale/EFT sonrası dekontunuzu iletin.</span>
              <a href={`/odeme-bildirimi/${order.order_number}`} className="inline-block bg-amber-600 text-white px-4 py-2 rounded-md text-xs uppercase tracking-[0.15em] hover:bg-amber-700 shrink-0">Ödeme Bildirimi Yap</a>
            </div>
          )}
          {order.status === "payment_notified" && (
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-sm text-blue-900">Ödeme bildiriminiz alındı, en kısa sürede kontrol edilecek.</div>
          )}
          {order.return_request ? (
            <div className="bg-rose-50 border border-rose-200 rounded-lg p-3 flex items-center justify-between gap-3 flex-wrap">
              <span className="text-sm text-rose-900">İade talebiniz oluşturuldu · Kod: <b className="font-mono">{order.return_request.return_code}</b></span>
              <a href={`/iade/${order.order_number}`} className="inline-block bg-rose-600 text-white px-4 py-2 rounded-md text-xs uppercase tracking-[0.15em] hover:bg-rose-700 shrink-0">Barkodu Gör</a>
            </div>
          ) : canReturn ? (
            <div className="bg-gray-50 border border-gray-200 rounded-lg p-3 flex items-center justify-between gap-3 flex-wrap">
              <span className="text-sm text-gray-700">Üründe sorun mu var? 14 gün içinde iade edebilirsiniz.</span>
              <a href={`/iade/${order.order_number}`} className="inline-block bg-black text-white px-4 py-2 rounded-md text-xs uppercase tracking-[0.15em] hover:bg-gray-800 shrink-0">İade Talebi</a>
            </div>
          ) : null}
          {canCancel && (
            <div className="bg-gray-50 border border-gray-200 rounded-lg p-3 flex items-center justify-between gap-3 flex-wrap">
              <span className="text-sm text-gray-700">Siparişiniz henüz hazırlanmaya başlamadı. Dilerseniz iptal edebilirsiniz.</span>
              <button
                onClick={handleCancel}
                disabled={cancelling}
                data-testid={`cancel-order-${order.id}`}
                className="inline-block bg-red-600 text-white px-4 py-2 rounded-md text-xs uppercase tracking-[0.15em] hover:bg-red-700 disabled:opacity-50 shrink-0"
              >
                {cancelling ? "İptal ediliyor..." : "Siparişi İptal Et"}
              </button>
            </div>
          )}
          {/* Items */}
          <div className="space-y-3">
            {items.length === 0 && (
              <p className="text-xs text-gray-500 italic">Bu sipariş için ürün detayı bulunamadı.</p>
            )}
            {items.map((it, idx) => (
              <div key={idx} className="flex gap-3 items-start">
                <div className="w-14 h-16 bg-white border border-gray-100 overflow-hidden shrink-0">
                  {it.image
                    ? <img src={it.image} alt={it.name || it.product_name || ""} className="w-full h-full object-contain" />
                    : <div className="w-full h-full bg-gradient-to-br from-gray-50 to-gray-200" />}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm leading-snug">{it.name || it.product_name || "Ürün"}</p>
                  <p className="text-xs text-gray-500 mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
                    {it.size && <span>Beden: {it.size}</span>}
                    {it.color && <span>Renk: {it.color}</span>}
                    <span>Adet: {it.quantity || 1}</span>
                  </p>
                </div>
                <p className="text-sm font-medium shrink-0">{((it.price || 0) * (it.quantity || 1)).toFixed(2)} ₺</p>
              </div>
            ))}
          </div>

          {/* Address & cargo */}
          <div className="grid md:grid-cols-2 gap-4 pt-4 border-t border-gray-200">
            {order.shipping_address && (
              <div>
                <h4 className="text-[10px] uppercase tracking-[0.2em] text-gray-500 mb-2">Teslimat Adresi</h4>
                <p className="text-sm text-gray-700 leading-relaxed">
                  {order.shipping_address.first_name} {order.shipping_address.last_name}<br />
                  {order.shipping_address.address}<br />
                  {order.shipping_address.district} / {order.shipping_address.city}
                </p>
              </div>
            )}
            {trackingNumber && (
              <div>
                <h4 className="text-[10px] uppercase tracking-[0.2em] text-gray-500 mb-2">Kargo Bilgisi</h4>
                <p className="text-sm text-gray-700">
                  {order.cargo?.company_name || order.cargo?.company || order.cargo_provider_name || "Kargo"}: <span className="font-medium">{trackingNumber}</span>
                </p>
                {trackingUrl && (
                  <a href={trackingUrl} target="_blank" rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-xs uppercase tracking-[0.15em] mt-2 underline underline-offset-4 hover:no-underline">
                    Kargoyu Takip Et <ChevronRight size={12} />
                  </a>
                )}
              </div>
            )}
          </div>

          {/* Özet — ara toplam, indirimler (ayrı ayrı), kargo, toplam */}
          <div className="pt-4 border-t border-gray-200 space-y-1.5 text-sm">
            {(order.subtotal != null) && (
              <div className="flex justify-between text-gray-600"><span>Ara Toplam</span><span>{Number(order.subtotal || 0).toFixed(2)} ₺</span></div>
            )}
            {Array.isArray(order.discount_breakdown) && order.discount_breakdown.length > 0 ? (
              order.discount_breakdown.map((d, i) => (
                <div key={i} className="flex justify-between text-emerald-700">
                  <span>{d.label}{d.code ? ` · ${d.code}` : ""}</span>
                  <span>-{Number(d.amount || 0).toFixed(2)} ₺</span>
                </div>
              ))
            ) : (
              (order.discount || 0) > 0 && (
                <div className="flex justify-between text-emerald-700"><span>İndirim{order.coupon_code ? ` · ${order.coupon_code}` : ""}</span><span>-{Number(order.discount).toFixed(2)} ₺</span></div>
              )
            )}
            {(order.shipping_cost != null) && (
              <div className="flex justify-between text-gray-600"><span>Kargo</span><span>{Number(order.shipping_cost || 0) === 0 ? "Ücretsiz" : `${Number(order.shipping_cost).toFixed(2)} ₺`}</span></div>
            )}
            <div className="pt-2 mt-1 border-t border-gray-200 flex items-center justify-between">
              <span className="text-xs uppercase tracking-[0.2em] text-gray-500">Toplam</span>
              <span className="text-lg font-medium">{(order.total ?? 0).toFixed(2)} ₺</span>
            </div>
          </div>
        </div>
      )}
    </article>
  );
}

/* ═══════════════════════════════ ADDRESSES ═══════════════════════════════ */

function AddressesPane({ loading, addresses, editing, setEditing, form, setForm, onSubmit, onDelete }) {
  const startNew = () => {
    setForm({ title: "", first_name: "", last_name: "", phone: "",
              address: "", city: "", district: "", postal_code: "", is_default: false,
              is_corporate: false, company_name: "", tax_no: "", tax_office: "" });
    setEditing({});
  };
  const startEdit = (addr) => {
    setForm(addr);
    setEditing(addr);
  };

  return (
    <div className="space-y-5 max-w-4xl">
      <div className="flex items-center justify-between">
        <h2 className="text-sm uppercase tracking-[0.2em] text-gray-700">
          Adreslerim <span className="text-gray-400">({addresses.length})</span>
        </h2>
        {editing === null && (
          <button onClick={startNew} data-testid="add-address-btn"
            className="flex items-center gap-2 bg-black text-white px-5 py-2.5 text-xs uppercase tracking-[0.15em] hover:bg-gray-800 transition-colors">
            <Plus size={14} /> Yeni Adres
          </button>
        )}
      </div>

      {/* Form */}
      {editing !== null && (
        <div className="bg-white border border-gray-100 p-6 md:p-8">
          <h3 className="text-sm uppercase tracking-[0.2em] text-gray-700 mb-5">
            {editing?.id ? "Adres Düzenle" : "Yeni Adres"}
          </h3>
          <form onSubmit={onSubmit} className="space-y-5">
            <Field label="Adres Başlığı (Ev / İş / vb.)" value={form.title} onChange={(v) => setForm({ ...form, title: v })} />
            <div className="grid md:grid-cols-2 gap-5">
              <Field label="Ad" value={form.first_name} onChange={(v) => setForm({ ...form, first_name: v })} />
              <Field label="Soyad" value={form.last_name} onChange={(v) => setForm({ ...form, last_name: v })} />
              <Field label="Telefon" value={form.phone} onChange={(v) => setForm({ ...form, phone: v })} type="tel" />
              <Field label="Posta Kodu" value={form.postal_code} onChange={(v) => setForm({ ...form, postal_code: v })} />
              <div className="md:col-span-2">
                <ProvinceDistrictSelect
                  city={form.city}
                  district={form.district}
                  onChange={({ city, district }) => setForm({ ...form, city, district })}
                  selectClass="w-full border-0 border-b border-gray-300 bg-transparent py-2 text-sm focus:outline-none focus:border-black transition-colors"
                  labelClass="block text-[10px] uppercase tracking-[0.2em] text-gray-500 mb-2"
                  cityLabel="Şehir"
                  testIdPrefix="account-addr"
                />
              </div>
            </div>
            <div>
              <label className="block text-[10px] uppercase tracking-[0.2em] text-gray-500 mb-2">Adres</label>
              <textarea
                value={form.address}
                onChange={(e) => setForm({ ...form, address: e.target.value })}
                rows={3} required
                className="w-full border border-gray-200 bg-white p-3 text-sm focus:outline-none focus:border-black transition-colors"
              />
            </div>
            <label className="flex items-center gap-2 text-xs cursor-pointer">
              <input type="checkbox" checked={form.is_default}
                onChange={(e) => setForm({ ...form, is_default: e.target.checked })}
                className="accent-black"/>
              <span className="uppercase tracking-[0.15em] text-gray-600">Varsayılan adres olarak işaretle</span>
            </label>

            {/* Kurumsal (Şirket) Fatura Bilgileri */}
            <div className="border-t border-gray-100 pt-5">
              <label className="flex items-center gap-2 text-xs cursor-pointer mb-4" data-testid="corporate-toggle">
                <input type="checkbox" checked={form.is_corporate}
                  onChange={(e) => setForm({ ...form, is_corporate: e.target.checked })}
                  className="accent-black"/>
                <span className="uppercase tracking-[0.15em] text-gray-700 font-medium">🏢 Kurumsal Fatura (Şirket Adına)</span>
              </label>
              {form.is_corporate && (
                <div className="grid md:grid-cols-2 gap-5 bg-gray-50 p-4 -mx-1 rounded">
                  <Field label="Şirket Adı / Ünvan" value={form.company_name} onChange={(v) => setForm({ ...form, company_name: v })} />
                  <Field label="Vergi No (10 hane)" value={form.tax_no} onChange={(v) => setForm({ ...form, tax_no: v })} />
                  <div className="md:col-span-2">
                    <Field label="Vergi Dairesi" value={form.tax_office} onChange={(v) => setForm({ ...form, tax_office: v })} />
                  </div>
                </div>
              )}
            </div>

            <div className="flex gap-3 pt-2">
              <button type="submit" data-testid="save-address-btn"
                className="bg-black text-white px-8 py-3 text-xs uppercase tracking-[0.2em] hover:bg-gray-800 transition-colors">
                Kaydet
              </button>
              <button type="button" onClick={() => setEditing(null)}
                className="border border-black text-black px-8 py-3 text-xs uppercase tracking-[0.2em] hover:bg-black hover:text-white transition-colors">
                İptal
              </button>
            </div>
          </form>
        </div>
      )}

      {/* List */}
      {loading ? (
        <div className="grid md:grid-cols-2 gap-4">
          {[1, 2].map((i) => <div key={i} className="bg-white border border-gray-100 h-40 animate-pulse" />)}
        </div>
      ) : addresses.length === 0 && editing === null ? (
        <div className="bg-white border border-gray-100 py-16 px-6 text-center">
          <div className="w-12 h-12 rounded-full bg-gray-100 flex items-center justify-center mx-auto mb-4">
            <MapPin size={20} className="text-gray-400" />
          </div>
          <p className="text-sm text-gray-500 mb-6">Henüz kayıtlı adresiniz yok</p>
          <button onClick={startNew} className="inline-flex items-center gap-2 bg-black text-white px-8 py-3 text-xs uppercase tracking-[0.2em] hover:bg-gray-800 transition-colors">
            <Plus size={14} /> İlk Adresi Ekle
          </button>
        </div>
      ) : (
        <div className="grid md:grid-cols-2 gap-4">
          {addresses.map((addr) => (
            <div key={addr.id} className="bg-white border border-gray-100 p-5 relative group hover:border-gray-300 transition-colors">
              {addr.is_default && (
                <span className="absolute top-3 right-3 inline-flex items-center gap-1 bg-black text-white text-[9px] uppercase tracking-[0.2em] px-2 py-0.5">
                  <Star size={9} /> Varsayılan
                </span>
              )}
              <h4 className="text-sm uppercase tracking-[0.15em] mb-3">{addr.title}</h4>
              <p className="text-sm text-gray-700 leading-relaxed">
                {addr.first_name} {addr.last_name}<br />
                <span className="text-gray-500">{addr.address}</span><br />
                {addr.district} / {addr.city}{addr.postal_code && ` · ${addr.postal_code}`}
              </p>
              <p className="text-xs text-gray-400 mt-2 flex items-center gap-1">
                <Phone size={10} /> {addr.phone}
              </p>
              <div className="flex gap-3 mt-4 pt-4 border-t border-gray-100">
                <button onClick={() => startEdit(addr)} data-testid={`edit-address-${addr.id}`}
                  className="text-xs uppercase tracking-[0.15em] text-black hover:underline underline-offset-4 flex items-center gap-1">
                  <Edit2 size={11} /> Düzenle
                </button>
                <button onClick={() => onDelete(addr.id)} data-testid={`delete-address-${addr.id}`}
                  className="text-xs uppercase tracking-[0.15em] text-gray-500 hover:text-red-600 flex items-center gap-1">
                  <Trash2 size={11} /> Sil
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════ FAVORITES ═══════════════════════════════ */

function FavoritesPane() {
  const { count } = useFavorites();
  const [products, setProducts] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancel = false;
    setLoading(true);
    axios
      .get(`${API}/favorites`, {
        headers: { Authorization: `Bearer ${localStorage.getItem("token")}` },
      })
      .then((r) => { if (!cancel) setProducts(r.data?.favorites || []); })
      .catch(() => { if (!cancel) setProducts([]); })
      .finally(() => { if (!cancel) setLoading(false); });
    return () => { cancel = true; };
  }, [count]);

  if (loading) {
    return (
      <div className="grid grid-cols-2 md:grid-cols-3 gap-x-4 gap-y-8 max-w-4xl">
        {[...Array(3)].map((_, i) => (
          <div key={i} className="animate-pulse">
            <div className="aspect-[2/3] bg-gray-100" />
            <div className="h-3 bg-gray-100 mt-3 w-3/4" />
            <div className="h-3 bg-gray-100 mt-2 w-1/3" />
          </div>
        ))}
      </div>
    );
  }

  if (products.length === 0) {
    return (
      <div className="bg-white border border-gray-100 py-20 px-6 text-center max-w-4xl" data-testid="favorites-empty">
        <div className="w-12 h-12 rounded-full bg-gray-100 flex items-center justify-center mx-auto mb-4">
          <Bookmark size={20} className="text-gray-400" />
        </div>
        <p className="text-sm text-gray-500 mb-1 tracking-wide">Henüz kaydedilen ürününüz yok</p>
        <p className="text-xs text-gray-400 mb-6">Beğendiğiniz ürünleri kaydet ikonu ile listenize ekleyin.</p>
        <a href="/" className="inline-block bg-black text-white px-8 py-3 text-xs uppercase tracking-[0.2em] hover:bg-gray-800 transition-colors">
          Ürünleri Keşfet
        </a>
      </div>
    );
  }

  return (
    <div className="max-w-4xl" data-testid="favorites-grid">
      <p className="text-xs text-gray-500 mb-5 tracking-wide">{products.length} favori ürün</p>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-x-4 gap-y-8">
        {products.map((p) => (
          <ProductCard key={p.id} product={p} />
        ))}
      </div>
    </div>
  );
}

/* ═══════════════════════════════ SECURITY (PASSWORD CHANGE) ═══════════════════════════════ */

function SecurityPane() {
  const [form, setForm] = useState({ current_password: "", new_password: "", confirm: "" });
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    if (form.new_password.length < 6) return toast.error("Yeni şifre en az 6 karakter olmalı");
    if (form.new_password !== form.confirm) return toast.error("Yeni şifreler eşleşmiyor");
    setBusy(true);
    try {
      const token = localStorage.getItem("token");
      const _res = await axios.post(`${API}/auth/change-password`,
        { current_password: form.current_password, new_password: form.new_password },
        { headers: { Authorization: `Bearer ${token}` } }
      );
      // Parola değişince eski token'lar geçersizleşir → sunucudan gelen TAZE token'ı sakla,
      // yoksa bu oturum da düşer.
      if (_res?.data?.token) localStorage.setItem("token", _res.data.token);
      toast.success("Şifreniz güncellendi");
      setForm({ current_password: "", new_password: "", confirm: "" });
    } catch (err) {
      toast.error(err.response?.data?.detail || "Şifre değiştirilemedi");
    } finally { setBusy(false); }
  };

  return (
    <div className="bg-white border border-gray-100 p-6 md:p-8 max-w-xl" data-testid="security-pane">
      <h2 className="text-sm uppercase tracking-[0.2em] text-gray-700 mb-2 flex items-center gap-2">
        <Lock size={14} /> Şifre Değiştir
      </h2>
      <p className="text-xs text-gray-500 mb-6">Mevcut şifrenizi girip yeni bir şifre belirleyin. En az 6 karakter olmalı.</p>
      <form onSubmit={submit} className="space-y-5">
        <Field label="Mevcut Şifre" type="password" value={form.current_password} onChange={(v) => setForm({ ...form, current_password: v })} />
        <Field label="Yeni Şifre" type="password" value={form.new_password} onChange={(v) => setForm({ ...form, new_password: v })} />
        <Field label="Yeni Şifre (Tekrar)" type="password" value={form.confirm} onChange={(v) => setForm({ ...form, confirm: v })} />
        <button type="submit" disabled={busy} data-testid="change-password-btn"
          className="bg-black text-white px-8 py-3 text-xs uppercase tracking-[0.2em] hover:bg-gray-800 disabled:opacity-50 transition-colors">
          {busy ? "Güncelleniyor..." : "Şifreyi Güncelle"}
        </button>
      </form>
    </div>
  );
}
