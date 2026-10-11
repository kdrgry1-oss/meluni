import { useState } from "react";
import { useNavigate, Navigate, useLocation } from "react-router-dom";
import { toast } from "sonner";
import axios from "axios";
import { useAuth } from "../../context/AuthContext";
import { safeInternalPath } from "../../lib/googleAuthFlow";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

// Oturum düşünce gelinen sayfaya geri dönüş (?next=/admin/...). Yalnız panel içi yollar kabul edilir.
// GÜVENLİK: düz "/admin" ön ek kontrolü "/admin/../\evil.com" ile aşılıyordu (React Router ".."
// çözer, tarayıcı "\"yi "/" sayar → DIŞ siteye yönlendirme). safeInternalPath origin + kanonik
// yol ön ekini doğrular.
const safeAdminNext = (search) => {
  let n = "";
  try { n = new URLSearchParams(search || "").get("next") || ""; } catch { n = ""; }
  if (safeInternalPath(n, "", "/admin") === "" || n.startsWith("/admin/login") || n.startsWith("/admin/davet")) {
    return "/admin";
  }
  return n;
};

export default function AdminLogin() {
  const navigate = useNavigate();
  const location = useLocation();
  const nextPath = safeAdminNext(location.search);
  const { login, verifyMfa, loginWithToken, user, isAdmin, logout, loading: authLoading } = useAuth();
  const [loading, setLoading] = useState(false);
  const [formData, setFormData] = useState({ email: "", password: "" });
  const [mfaToken, setMfaToken] = useState(null);
  const [mfaCode, setMfaCode] = useState("");
  const [mfaMethod, setMfaMethod] = useState("totp");
  const [phoneMasked, setPhoneMasked] = useState("");
  const [emailMasked, setEmailMasked] = useState("");
  const [mfaChannel, setMfaChannel] = useState("sms");   // kodun gönderildiği kanal (sms|email)
  const [mfaNotice, setMfaNotice] = useState("");       // admin kanal politikası açıklaması
  const [sending, setSending] = useState(false);
  // Zorunlu MFA kurulum modu (login sonrası mfa_setup_required)
  const [setupMode, setSetupMode] = useState(false);   // false | 'choose' | 'sms' | 'totp'
  const [setupPhone, setSetupPhone] = useState("");
  const [setupCode, setSetupCode] = useState("");
  const [totp, setTotp] = useState(null); // {qr_code, secret}
  const [smsSent, setSmsSent] = useState(false);
  // MFA adımında kod yerine tek kullanımlık KURTARMA KODU kullanımı
  const [useRecovery, setUseRecovery] = useState(false);
  const [recoveryCode, setRecoveryCode] = useState("");

  if (authLoading) {
    return <div className="min-h-screen flex items-center justify-center bg-gray-100"><p>Yükleniyor...</p></div>;
  }
  if (user && isAdmin && !setupMode) {
    return <Navigate to={nextPath} />;
  }

  const finishLogin = (userData) => {
    if (userData?.is_admin) {
      toast.success("Admin girişi başarılı!");
      navigate(nextPath);
    } else {
      toast.error("Bu alana giriş yetkiniz bulunmamaktadır.");
      logout();
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const result = await login(formData.email, formData.password);
      if (result?.mfaRequired) {
        setMfaToken(result.mfaToken);
        setMfaMethod(result.mfaMethod || "totp");
        setPhoneMasked(result.phoneMasked || "");
        setEmailMasked(result.emailMasked || "");
        setMfaChannel(result.mfaChannel || result.mfaMethod || "sms");
        setMfaNotice(result.mfaNotice || "");
        toast.info(result.mfaMethod !== "sms"
          ? "Authenticator kodunuzu girin"
          : (result.mfaChannel === "email"
              ? `Doğrulama kodu e-postanıza gönderildi (${result.emailMasked || "***"})`
              : `Doğrulama kodu telefonunuza gönderildi (${result.phoneMasked || "***"})`));
      } else if (result?.mfaSetupRequired) {
        // Zorunlu MFA: kurulmamış → kurulum ekranını aç (oturum verildi ama panele geçmeden kur).
        setSetupMode("choose");
        toast.warning("Güvenlik için MFA (2FA) kurulumu zorunludur.");
      } else {
        finishLogin(result);
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || "Giriş başarısız");
    } finally {
      setLoading(false);
    }
  };

  const handleMfaVerify = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const userData = useRecovery
        ? await verifyMfa(mfaToken, recoveryCode, { recovery: true })
        : await verifyMfa(mfaToken, mfaCode);
      if (useRecovery) {
        const left = userData?.recoveryCodesRemaining;
        toast.warning(`Kurtarma kodu kullanıldı${left !== null && left !== undefined ? ` — kalan: ${left}` : ""}. `
          + "Yöneticilere bildirim gönderildi; MFA ayarlarınızı kontrol edin.");
      }
      finishLogin(userData);
    } catch (err) {
      toast.error(err.response?.data?.detail || "Kod doğrulanamadı");
    } finally {
      setLoading(false);
    }
  };

  // Kodu istenen kanaldan (SMS / e-posta) gönder. Kod kanaldan bağımsızdır: hangi kanaldan
  // gelirse gelsin aynı 6 hane doğrulanır; son gönderilen kod geçerlidir.
  const sendCode = async (channel) => {
    setSending(true);
    try {
      const r = await axios.post(`${API}/auth/mfa/send`, { mfa_token: mfaToken, channel });
      const ch = r.data?.channel || channel;
      setMfaChannel(ch);
      if (r.data?.phone_masked) setPhoneMasked(r.data.phone_masked);
      if (r.data?.email_masked) setEmailMasked(r.data.email_masked);
      toast.success(ch === "email"
        ? `Kod e-postanıza gönderildi (${r.data?.email_masked || emailMasked || "***"})`
        : `Kod telefonunuza gönderildi (${r.data?.phone_masked || phoneMasked || "***"})`);
    } catch (err) {
      toast.error(err.response?.data?.detail || "Kod gönderilemedi");
    } finally { setSending(false); }
  };

  // ── Zorunlu kurulum akışı (token zaten set; axios Authorization header hazır) ──
  // Güvenlik: faktör bağlama yeniden kimlik doğrulama ister → girişte yazılan şifre
  // (current_password) gönderilir. Kurulum tamamlanınca sunucu diğer oturumları kapatır ve
  // bu oturum için TAZE token döner; o token yerleştirilmezse panel oturumu düşer.
  const reauth = { current_password: formData.password };
  const adoptFreshToken = (r) => {
    if (r?.data?.token) loginWithToken(r.data.token, user);
  };
  const startSmsSetup = async () => {
    if (!setupPhone || setupPhone.replace(/\D/g, "").length < 10) { toast.error("Geçerli telefon girin"); return; }
    setLoading(true);
    try {
      const r = await axios.post(`${API}/auth/mfa/setup-sms`, { phone: setupPhone, ...reauth });
      setSmsSent(true);
      toast.success(`Kod gönderildi (${r.data?.phone_masked || "***"})`);
    } catch (err) {
      toast.error(err.response?.data?.detail || "SMS gönderilemedi");
    } finally { setLoading(false); }
  };
  const enableSms = async () => {
    setLoading(true);
    try {
      const r = await axios.post(`${API}/auth/mfa/enable-sms`, { code: setupCode, ...reauth });
      adoptFreshToken(r);
      toast.success("MFA (SMS) kuruldu ✓");
      navigate(nextPath);
    } catch (err) {
      toast.error(err.response?.data?.detail || "Kod doğrulanamadı");
    } finally { setLoading(false); }
  };
  const startTotpSetup = async () => {
    setLoading(true);
    try {
      const r = await axios.post(`${API}/auth/mfa/setup`, { ...reauth });
      setTotp(r.data);
      setSetupMode("totp");
    } catch (err) {
      toast.error(err.response?.data?.detail || "Kurulum başlatılamadı");
    } finally { setLoading(false); }
  };
  const enableTotp = async () => {
    setLoading(true);
    try {
      const r = await axios.post(`${API}/auth/mfa/enable`, { code: setupCode, ...reauth });
      adoptFreshToken(r);
      toast.success("MFA (Authenticator) kuruldu ✓");
      navigate(nextPath);
    } catch (err) {
      toast.error(err.response?.data?.detail || "Kod doğrulanamadı");
    } finally { setLoading(false); }
  };

  const codeInput = (val, set, testid) => (
    <input type="text" inputMode="numeric" autoFocus maxLength={6} value={val}
      onChange={(e) => set(e.target.value.replace(/\D/g, ""))} placeholder="000000"
      className="w-full border border-gray-300 px-3 py-3 rounded text-center text-2xl tracking-[0.5em] focus:outline-none focus:ring-1 focus:ring-black"
      data-testid={testid} />
  );

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-100">
      <div className="bg-white p-8 rounded-lg shadow-md w-full max-w-md">
        <div className="flex flex-col items-center mb-8">
          <img src={`${process.env.PUBLIC_URL}/logo.png`} alt="Meluni" className="h-9 w-auto" />
          <span className="mt-2 text-xs font-medium tracking-[0.3em] text-gray-500">ADMIN PANEL</span>
        </div>

        {/* 1) E-posta/şifre */}
        {!mfaToken && !setupMode && (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">E-posta</label>
              <input type="email" value={formData.email} onChange={(e) => setFormData({ ...formData, email: e.target.value })}
                required className="w-full border border-gray-300 px-3 py-2 rounded focus:outline-none focus:ring-1 focus:ring-black" data-testid="admin-email-input" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Şifre</label>
              <input type="password" value={formData.password} onChange={(e) => setFormData({ ...formData, password: e.target.value })}
                required className="w-full border border-gray-300 px-3 py-2 rounded focus:outline-none focus:ring-1 focus:ring-black" data-testid="admin-password-input" />
            </div>
            <button type="submit" disabled={loading} className="w-full bg-black text-white py-3 rounded hover:bg-gray-900 transition-colors disabled:opacity-50 mt-4" data-testid="admin-submit-btn">
              {loading ? "Giriş Yapılıyor..." : "Giriş Yap"}
            </button>
          </form>
        )}

        {/* 2) MFA doğrulama (SMS veya TOTP) */}
        {mfaToken && !setupMode && (
          <form onSubmit={handleMfaVerify} className="space-y-4" data-testid="admin-mfa-form">
            {useRecovery ? (
              <>
                <p className="text-sm text-gray-600 text-center">
                  Size verilen tek kullanımlık <b>kurtarma kodlarından</b> birini girin (ör. ABCDE-12345).
                </p>
                <input type="text" autoFocus autoComplete="one-time-code" maxLength={11} value={recoveryCode}
                  onChange={(e) => setRecoveryCode(e.target.value.toUpperCase().replace(/[^A-Z0-9-]/g, ""))}
                  placeholder="XXXXX-XXXXX"
                  className="w-full border border-gray-300 px-3 py-3 rounded text-center text-xl font-mono tracking-[0.3em] focus:outline-none focus:ring-1 focus:ring-black"
                  data-testid="admin-mfa-recovery-input" />
                <button type="submit" disabled={loading || recoveryCode.replace(/-/g, "").length !== 10}
                  className="w-full bg-black text-white py-3 rounded hover:bg-gray-900 transition-colors disabled:opacity-50"
                  data-testid="admin-mfa-recovery-verify-btn">
                  {loading ? "Doğrulanıyor..." : "Kurtarma Koduyla Giriş Yap"}
                </button>
                <p className="text-[11px] text-gray-400 text-center">
                  Her kod yalnız bir kez kullanılabilir. Kullanımda yöneticilere bildirim gider.
                </p>
              </>
            ) : (
              <>
                <p className="text-sm text-gray-600 text-center">
                  {mfaMethod !== "sms"
                    ? <>Authenticator uygulamanızdaki 6 haneli kodu girin.</>
                    : mfaChannel === "email"
                      ? <>E-postanıza ({emailMasked || "***"}) gönderilen 6 haneli kodu girin.</>
                      : <>Telefonunuza ({phoneMasked || "***"}) gönderilen 6 haneli kodu girin.</>}
                </p>
                {codeInput(mfaCode, setMfaCode, "admin-mfa-code-input")}
                <button type="submit" disabled={loading || mfaCode.length !== 6} className="w-full bg-black text-white py-3 rounded hover:bg-gray-900 transition-colors disabled:opacity-50" data-testid="admin-mfa-verify-btn">
                  {loading ? "Doğrulanıyor..." : "Doğrula ve Giriş Yap"}
                </button>
                {mfaMethod === "sms" && (
                  <div className="space-y-2">
                    {/* İki kanal da alternatif: isteyen SMS, isteyen e-posta ile kod alır. */}
                    <div className="flex gap-2">
                      <button type="button" disabled={sending || !phoneMasked} onClick={() => sendCode("sms")}
                        data-testid="admin-mfa-send-sms"
                        className={`flex-1 text-sm py-2 rounded border transition-colors disabled:opacity-40
                          ${mfaChannel === "sms" ? "border-black bg-black text-white" : "border-gray-300 text-gray-700 hover:border-black"}`}>
                        SMS ile gönder
                      </button>
                      <button type="button" disabled={sending || !emailMasked} onClick={() => sendCode("email")}
                        data-testid="admin-mfa-send-email"
                        className={`flex-1 text-sm py-2 rounded border transition-colors disabled:opacity-40
                          ${mfaChannel === "email" ? "border-black bg-black text-white" : "border-gray-300 text-gray-700 hover:border-black"}`}>
                        E-posta ile gönder
                      </button>
                    </div>
                    <p className="text-[11px] text-gray-400 text-center">
                      {mfaNotice
                        ? <>{mfaNotice} Son gönderilen kod geçerlidir.</>
                        : <>SMS gelmiyorsa e-posta ile kod isteyebilirsiniz. Son gönderilen kod geçerlidir.</>}
                    </p>
                  </div>
                )}
                {mfaMethod !== "sms" && mfaNotice && (
                  <p className="text-[11px] text-gray-400 text-center">{mfaNotice}</p>
                )}
              </>
            )}
            <button type="button" data-testid="admin-mfa-recovery-toggle"
              onClick={() => { setUseRecovery(!useRecovery); setRecoveryCode(""); setMfaCode(""); }}
              className="w-full text-sm text-gray-500 hover:text-black">
              {useRecovery ? "Doğrulama koduna dön" : "Kurtarma kodu kullan"}
            </button>
            <button type="button" onClick={() => { setMfaToken(null); setMfaCode(""); setUseRecovery(false); setRecoveryCode(""); }} className="w-full text-sm text-gray-500 hover:text-black">← Geri</button>
          </form>
        )}

        {/* 3) ZORUNLU MFA kurulumu */}
        {setupMode === "choose" && (
          <div className="space-y-4">
            <p className="text-sm text-gray-700 text-center">Güvenlik için <b>MFA (2FA)</b> kurulumu zorunludur. Bir yöntem seçin:</p>
            <button onClick={() => setSetupMode("sms")} className="w-full border border-gray-300 py-3 rounded hover:border-black text-sm font-medium">📱 SMS ile (telefonuma kod)</button>
            <button onClick={startTotpSetup} disabled={loading} className="w-full border border-gray-300 py-3 rounded hover:border-black text-sm font-medium">🔐 Authenticator uygulaması (TOTP)</button>
          </div>
        )}
        {setupMode === "sms" && (
          <div className="space-y-3">
            <p className="text-sm text-gray-700">Telefon numaranıza doğrulama kodu göndereceğiz.</p>
            <input type="tel" value={setupPhone} onChange={(e) => setSetupPhone(e.target.value)} placeholder="+90 5xx xxx xx xx"
              className="w-full border border-gray-300 px-3 py-2 rounded focus:outline-none focus:ring-1 focus:ring-black" />
            {!smsSent ? (
              <button onClick={startSmsSetup} disabled={loading} className="w-full bg-black text-white py-2.5 rounded disabled:opacity-50">Kod Gönder</button>
            ) : (
              <>
                {codeInput(setupCode, setSetupCode, "mfa-setup-sms-code")}
                <button onClick={enableSms} disabled={loading || setupCode.length !== 6} className="w-full bg-black text-white py-2.5 rounded disabled:opacity-50">Doğrula ve Kur</button>
                <button onClick={startSmsSetup} className="w-full text-sm text-indigo-600">Kodu yeniden gönder</button>
              </>
            )}
            <button onClick={() => { setSetupMode("choose"); setSmsSent(false); setSetupCode(""); }} className="w-full text-sm text-gray-500 hover:text-black">← Yöntem değiştir</button>
          </div>
        )}
        {setupMode === "totp" && totp && (
          <div className="space-y-3">
            <p className="text-sm text-gray-700">Google Authenticator / Authy ile QR'ı tarayın, kodu girin.</p>
            <img src={totp.qr_code} alt="MFA QR" className="w-40 h-40 border rounded mx-auto" />
            <p className="text-xs text-gray-400 break-all text-center">Manuel anahtar: <span className="font-mono">{totp.secret}</span></p>
            {codeInput(setupCode, setSetupCode, "mfa-setup-totp-code")}
            <button onClick={enableTotp} disabled={loading || setupCode.length !== 6} className="w-full bg-black text-white py-2.5 rounded disabled:opacity-50">Doğrula ve Kur</button>
            <button onClick={() => { setSetupMode("choose"); setSetupCode(""); }} className="w-full text-sm text-gray-500 hover:text-black">← Yöntem değiştir</button>
          </div>
        )}
      </div>
    </div>
  );
}
