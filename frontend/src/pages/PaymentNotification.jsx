import { useState, useEffect } from "react";
import { useParams } from "react-router-dom";
import axios from "axios";
import { toast } from "sonner";
import { UploadCloud, CheckCircle2, Copy, FileText } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { orderAccessHeaders } from "../lib/orderAccess";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

// 2,5MB üstü görseli uzun kenarı 2000px JPEG'e küçültür (dekont okunur kalır). Tarayıcı
// görseli açamazsa (HEIC vb.) veya PDF ise dosyayı OLDUĞU GİBİ döndürür.
function shrinkImage(file) {
  return new Promise((resolve) => {
    if (!file.type.startsWith("image/") || file.size <= 2.5 * 1024 * 1024) { resolve(file); return; }
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      try {
        const scale = Math.min(1, 2000 / Math.max(img.width, img.height));
        const c = document.createElement("canvas");
        c.width = Math.round(img.width * scale); c.height = Math.round(img.height * scale);
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        c.toBlob((b) => {
          URL.revokeObjectURL(url);
          if (!b || b.size >= file.size) { resolve(file); return; }
          const name = file.name.replace(/\.[^.]+$/, "") + ".jpg";
          resolve(new File([b], name, { type: "image/jpeg" }));
        }, "image/jpeg", 0.85);
      } catch (_) { URL.revokeObjectURL(url); resolve(file); }
    };
    img.onerror = () => { URL.revokeObjectURL(url); resolve(file); };
    img.src = url;
  });
}

export default function PaymentNotification() {
  const { orderNumber } = useParams();
  // Giriş yapmış müşteri kendi siparişine yüklüyorsa sunucu oturumu kanıt sayar → doğrulama alanı opsiyonel.
  const { user } = useAuth() || {};
  const [order, setOrder] = useState(null);
  const [bank, setBank] = useState(null);
  const [loading, setLoading] = useState(true);
  const [file, setFile] = useState(null);
  const [note, setNote] = useState("");
  // Güvenlik: sipariş no tahmin edilebilir → siparişteki e-posta ya da telefonun son 4 hanesi istenir.
  const [contact, setContact] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const [o, b] = await Promise.allSettled([
          axios.get(`${API}/orders/by-number/${orderNumber}`, { headers: orderAccessHeaders(orderNumber) }),
          axios.get(`${API}/settings/public/bank-default`),
        ]);
        if (o.status === "fulfilled") {
          setOrder(o.value.data);
          if (o.value.data?.payment_notified) setDone(true);
        }
        if (b.status === "fulfilled") setBank(b.value.data?.bank || null);
      } catch (e) {
        // sessiz
      } finally {
        setLoading(false);
      }
    })();
  }, [orderNumber]);

  const copyIban = () => {
    if (bank?.iban) {
      navigator.clipboard?.writeText(bank.iban.replace(/\s/g, ""));
      toast.success("IBAN kopyalandı");
    }
  };

  const onPick = async (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    // Bazı telefon/bilgisayarlar dosya türünü boş gönderir (ör. HEIC) → uzantıdan tanı.
    const ext = (f.name.split(".").pop() || "").toLowerCase();
    const okType = f.type.startsWith("image/") || f.type === "application/pdf"
      || ["pdf", "jpg", "jpeg", "png", "heic", "heif", "webp"].includes(ext);
    if (!okType) { toast.error("Sadece PDF veya görsel (JPG, PNG, HEIC) yükleyebilirsiniz"); return; }
    // Büyük telefon fotoğrafları (10–20MB) yüklemeden önce küçültülür; küçültülemeyen
    // (ör. tarayıcının açamadığı HEIC) dosya olduğu gibi gönderilir.
    const ready = await shrinkImage(f);
    if (ready.size > 15 * 1024 * 1024) { toast.error("Dosya 15MB'tan büyük olamaz"); return; }
    setFile(ready);
  };

  const submit = async () => {
    if (!file) { toast.error("Lütfen dekont dosyası seçin"); return; }
    if (!contact.trim() && !user) { toast.error("Lütfen siparişteki e-posta adresinizi veya telefonunuzun son 4 hanesini girin"); return; }
    try {
      setSubmitting(true);
      const fd = new FormData();
      fd.append("file", file);
      fd.append("note", note);
      fd.append("contact", contact.trim());
      await axios.post(`${API}/orders/by-number/${orderNumber}/payment-notification`, fd, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      setDone(true);
      toast.success("Ödeme bildiriminiz alındı");
    } catch (e) {
      const st = e.response?.status;
      toast.error(e.response?.data?.detail
        || (st === 429 ? "Çok fazla deneme yapıldı, lütfen 1 dakika sonra tekrar deneyin."
          : st === 413 ? "Dosya çok büyük, lütfen daha küçük bir dosya seçin."
            : !e.response ? "Bağlantı kesildi, lütfen internetinizi kontrol edip tekrar deneyin."
              : "Gönderilemedi, lütfen tekrar deneyin"), { duration: 8000 });
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return <div className="max-w-xl mx-auto px-4 py-20 text-center text-gray-400">Yükleniyor…</div>;
  }

  if (!order) {
    return (
      <div className="max-w-xl mx-auto px-4 py-20 text-center">
        <h1 className="text-xl font-medium mb-2">Sipariş bulunamadı</h1>
        <p className="text-sm text-gray-500">“{orderNumber}” numaralı sipariş bulunamadı. Lütfen bağlantıyı kontrol edin.</p>
      </div>
    );
  }

  return (
    <div className="max-w-xl mx-auto px-4 py-10 md:py-16">
      <p className="text-xs uppercase tracking-[0.2em] text-gray-400 mb-1">Ödeme Bildirimi</p>
      <h1 className="text-2xl font-medium tracking-wide mb-6">Sipariş {order.order_number}</h1>

      <div className="border border-gray-200 p-4 mb-6">
        <div className="flex justify-between text-sm">
          <span className="text-gray-500">Tutar</span>
          <span className="font-medium">{(order.total ?? 0).toFixed(2)} ₺</span>
        </div>
      </div>

      {/* Banka bilgisi */}
      {bank && (
        <div className="border border-gray-200 p-4 mb-6">
          <h2 className="text-xs uppercase tracking-[0.2em] text-gray-400 mb-3">Havale / EFT Bilgileri</h2>
          <div className="space-y-1 text-sm">
            <p><span className="text-gray-500">Banka:</span> {bank.bank_name}</p>
            {bank.branch && <p><span className="text-gray-500">Şube:</span> {bank.branch}</p>}
            <div className="flex items-center gap-2">
              <span className="text-gray-500">IBAN:</span>
              <span className="font-mono font-medium">{bank.iban}</span>
              <button onClick={copyIban} className="text-gray-400 hover:text-black" title="Kopyala"><Copy size={14} /></button>
            </div>
            {bank.account_holder && <p><span className="text-gray-500">Hesap Sahibi:</span> {bank.account_holder}</p>}
          </div>
        </div>
      )}

      {done ? (
        <div className="border border-emerald-200 bg-emerald-50 p-5 text-center">
          <CheckCircle2 className="mx-auto mb-2 text-emerald-600" size={28} />
          <p className="text-sm text-emerald-900 font-medium">Ödeme bildiriminiz alındı.</p>
          <p className="text-xs text-emerald-700 mt-1">Ödemeniz kontrol edildikten sonra siparişiniz onaylanacak ve bilgilendirileceksiniz.</p>
          <button onClick={() => setDone(false)} className="text-xs underline underline-offset-4 text-emerald-800 mt-3">Yeni dekont yükle</button>
        </div>
      ) : (
        <div>
          <h2 className="text-xs uppercase tracking-[0.2em] text-gray-400 mb-3">Dekont Yükle</h2>
          <label className="block border-2 border-dashed border-gray-300 hover:border-gray-500 transition-colors cursor-pointer p-8 text-center">
            <input type="file" accept="image/*,application/pdf" onChange={onPick} className="hidden" />
            {file ? (
              <div className="flex items-center justify-center gap-2 text-sm text-gray-800">
                <FileText size={18} /> {file.name}
              </div>
            ) : (
              <div className="text-gray-400">
                <UploadCloud className="mx-auto mb-2" size={26} />
                <p className="text-sm">PDF veya görsel seçmek için tıklayın</p>
                <p className="text-[11px] mt-1">PDF, JPG, PNG veya HEIC</p>
              </div>
            )}
          </label>

          {/* Buton eskiden bu alan boşken SOLUK ve tıklanamaz kalıyordu — müşteri nedenini göremiyordu
              ("dekont yükleyemiyorum"). Artık alan etiketli; boşsa butona basınca ne gerektiği söylenir. */}
          <label className="block text-xs text-gray-600 mt-4 mb-1">
            Doğrulama: siparişteki e-posta adresiniz veya telefonunuzun son 4 hanesi
            {user ? <span className="text-gray-400"> (giriş yaptığınız için gerekmez)</span> : <span className="text-red-500"> *</span>}
          </label>
          <input
            type="text"
            value={contact}
            onChange={(e) => setContact(e.target.value)}
            placeholder="Siparişteki e-posta adresiniz veya telefonunuzun son 4 hanesi"
            autoComplete="email"
            className="w-full border border-gray-200 p-3 text-sm focus:outline-none focus:border-gray-500"
          />

          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Not (opsiyonel) — gönderen ad/soyad, tarih vb."
            rows={2}
            className="w-full border border-gray-200 p-3 text-sm mt-4 resize-none focus:outline-none focus:border-gray-500"
          />

          <button
            onClick={submit}
            disabled={submitting}
            className="w-full bg-black text-white py-3 mt-4 text-xs uppercase tracking-[0.2em] hover:bg-gray-800 transition-colors disabled:opacity-40"
          >
            {submitting ? "Gönderiliyor…" : "Ödeme Bildirimini Gönder"}
          </button>
        </div>
      )}
    </div>
  );
}
