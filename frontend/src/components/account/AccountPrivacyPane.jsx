/**
 * AccountPrivacyPane.jsx — Hesabım › Güvenlik sekmesi: oturum + KVKK self-servis.
 *
 *  • Tüm cihazlardan çıkış yap  → AuthContext.logoutAll() / POST /api/auth/logout-all
 *                                 (yanıt {token}: BU cihazın yeni oturumu)
 *  • Verilerimi indir           → GET  /api/kvkk/my-data     (JSON dosyası, 24 saatte en çok 3)
 *  • Hesabımı sil               → POST /api/kvkk/delete-request {password, confirm:true}
 *
 * Tasarım: Account.jsx SecurityPane ile aynı kart dili (beyaz kart, 10px büyük harf başlık,
 * aynı buton ölçüsü px-8 py-3). Üç kart tek sütunda, aynı iç boşluk ve aynı başlık hizasıyla.
 */
import { useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { LogOut, Download, Trash2, AlertTriangle, X } from "lucide-react";
import { useAuth } from "../../context/AuthContext";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const authHeaders = () => {
  let t = null;
  try { t = localStorage.getItem("token"); } catch { t = null; }
  return t ? { Authorization: `Bearer ${t}` } : {};
};

// responseType: "blob" iken hata gövdesi de Blob gelir → detail'i okunur metne çevir.
const blobErrorDetail = async (err, fallback) => {
  try {
    const data = err?.response?.data;
    if (data && typeof data.text === "function") {
      const parsed = JSON.parse(await data.text());
      return parsed?.detail || fallback;
    }
    return data?.detail || fallback;
  } catch {
    return fallback;
  }
};

const BTN_BASE = "inline-flex items-center justify-center gap-2 px-8 py-3 text-xs uppercase tracking-[0.2em] transition-colors disabled:opacity-50";
const BTN_DARK = `${BTN_BASE} bg-black text-white hover:bg-gray-800`;
const BTN_OUTLINE = `${BTN_BASE} border border-black text-black hover:bg-black hover:text-white`;
const BTN_DANGER = `${BTN_BASE} border border-red-600 text-red-600 hover:bg-red-600 hover:text-white`;
const BTN_DANGER_SOLID = `${BTN_BASE} bg-red-600 text-white hover:bg-red-700`;

function Card({ icon: Icon, title, children, testId }) {
  return (
    <section className="bg-white border border-gray-100 p-6 md:p-8" data-testid={testId}>
      <h2 className="text-sm uppercase tracking-[0.2em] text-gray-700 mb-2 inline-flex items-center gap-2">
        <Icon size={14} className="shrink-0" />
        <span>{title}</span>
      </h2>
      {children}
    </section>
  );
}

function DeleteAccountDialog({ open, onClose, onDeleted }) {
  const [password, setPassword] = useState("");
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === "Escape" && !busy) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, busy, onClose]);

  useEffect(() => {
    if (!open) { setPassword(""); setAck(false); setBusy(false); }
  }, [open]);

  if (!open) return null;

  const submit = async (e) => {
    e.preventDefault();
    if (!ack) { toast.error("Lütfen uyarıyı okuduğunuzu onaylayın."); return; }
    setBusy(true);
    try {
      const res = await axios.post(`${API}/kvkk/delete-request`,
        { password, confirm: true }, { headers: authHeaders() });
      onDeleted(res?.data || {});
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Hesap silinemedi. Lütfen tekrar deneyin.");
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
         role="dialog" aria-modal="true" aria-labelledby="delete-account-title"
         onClick={() => { if (!busy) onClose(); }}>
      <form onSubmit={submit} onClick={(e) => e.stopPropagation()}
            className="w-full max-w-md bg-white p-6 md:p-8" data-testid="delete-account-dialog">
        <div className="flex items-center justify-between gap-4 mb-4">
          <h3 id="delete-account-title"
              className="text-sm uppercase tracking-[0.2em] text-red-600 inline-flex items-center gap-2">
            <AlertTriangle size={14} className="shrink-0" />
            <span>Hesabımı Sil</span>
          </h3>
          <button type="button" onClick={onClose} disabled={busy} aria-label="Kapat"
                  className="inline-flex items-center justify-center w-8 h-8 text-gray-400 hover:text-black disabled:opacity-50">
            <X size={16} />
          </button>
        </div>

        <div className="border border-red-200 bg-red-50 p-4 mb-6 text-xs leading-relaxed text-red-800 space-y-2">
          <p className="font-semibold">Bu işlem geri alınamaz.</p>
          <ul className="list-disc pl-4 space-y-1">
            <li>Adınız, e-posta adresiniz, telefonunuz, adresleriniz, favorileriniz ve kayıtlı cihazlarınız kalıcı olarak silinir.</li>
            <li>Kampanya e-posta ve SMS izinleriniz iptal edilir.</li>
            <li>Tüm cihazlardaki oturumlarınız kapanır; bu hesapla bir daha giriş yapılamaz.</li>
            <li>Vergi mevzuatı gereği sipariş ve fatura kayıtları kimliğinizden ayrılarak yalnız yasal süre boyunca saklanır.</li>
            <li>Devam eden bir siparişiniz ya da iadeniz varsa silme, bu işlemler tamamlanınca uygulanır.</li>
          </ul>
        </div>

        <label className="block text-[10px] uppercase tracking-[0.2em] text-gray-500 mb-2" htmlFor="delete-account-password">
          Mevcut Şifre
        </label>
        <input id="delete-account-password" type="password" value={password} autoComplete="current-password"
               onChange={(e) => setPassword(e.target.value)} data-testid="delete-account-password"
               className="w-full border-0 border-b border-gray-300 focus:border-black bg-transparent py-2 text-sm focus:outline-none transition-colors mb-6" />

        <label className="flex items-start gap-3 text-xs text-gray-700 mb-6 cursor-pointer select-none">
          <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)}
                 className="mt-0.5 w-4 h-4 shrink-0 accent-black" data-testid="delete-account-ack" />
          <span>Yukarıdaki uyarıları okudum; hesabımın ve kişisel verilerimin silinmesini istiyorum.</span>
        </label>

        <div className="grid grid-cols-2 gap-3">
          <button type="button" onClick={onClose} disabled={busy} className={BTN_OUTLINE}>Vazgeç</button>
          <button type="submit" disabled={busy || !ack} className={BTN_DANGER_SOLID} data-testid="delete-account-confirm">
            {busy ? "Siliniyor..." : "Kalıcı Olarak Sil"}
          </button>
        </div>
      </form>
    </div>
  );
}

export default function AccountPrivacyPane() {
  const { user, logout, loginWithToken, logoutAll: ctxLogoutAll } = useAuth();
  const [busy, setBusy] = useState("");
  const [deleteOpen, setDeleteOpen] = useState(false);

  const logoutAll = async () => {
    const ok = window.confirm("Bu cihaz dışındaki tüm cihazlarda oturumunuz kapatılacak. Devam edilsin mi?");
    if (!ok) return;
    setBusy("logout-all");
    try {
      // AuthContext.logoutAll varsa onu kullan (taze token'ı kendisi saklar); yoksa aynı uca doğrudan
      // istek atılır ve dönen token loginWithToken ile bu cihaza yerleştirilir.
      let fresh = null;
      if (typeof ctxLogoutAll === "function") {
        const data = await ctxLogoutAll();
        fresh = data?.token || null;
      } else {
        const res = await axios.post(`${API}/auth/logout-all`, {}, { headers: authHeaders() });
        fresh = res?.data?.token || null;
        if (fresh) loginWithToken(fresh, user);
      }
      if (fresh) {
        toast.success("Diğer tüm cihazlarda oturumunuz kapatıldı.");
      } else {
        toast.success("Tüm cihazlarda oturumunuz kapatıldı. Lütfen yeniden giriş yapın.");
        logout();
      }
    } catch (err) {
      toast.error(err?.response?.data?.detail || "İşlem yapılamadı. Lütfen tekrar deneyin.");
    } finally {
      setBusy("");
    }
  };

  const downloadData = async () => {
    setBusy("export");
    try {
      const res = await axios.get(`${API}/kvkk/my-data`, { headers: authHeaders(), responseType: "blob" });
      const cd = String(res.headers?.["content-disposition"] || "");
      const m = cd.match(/filename="?([^";]+)"?/i);
      const name = (m && m[1]) || "meluni-verilerim.json";
      const url = URL.createObjectURL(new Blob([res.data], { type: "application/json" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 30000);
      toast.success("Verileriniz indirildi.");
    } catch (err) {
      toast.error(await blobErrorDetail(err, "Veriler indirilemedi. Lütfen tekrar deneyin."));
    } finally {
      setBusy("");
    }
  };

  const onDeleted = (data) => {
    setDeleteOpen(false);
    if (data.status === "completed") {
      toast.success(data.message || "Hesabınız silindi.");
      logout();
      window.location.assign("/");
      return;
    }
    toast.success(data.message || "Silme talebiniz alındı.");
  };

  return (
    <div className="space-y-6" data-testid="account-privacy-pane">
      <Card icon={LogOut} title="Oturumlar" testId="sessions-card">
        <p className="text-xs text-gray-500 mb-6">
          Telefonunuzu kaybettiyseniz ya da ortak bir bilgisayarda oturum açık kaldıysa, bu cihaz dışındaki
          tüm cihazlarda oturumunuzu kapatabilirsiniz.
        </p>
        <button type="button" onClick={logoutAll} disabled={!!busy} className={BTN_OUTLINE}
                data-testid="logout-all-btn">
          <LogOut size={14} className="shrink-0" />
          <span>{busy === "logout-all" ? "Kapatılıyor..." : "Tüm Cihazlardan Çıkış Yap"}</span>
        </button>
      </Card>

      <Card icon={Download} title="Kişisel Verilerim" testId="my-data-card">
        <p className="text-xs text-gray-500 mb-6">
          KVKK kapsamında hesabınıza ait profil, adres, sipariş, izin, favori, yorum ve iade bilgilerinizi
          tek bir dosya (JSON) olarak indirebilirsiniz. Günde en fazla 3 kez indirilebilir.
        </p>
        <button type="button" onClick={downloadData} disabled={!!busy} className={BTN_DARK}
                data-testid="download-my-data-btn">
          <Download size={14} className="shrink-0" />
          <span>{busy === "export" ? "Hazırlanıyor..." : "Verilerimi İndir"}</span>
        </button>
      </Card>

      <Card icon={Trash2} title="Hesabı Sil" testId="delete-account-card">
        <p className="text-xs text-gray-500 mb-6">
          Hesabınızı ve kişisel verilerinizi kalıcı olarak silebilirsiniz. Yasal saklama zorunluluğu olan
          sipariş ve fatura kayıtları kimliğinizden ayrılarak yalnız yasal süre boyunca saklanır.
        </p>
        <button type="button" onClick={() => setDeleteOpen(true)} disabled={!!busy} className={BTN_DANGER}
                data-testid="delete-account-btn">
          <Trash2 size={14} className="shrink-0" />
          <span>Hesabımı Sil</span>
        </button>
      </Card>

      <DeleteAccountDialog open={deleteOpen} onClose={() => setDeleteOpen(false)} onDeleted={onDeleted} />
    </div>
  );
}
