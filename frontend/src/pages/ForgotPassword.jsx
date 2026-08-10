// =============================================================================
// ForgotPassword.jsx — Şifremi Unuttum (e-posta ile sıfırlama bağlantısı iste)
// =============================================================================
import { useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import axios from "axios";
import Header from "../components/Header";
import Footer from "../components/Footer";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

export default function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);

  const submit = async () => {
    const e = email.trim();
    if (!e || !e.includes("@")) {
      toast.error("Geçerli bir e-posta girin");
      return;
    }
    setLoading(true);
    try {
      await axios.post(`${API}/auth/forgot-password/email`, { email: e });
      setSent(true);
    } catch {
      // Güvenlik gereği backend her durumda 200 döner; yine de hata olursa kullanıcıyı bilgilendir
      setSent(true);
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <Header />
      <div className="min-h-[60vh] flex items-center justify-center px-4 py-12">
        <div className="w-full max-w-md border rounded-2xl p-6 md:p-8 shadow-sm">
          <h1 className="text-xl font-semibold mb-2">Şifremi Unuttum</h1>
          {!sent ? (
            <>
              <p className="text-sm text-gray-500 mb-5">
                Hesabınızın e-posta adresini girin. Şifre sıfırlama bağlantısını e-posta ile göndereceğiz.
              </p>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
                placeholder="E-posta adresiniz"
                className="w-full px-4 py-3 border rounded-lg text-sm mb-4"
                autoFocus
              />
              <button
                onClick={submit}
                disabled={loading}
                className="w-full py-3 bg-black text-white rounded-lg text-sm hover:bg-gray-800 disabled:opacity-50"
              >
                {loading ? "Gönderiliyor…" : "Sıfırlama Bağlantısı Gönder"}
              </button>
            </>
          ) : (
            <div className="text-sm text-gray-700 space-y-3">
              <p>
                Eğer bu e-posta adresi sistemimizde kayıtlıysa, şifre sıfırlama bağlantısını gönderdik.
                Lütfen gelen kutunuzu (ve spam klasörünü) kontrol edin.
              </p>
              <p className="text-gray-500">Bağlantı 30 dakika geçerlidir.</p>
            </div>
          )}
          <div className="mt-5 text-sm text-center">
            <Link to="/giris" className="text-gray-500 hover:text-black underline">
              Girişe dön
            </Link>
          </div>
        </div>
      </div>
      <Footer />
    </>
  );
}
