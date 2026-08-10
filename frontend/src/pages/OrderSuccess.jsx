import { useState, useEffect } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import { Check, Package, Truck, Home, Mail, Phone, MapPin, UserPlus, ChevronLeft, Clock } from "lucide-react";
import axios from "axios";
import { toast } from "sonner";
import Header from "../components/Header";
import Footer from "../components/Footer";
import BankTransferInfo from "../components/BankTransferInfo";
import { useAuth } from "../context/AuthContext";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

// Havale/EFT ödeme yöntemi mi? (sunucu farklı anahtarlar kullanabiliyor)
const _isBankTransfer = (pm) =>
  ["bank_transfer", "havale", "eft", "havale_eft", "banka_havale", "havale/eft"].includes(
    String(pm || "").toLowerCase()
  );

export default function OrderSuccess() {
  const { orderNumber } = useParams();
  const navigate = useNavigate();
  const { user, refreshUser } = useAuth() || {};
  const [order, setOrder] = useState(null);
  const [loading, setLoading] = useState(true);
  const [signupPwd, setSignupPwd] = useState("");
  const [signupBusy, setSignupBusy] = useState(false);
  const [signupDone, setSignupDone] = useState(false);
  const [codeSent, setCodeSent] = useState(false);   // A2.4: e-posta doğrulama kodu gönderildi mi
  const [signupCode, setSignupCode] = useState("");   // A2.4: kullanıcının girdiği 6 haneli kod
  const [codeEmail, setCodeEmail] = useState("");      // maskeli e-posta (kod nereye gitti)

  useEffect(() => {
    if (!orderNumber) {
      navigate("/");
      return;
    }
    let cancel = false;
    (async () => {
      try {
        const token = localStorage.getItem("token");
        const headers = token ? { Authorization: `Bearer ${token}` } : {};
        const r = await axios.get(`${API}/orders/by-number/${orderNumber}`, { headers });
        if (!cancel) setOrder(r.data);
      } catch (_) {
        if (!cancel) setOrder(null);
      } finally {
        if (!cancel) setLoading(false);
      }
    })();
    return () => { cancel = true; };
  }, [orderNumber, navigate]);
  if (loading) {
    return (
      <div className="min-h-screen bg-white">
        <Header />
        <div className="container-main py-32 text-center">
          <div className="animate-spin h-12 w-12 border-2 border-black border-t-transparent rounded-full mx-auto" />
        </div>
      </div>
    );
  }

  const ship = order?.shipping_address || {};
  const items = order?.items || [];
  const isCorporate = order?.billing_info?.is_corporate;

  return (
    <div className="min-h-screen bg-white" data-testid="order-success-page">
      <Header />
      <div className="max-w-3xl mx-auto px-4 sm:px-6 py-10 sm:py-16">
        {/* Back button — masaüstü + mobil */}
        <button onClick={() => navigate(-1)}
          data-testid="ordersuccess-back-btn"
          aria-label="Geri Dön"
          className="mb-6 w-9 h-9 sm:w-10 sm:h-10 border border-black flex items-center justify-center hover:bg-black hover:text-white transition-colors">
          <ChevronLeft size={16} strokeWidth={2} />
        </button>

        {/* Hero */}
        <div className="text-center mb-12 sm:mb-16">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-black mb-6">
            <Check size={32} className="text-white" strokeWidth={2} />
          </div>
          <h1 className="text-3xl sm:text-4xl font-light tracking-[0.3em] text-black mb-4" data-testid="order-success-title">
            TEŞEKKÜR EDERİZ
          </h1>
          <p className="text-gray-700 text-sm tracking-wide max-w-md mx-auto leading-relaxed">
            Siparişiniz başarıyla alındı. Sipariş detayları ve takip bilgileri e-posta adresinize gönderildi.
          </p>
        </div>

        {/* Order Number Card */}
        <div className="border border-gray-200 mb-12">
          <div className="px-6 sm:px-10 py-8 text-center">
            <p className="text-[10px] tracking-[0.2em] text-gray-500 uppercase mb-2">Sipariş Numarası</p>
            <p className="text-2xl font-light text-black tracking-wider" data-testid="order-success-number">
              {orderNumber}
            </p>
            {order?.created_at && (
              <p className="text-xs text-gray-500 mt-3">
                {new Date(order.created_at).toLocaleString("tr-TR", {
                  day: "2-digit", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit",
                })}
              </p>
            )}
          </div>
        </div>

        {/* Havale/EFT: kopyalanabilir banka bilgileri (yalnızca havale siparişlerinde) */}
        {_isBankTransfer(order?.payment_method) && (
          <BankTransferInfo orderNumber={orderNumber} />
        )}

        {/* Step indicator — havale/EFT siparişinde önce "Ödeme Bekliyor" adımı gelir */}
        <div className="mb-16">
          <div className="flex items-center justify-between max-w-2xl mx-auto">
            {(_isBankTransfer(order?.payment_method)
              ? [
                  { icon: Clock, label: "Ödeme Bekliyor", active: true },
                  { icon: Check, label: "Onaylandı", active: false },
                  { icon: Package, label: "Hazırlanıyor", active: false },
                  { icon: Truck, label: "Kargoda", active: false },
                  { icon: Home, label: "Teslim", active: false },
                ]
              : [
                  { icon: Check, label: "Onaylandı", active: true },
                  { icon: Package, label: "Hazırlanıyor", active: false },
                  { icon: Truck, label: "Kargoda", active: false },
                  { icon: Home, label: "Teslim", active: false },
                ]
            ).map((step, i, arr) => (
              <div key={i} className="flex-1 flex items-center">
                <div className="flex flex-col items-center">
                  <div className={`w-10 h-10 rounded-full flex items-center justify-center ${step.active ? "bg-black text-white" : "bg-gray-100 text-gray-500"}`}>
                    <step.icon size={16} strokeWidth={1.5} />
                  </div>
                  <span className={`text-[10px] tracking-wider uppercase mt-2 ${step.active ? "text-black font-medium" : "text-gray-500"}`}>
                    {step.label}
                  </span>
                </div>
                {i < arr.length - 1 && (
                  <div className={`flex-1 h-px mx-2 ${i === 0 ? "bg-gray-300" : "bg-gray-100"}`} />
                )}
              </div>
            ))}
          </div>
        </div>

        {/* Items */}
        {items.length > 0 && (
          <div className="mb-12">
            <p className="text-[10px] tracking-[0.2em] text-gray-500 uppercase mb-6">Ürünler ({items.length})</p>
            <div className="space-y-4">
              {items.map((it, idx) => (
                <div key={idx} className="flex gap-5 pb-4 border-b border-gray-100 last:border-0">
                  <img src={it.image} alt={it.name} className="w-16 h-20 object-contain bg-gray-50" />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-black truncate">{it.name}</p>
                    <p className="text-xs text-gray-500 mt-1">
                      {[it.size && `Beden: ${it.size}`, it.color && `Renk: ${it.color}`, `Adet: ${it.quantity}`].filter(Boolean).join(" · ")}
                    </p>
                  </div>
                  <div className="text-sm text-black whitespace-nowrap">
                    {(it.price * it.quantity).toFixed(2)} TL
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Totals */}
        <div className="mb-12 pb-12 border-b border-gray-100">
          <div className="space-y-2 text-sm">
            <div className="flex justify-between text-gray-700"><span>Ara Toplam</span><span>{(order?.subtotal || 0).toFixed(2)} TL</span></div>
            {/* İndirimler AYRI AYRI — kampanya / kupon / havale her biri kendi satırı */}
            {Array.isArray(order?.discount_breakdown) && order.discount_breakdown.length > 0 ? (
              order.discount_breakdown.map((d, i) => (
                <div key={i} className="flex justify-between text-emerald-700">
                  <span>{d.label}{d.code ? ` · ${d.code}` : ""}</span>
                  <span>-{Number(d.amount || 0).toFixed(2)} TL</span>
                </div>
              ))
            ) : (
              (order?.discount || 0) > 0 && (
                <div className="flex justify-between text-emerald-700"><span>İndirim{order.coupon_code ? ` · ${order.coupon_code}` : ""}</span><span>-{order.discount.toFixed(2)} TL</span></div>
              )
            )}
            <div className="flex justify-between text-gray-700"><span>Kargo</span><span>{(order?.shipping_cost || 0) === 0 ? "Ücretsiz" : `${(order?.shipping_cost || 0).toFixed(2)} TL`}</span></div>
            <div className="flex justify-between pt-3 mt-3 border-t border-gray-200 text-black font-medium">
              <span>Toplam</span><span>{(order?.total || 0).toFixed(2)} TL</span>
            </div>
          </div>
        </div>

        {/* Address & Contact */}
        <div className="grid sm:grid-cols-2 gap-12 mb-16">
          <div>
            <p className="text-[10px] tracking-[0.2em] text-gray-500 uppercase mb-4 flex items-center gap-2">
              <MapPin size={12} /> Teslimat Adresi
            </p>
            <p className="text-sm text-black mb-1">{ship.first_name} {ship.last_name}</p>
            <p className="text-sm text-gray-700 leading-relaxed">{ship.address}</p>
            <p className="text-sm text-gray-700">{ship.district} / {ship.city}</p>
            <p className="text-sm text-gray-700 mt-2 flex items-center gap-2">
              <Phone size={12} /> {ship.phone}
            </p>
            {ship.email && (
              <p className="text-sm text-gray-700 flex items-center gap-2">
                <Mail size={12} /> {ship.email}
              </p>
            )}
          </div>
          {isCorporate && (
            <div>
              <p className="text-[10px] tracking-[0.2em] text-gray-500 uppercase mb-4">Kurumsal Fatura</p>
              <p className="text-sm text-black mb-1">{order.billing_info.company_name}</p>
              <p className="text-sm text-gray-700">VKN: {order.billing_info.tax_number}</p>
              <p className="text-sm text-gray-700">Vergi Dairesi: {order.billing_info.tax_office}</p>
              {order.billing_info.e_invoice_user && (
                <p className="text-xs text-gray-500 mt-2">e-Fatura mükellefi</p>
              )}
            </div>
          )}
        </div>

        {/* CTAs */}
        <div className="flex flex-col sm:flex-row gap-4 justify-center">
          <Link to="/hesabim" data-testid="view-orders-btn"
            className="inline-block text-center bg-stone-900 hover:bg-stone-800 text-white px-10 py-4 text-xs tracking-[0.2em] uppercase transition-colors">
            Siparişlerimi Gör
          </Link>
          <Link to="/" data-testid="continue-shopping-btn"
            className="inline-block text-center border border-stone-900 hover:bg-gray-50 text-black px-10 py-4 text-xs tracking-[0.2em] uppercase transition-colors">
            Alışverişe Devam Et
          </Link>
        </div>

        {/* Guest Signup CTA — Hesap oluştur ve siparişi takip et */}
        {!user && !signupDone && order && (
          <div className="mt-12 border-t border-gray-100 pt-12" data-testid="guest-signup-cta">
            <div className="max-w-lg mx-auto bg-gray-50 px-6 sm:px-10 py-10 text-center">
              <div className="inline-flex items-center justify-center w-12 h-12 rounded-full bg-white mb-4">
                <UserPlus size={20} strokeWidth={1.5} className="text-black" />
              </div>
              <p className="text-[10px] tracking-[0.3em] text-gray-500 uppercase mb-2">SIRA SENDE</p>
              <h3 className="text-lg font-light text-black mb-2">Hesap oluştur, takipte kal</h3>
              <p className="text-xs text-gray-700 leading-relaxed mb-6">
                Bu siparişin <strong>otomatik hesabına bağlanır</strong>; iade & değişim, kargo durumu ve gelecek siparişlerin tek bir yerden yönetilir.
              </p>
              {!codeSent ? (
                <>
                  <div className="flex flex-col sm:flex-row gap-2 max-w-sm mx-auto">
                    <input type="password" value={signupPwd} onChange={(e) => setSignupPwd(e.target.value)}
                      placeholder="En az 6 karakter şifre"
                      data-testid="guest-signup-password"
                      className="flex-1 bg-white border border-gray-200 px-4 py-3 text-sm focus:outline-none focus:border-stone-900" />
                    <button onClick={async () => {
                      if (signupPwd.length < 6) { toast.error("Şifre en az 6 karakter olmalı"); return; }
                      setSignupBusy(true);
                      try {
                        const r = await axios.post(`${API}/auth/guest-convert/send-code`, { order_id: orderNumber });
                        if (r.data.existing_account) {
                          toast(r.data.message || "Bu e-posta ile zaten hesabınız var. Lütfen giriş yapın.", { icon: "ℹ️" });
                          navigate("/giris");
                          return;
                        }
                        setCodeEmail(r.data.email_masked || "");
                        setCodeSent(true);
                        toast.success("Doğrulama kodu e-postanıza gönderildi.");
                      } catch (e) {
                        toast.error(e?.response?.data?.detail || "Kod gönderilemedi");
                      } finally { setSignupBusy(false); }
                    }} disabled={signupBusy}
                      data-testid="guest-signup-create-btn"
                      className="bg-stone-900 hover:bg-stone-800 text-white px-6 py-3 text-xs tracking-[0.2em] uppercase disabled:opacity-60">
                      {signupBusy ? "..." : "Devam"}
                    </button>
                  </div>
                  <p className="text-[10px] text-gray-500 mt-4">
                    E-posta: {order?.shipping_address?.email || "—"}
                  </p>
                </>
              ) : (
                <>
                  <p className="text-xs text-gray-700 mb-4">
                    <strong>{codeEmail}</strong> adresine gönderilen 6 haneli doğrulama kodunu girin.
                  </p>
                  <div className="flex flex-col sm:flex-row gap-2 max-w-sm mx-auto">
                    <input type="text" inputMode="numeric" maxLength={6} value={signupCode}
                      onChange={(e) => setSignupCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                      placeholder="000000"
                      data-testid="guest-signup-code"
                      className="flex-1 bg-white border border-gray-200 px-4 py-3 text-sm tracking-[0.4em] text-center focus:outline-none focus:border-stone-900" />
                    <button onClick={async () => {
                      if (signupCode.length !== 6) { toast.error("6 haneli kodu girin"); return; }
                      setSignupBusy(true);
                      try {
                        const r = await axios.post(`${API}/auth/convert-guest-order`, { order_id: orderNumber, password: signupPwd, code: signupCode });
                        if (r.data.token) localStorage.setItem("token", r.data.token);
                        toast.success(r.data.existing_account ? "Hesabınıza bağlandı" : "Hesap oluşturuldu!");
                        setSignupDone(true);
                        if (refreshUser) await refreshUser();
                      } catch (e) {
                        toast.error(e?.response?.data?.detail || "İşlem başarısız");
                      } finally { setSignupBusy(false); }
                    }} disabled={signupBusy}
                      data-testid="guest-signup-verify-btn"
                      className="bg-stone-900 hover:bg-stone-800 text-white px-6 py-3 text-xs tracking-[0.2em] uppercase disabled:opacity-60">
                      {signupBusy ? "..." : "Onayla"}
                    </button>
                  </div>
                  <button onClick={() => { setCodeSent(false); setSignupCode(""); }}
                    className="text-[10px] text-gray-500 mt-4 underline hover:text-black">
                    Kodu almadınız mı? Tekrar deneyin
                  </button>
                </>
              )}
            </div>
          </div>
        )}

        {signupDone && (
          <div className="mt-12 border-t border-gray-100 pt-12 text-center" data-testid="guest-signup-done">
            <div className="inline-flex items-center justify-center w-12 h-12 rounded-full bg-gray-100 mb-3">
              <Check size={20} className="text-black" />
            </div>
            <p className="text-sm text-black mb-2">Hesabın hazır!</p>
            <Link to="/hesabim" className="text-xs underline tracking-wider text-gray-700 hover:text-black">
              HESABIMA GİT
            </Link>
          </div>
        )}

        {/* Help */}
        <div className="text-center mt-16 pt-12 border-t border-gray-100">
          <p className="text-xs text-gray-500 tracking-wide">
            Sorularınız için: <a href="mailto:destek@facette.com.tr" className="underline hover:text-black">destek@facette.com.tr</a>
          </p>
        </div>
      </div>
      <Footer />
    </div>
  );
}
