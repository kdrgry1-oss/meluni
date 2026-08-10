// =============================================================================
// ResetPassword.jsx — Şifre Sıfırla (e-postadaki ?token= ile yeni şifre belirle)
// Backend: POST /auth/forgot-password/reset { reset_token, new_password }
// =============================================================================
import { useState } from "react";
import { useNavigate, useSearchParams, Link } from "react-router-dom";
import { toast } from "sonner";
import axios from "axios";
import Header from "../components/Header";
import Footer from "../components/Footer";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

export default function ResetPassword() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const token = params.get("token") || "";

  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async () => {
    if (!token) {
      toast.error("Bağlantı geçersiz. Lütfen yeni bir sıfırlama bağlantısı isteyin.");
      return;
    }
    if (pw.length < 6) {
      toast.error("Şifre en az 6 karakter olmalı");
      return;
    }
    if (pw !== pw2) {
      toast.error("Şifreler eşleşmiyor");
      return;
    }
    setLoading(true);
    try {
      await axios.post(`${API}/auth/forgot-password/reset`, {
        reset_token: token,
        new_password: pw,
      });
      toast.success("Şifreniz güncellendi. Şimdi giriş yapabilirsiniz.");
      navigate("/giris");
    } catch (err) {
      toast.error(err.response?.data?.detail || "Bağlantı geçersiz veya süresi dolmuş");
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <Header />
      <div className="min-h-[60vh] flex items-center justify-center px-4 py-12">
        <div className="w-full max-w-md border rounded-2xl p-6 md:p-8 shadow-sm">
          <h1 className="text-xl font-semibold mb-2">Yeni Şifre Belirle</h1>
          {!token ? (
            <div className="text-sm text-gray-700 space-y-3">
              <p>Bağlantı geçersiz veya eksik. Lütfen yeniden şifre sıfırlama bağlantısı isteyin.</p>
              <Link to="/sifremi-unuttum" className="inline-block text-black underline">
                Sıfırlama bağlantısı iste
              </Link>
            </div>
          ) : (
            <>
              <p className="text-sm text-gray-500 mb-5">Yeni şifrenizi belirleyin (en az 6 karakter).</p>
              <input
                type="password"
                value={pw}
                onChange={(e) => setPw(e.target.value)}
                placeholder="Yeni şifre"
                className="w-full px-4 py-3 border rounded-lg text-sm mb-3"
                autoFocus
              />
              <input
                type="password"
                value={pw2}
                onChange={(e) => setPw2(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
                placeholder="Yeni şifre (tekrar)"
                className="w-full px-4 py-3 border rounded-lg text-sm mb-4"
              />
              <button
                onClick={submit}
                disabled={loading}
                className="w-full py-3 bg-black text-white rounded-lg text-sm hover:bg-gray-800 disabled:opacity-50"
              >
                {loading ? "Güncelleniyor…" : "Şifreyi Güncelle"}
              </button>
            </>
          )}
          <div className="mt-5 text-sm text-center">
            <Link to="/giris" className="text-gray-500 hover:text-black underline">Girişe dön</Link>
          </div>
        </div>
      </div>
      <Footer />
    </>
  );
}
