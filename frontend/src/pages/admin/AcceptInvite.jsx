/**
 * AcceptInvite.jsx — Personel daveti kabulü (ADM-01).
 * Bağlantı: /admin/davet#<token>  (token URL parçasında → sunucu loglarına / Referer'a gitmez)
 * Adımlar: 1) şifre belirle  2) MFA kur (Authenticator QR ya da yöneticinin verdiği telefona SMS)
 *          3) kurtarma kodlarını kaydet → panele geç. Oturum ancak 2. adım doğrulanınca verilir.
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import axios from "axios";
import { toast } from "sonner";
import { Copy } from "lucide-react";
import { useAuth } from "../../context/AuthContext";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const readToken = () => {
  try {
    return decodeURIComponent((window.location.hash || "").replace(/^#/, "").trim());
  } catch {
    return "";
  }
};

const STEPS = ["Şifre", "Doğrulama", "Kurtarma kodları"];

export default function AcceptInvite() {
  const navigate = useNavigate();
  const { loginWithToken } = useAuth();
  const [token] = useState(readToken);
  const [info, setInfo] = useState(null);
  const [error, setError] = useState("");
  const [step, setStep] = useState(0);
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [mfa, setMfa] = useState(null);      // {method, qr_code?, secret?, phone_masked?}
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [recovery, setRecovery] = useState([]);
  const [session, setSession] = useState(null);

  useEffect(() => {
    // Token adres çubuğunda/geçmişte kalmasın.
    try { window.history.replaceState(null, "", window.location.pathname); } catch { /* yoksay */ }
    if (!token) {
      setError("Davet bağlantısı eksik ya da hatalı. E-postadaki bağlantıyı tam olarak açın.");
      return;
    }
    axios.post(`${API}/auth/invite/inspect`, { token })
      .then((r) => setInfo(r.data))
      .catch((e) => setError(e.response?.data?.detail || "Davet doğrulanamadı"));
  }, [token]);

  const startMfa = async () => {
    setBusy(true);
    try {
      const r = await axios.post(`${API}/auth/invite/mfa-start`, { token });
      setMfa(r.data);
      if (r.data?.method === "sms") {
        toast.success(r.data?.sent ? `Kod gönderildi (${r.data?.phone_masked || "***"})` : "Kod gönderilemedi, tekrar deneyin");
      }
    } catch (e) {
      toast.error(e.response?.data?.detail || "Doğrulama başlatılamadı");
    } finally {
      setBusy(false);
    }
  };

  const goMfa = async (e) => {
    e.preventDefault();
    if (password.length < 12) { toast.error("Şifre en az 12 karakter olmalı"); return; }
    if (password !== password2) { toast.error("Şifreler eşleşmiyor"); return; }
    setStep(1);
    if (!mfa) await startMfa();
  };

  const accept = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const r = await axios.post(`${API}/auth/invite/accept`, { token, password, code });
      setSession({ token: r.data?.token, user: r.data?.user });
      setRecovery(r.data?.recovery_codes || []);
      setStep(2);
    } catch (err) {
      const d = err.response?.data?.detail;
      toast.error(typeof d === "string" ? d : "Davet kabul edilemedi");
      if (err.response?.status === 400 && typeof d === "string" && d.includes("Personel şifresi")) setStep(0);
    } finally {
      setBusy(false);
    }
  };

  const copyCodes = async () => {
    try {
      await navigator.clipboard.writeText(recovery.join("\n"));
      toast.success("Kodlar panoya kopyalandı");
    } catch {
      toast.error("Kopyalanamadı — kodları elle not edin");
    }
  };

  const finish = () => {
    if (session?.token) loginWithToken(session.token, session.user);
    toast.success("Hoş geldiniz!");
    navigate("/admin", { replace: true });
  };

  const input = "w-full border border-gray-300 px-3 py-2 rounded focus:outline-none focus:ring-1 focus:ring-black";
  const primary = "w-full bg-black text-white py-3 rounded hover:bg-gray-900 transition-colors disabled:opacity-50";

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-100 px-4">
      <div className="bg-white p-8 rounded-lg shadow-md w-full max-w-md" data-testid="accept-invite-page">
        <div className="flex flex-col items-center mb-6">
          <img src={`${process.env.PUBLIC_URL}/logo.png`} alt="Logo" className="h-9 w-auto" />
          <span className="mt-2 text-xs font-medium tracking-[0.3em] text-gray-500">PANEL DAVETİ</span>
        </div>

        {error ? (
          <div className="space-y-4 text-center">
            <p className="text-sm text-red-600">{error}</p>
            <p className="text-xs text-gray-500">Yeni bir davet için yöneticinize başvurun.</p>
          </div>
        ) : !info ? (
          <p className="text-sm text-gray-500 text-center">Davet doğrulanıyor…</p>
        ) : (
          <>
            {/* Adım göstergesi — eşit genişlikte, tek eksende */}
            <div className="grid grid-cols-3 gap-2 mb-6">
              {STEPS.map((label, i) => (
                <div key={label} className="flex flex-col items-center gap-1.5">
                  <span className={`h-1.5 w-full rounded-full ${i <= step ? "bg-black" : "bg-gray-200"}`} />
                  <span className={`text-[11px] ${i === step ? "text-black font-medium" : "text-gray-400"}`}>{label}</span>
                </div>
              ))}
            </div>

            {step === 0 && (
              <form onSubmit={goMfa} className="space-y-4">
                <p className="text-sm text-gray-600 text-center">
                  <b>{info.email_masked}</b> için <b>{info.role_name}</b> rolüyle davet edildiniz.
                  Önce şifrenizi belirleyin.
                </p>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Yeni şifre</label>
                  <input type="password" autoComplete="new-password" value={password} required
                    onChange={(e) => setPassword(e.target.value)} className={input} data-testid="invite-password" />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Yeni şifre (tekrar)</label>
                  <input type="password" autoComplete="new-password" value={password2} required
                    onChange={(e) => setPassword2(e.target.value)} className={input} data-testid="invite-password2" />
                </div>
                <p className="text-[11px] text-gray-400">
                  En az 12 karakter; büyük harf, küçük harf, rakam ve özel karakter içermeli; ad/e-posta parçası içermemeli.
                </p>
                <button type="submit" disabled={busy} className={primary} data-testid="invite-next">Devam</button>
              </form>
            )}

            {step === 1 && (
              <form onSubmit={accept} className="space-y-4">
                {mfa?.method === "totp" && (
                  <>
                    <p className="text-sm text-gray-600 text-center">
                      Google Authenticator / Authy ile QR kodu tarayın ve uygulamadaki 6 haneli kodu girin.
                    </p>
                    <img src={mfa.qr_code} alt="MFA QR" className="w-40 h-40 border rounded mx-auto" />
                    <p className="text-xs text-gray-400 break-all text-center">
                      Manuel anahtar: <span className="font-mono">{mfa.secret}</span>
                    </p>
                  </>
                )}
                {mfa?.method === "sms" && (
                  <p className="text-sm text-gray-600 text-center">
                    Telefonunuza ({mfa.phone_masked || info.phone_masked || "***"}) gönderilen 6 haneli kodu girin.
                  </p>
                )}
                {!mfa && <p className="text-sm text-gray-500 text-center">Doğrulama hazırlanıyor…</p>}
                <input type="text" inputMode="numeric" autoFocus maxLength={6} value={code}
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} placeholder="000000"
                  className="w-full border border-gray-300 px-3 py-3 rounded text-center text-2xl tracking-[0.5em] focus:outline-none focus:ring-1 focus:ring-black"
                  data-testid="invite-code" />
                <button type="submit" disabled={busy || code.length !== 6 || !mfa} className={primary} data-testid="invite-accept">
                  {busy ? "Doğrulanıyor..." : "Doğrula ve Hesabı Oluştur"}
                </button>
                {mfa?.method === "sms" && (
                  <button type="button" onClick={startMfa} disabled={busy} className="w-full text-sm text-gray-500 hover:text-black">
                    Kodu yeniden gönder
                  </button>
                )}
                <button type="button" onClick={() => setStep(0)} className="w-full text-sm text-gray-500 hover:text-black">← Şifreye dön</button>
              </form>
            )}

            {step === 2 && (
              <div className="space-y-4" data-testid="invite-recovery">
                <p className="text-sm text-gray-600 text-center">
                  Hesabınız oluşturuldu. Telefonunuza/uygulamanıza erişemezseniz girişte kullanabileceğiniz
                  <b> tek kullanımlık kurtarma kodlarınız</b> aşağıda. Yalnız bir kez gösterilir — güvenli bir yere kaydedin.
                </p>
                <div className="grid grid-cols-2 gap-2">
                  {recovery.map((c) => (
                    <span key={c} className="font-mono text-sm text-center bg-gray-50 border rounded px-2 py-1.5">{c}</span>
                  ))}
                </div>
                <button type="button" onClick={copyCodes}
                  className="w-full inline-flex items-center justify-center gap-1.5 border border-gray-300 py-2.5 rounded text-sm hover:border-black">
                  <Copy size={14} /> Kodları Kopyala
                </button>
                <button type="button" onClick={finish} className={primary} data-testid="invite-finish">Kaydettim, panele geç</button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
