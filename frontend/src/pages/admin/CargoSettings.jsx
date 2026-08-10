/**
 * CargoSettings.jsx — Kargo Firması Ayarları sayfası.
 *
 * İki bölümden oluşur:
 *   1) DhlPollMonitor — DHL/MNG kargo takip senkronizasyonunun CANLI İZLEME paneli.
 *      Backend her 5 dk'da bir site siparişlerini MNG/DHL'den sorgular; bu panel
 *      o taramanın son durumunu (ne zaman çalıştı, kaç sipariş sorgulandı, kaçı
 *      güncellendi, hata var mı, ayar aktif mi) gösterir ve "Şimdi Çalıştır" ile
 *      elle tetiklenebilir.
 *        - GET  /api/orders/cargo/poll-health  → izleme verisi
 *        - POST /api/orders/cargo/poll-now     → taramayı hemen çalıştır
 *   2) ProviderSettings (kind="cargo") — kargo firması credential ayarları.
 *
 * Bağlantılı:
 *   - components/admin/ProviderSettings.jsx (provider credential UI)
 *   - backend/scheduler.py  (_dhl_cargo_poll_tick + db.settings.dhl_poll_health)
 *   - backend/routes/orders.py (poll-health / poll-now endpointleri)
 */
import { useEffect, useState, useCallback } from "react";
import axios from "axios";
import { toast } from "sonner";
import {
  Truck, RefreshCw, Play, CheckCircle2, AlertTriangle,
  PauseCircle, Clock, HelpCircle, Wifi, Search,
} from "lucide-react";
import ProviderSettings from "../../components/admin/ProviderSettings";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const authHeaders = () => ({ Authorization: `Bearer ${localStorage.getItem("token")}` });

/** ISO zamanı "3 dk önce" gibi göreli Türkçe metne çevirir. */
function relTime(iso) {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const sec = Math.floor((Date.now() - t) / 1000);
  if (sec < 0) return "az sonra";
  if (sec < 60) return `${sec} sn önce`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} dk önce`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} saat önce`;
  const d = Math.floor(hr / 24);
  return `${d} gün önce`;
}

function fmtClock(iso) {
  if (!iso) return "—";
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return "—";
  return dt.toLocaleString("tr-TR", { dateStyle: "short", timeStyle: "medium" });
}

/** Durum koduna göre rozet stili + Türkçe etiket + ikon. */
const STATUS_META = {
  ok:       { label: "Çalışıyor", cls: "bg-emerald-100 text-emerald-700 border-emerald-200", Icon: CheckCircle2 },
  running:  { label: "Şu an taranıyor", cls: "bg-blue-100 text-blue-700 border-blue-200", Icon: RefreshCw },
  skipped:  { label: "Atlandı (ayar kapalı)", cls: "bg-amber-100 text-amber-700 border-amber-200", Icon: PauseCircle },
  error:    { label: "Hata", cls: "bg-red-100 text-red-700 border-red-200", Icon: AlertTriangle },
  unknown:  { label: "Henüz çalışmadı", cls: "bg-gray-100 text-gray-600 border-gray-200", Icon: HelpCircle },
};

function StatCard({ label, value, accent }) {
  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4">
      <div className="text-[11px] uppercase tracking-wide text-gray-500">{label}</div>
      <div className={`text-2xl font-bold mt-1 ${accent || "text-gray-900"}`}>{value}</div>
    </div>
  );
}

function DhlPollMonitor() {
  const [h, setH] = useState(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [test, setTest] = useState(null);
  const [testing, setTesting] = useState(false);
  const [backfill, setBackfill] = useState(null);
  const [backfilling, setBackfilling] = useState(false);
  const [qNo, setQNo] = useState("");
  const [qRes, setQRes] = useState(null);
  const [querying, setQuerying] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/orders/cargo/poll-health`, { headers: authHeaders() });
      setH(data || null);
    } catch (e) {
      // İzleme verisi alınamadı — HTTP durumunu sakla (404 = backend henüz güncellenmemiş olabilir)
      const code = e?.response?.status || 0;
      setH((prev) => (prev && !prev._failed ? prev : { _failed: true, _http: code }));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 30000); // 30 sn'de bir tazele
    return () => clearInterval(t);
  }, [load]);

  const runNow = async () => {
    setRunning(true);
    try {
      await axios.post(`${API}/orders/cargo/poll-now`, {}, { headers: authHeaders() });
      toast.success("DHL/MNG kargo taraması çalıştırıldı.");
      await load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Tarama çalıştırılamadı.");
    } finally {
      setRunning(false);
    }
  };

  const runTest = async () => {
    setTesting(true);
    setTest(null);
    try {
      const { data } = await axios.get(`${API}/orders/cargo/mng-test`, { headers: authHeaders() });
      setTest(data || { ok: false, error: "Boş yanıt" });
      if (data?.ok) toast.success("MNG/DHL bağlantısı başarılı.");
      else toast.error("MNG/DHL bağlantısı başarısız — detay panelde.");
    } catch (e) {
      const code = e?.response?.status;
      setTest({ ok: false, error: e?.response?.data?.detail || e.message || "İstek başarısız", _http: code });
      toast.error(code === 404 ? "Test endpoint'i bulunamadı (backend güncellenmemiş)." : "Bağlantı testi başarısız.");
    } finally {
      setTesting(false);
    }
  };

  const queryOne = async () => {
    const no = (qNo || "").trim();
    if (!no) { toast("Sipariş no gir (örn. W10063)"); return; }
    setQuerying(true);
    setQRes(null);
    try {
      const { data } = await axios.get(
        `${API}/orders/cargo/mng-test?siparis_no=${encodeURIComponent(no)}`,
        { headers: authHeaders() }
      );
      setQRes(data?.shipment_status || { error: data?.shipment_status_error || "Yanıt yok" });
    } catch (e) {
      setQRes({ error: e?.response?.data?.detail || e.message || "İstek başarısız" });
    } finally {
      setQuerying(false);
    }
  };

  const runBackfill = async () => {
    setBackfilling(true);
    setBackfill(null);
    try {
      // Tüm site siparişlerini (durum farkı gözetmeden) tara, eksik takip no'ları doldur.
      const { data } = await axios.post(
        `${API}/orders/cargo/backfill-tracking?all_statuses=true&site_only=true`,
        {}, { headers: authHeaders() }
      );
      setBackfill(data || null);
      const upd = data?.updated ?? 0;
      if (upd > 0) toast.success(`${upd} siparişe takip no yazıldı.`);
      else toast(`Yazılabilecek yeni takip no bulunamadı — döküme bak.`, { icon: "ℹ️" });
      await load();
    } catch (e) {
      const code = e?.response?.status;
      toast.error(code === 404 ? "Backfill endpoint'i bulunamadı (backend güncellenmemiş)." : (e?.response?.data?.detail || "Toplama başarısız."));
    } finally {
      setBackfilling(false);
    }
  };

  const status = h?.status || (h?._failed ? "error" : "unknown");
  const meta = STATUS_META[status] || STATUS_META.unknown;
  const Icon = meta.Icon;
  const interval = h?.interval_min || 5;

  return (
    <div className="bg-gradient-to-br from-slate-50 to-white border border-gray-200 rounded-2xl p-5 space-y-4">
      {/* Başlık + aksiyonlar */}
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h2 className="text-lg font-bold flex items-center gap-2 text-gray-900">
            <Truck className="w-5 h-5 text-indigo-600" /> DHL / MNG Takip Senkron İzleme
          </h2>
          <p className="text-sm text-gray-500 mt-1">
            Site siparişlerinin kargo durumu her <b>{interval} dk</b>'da bir DHL/MNG'den
            otomatik çekilir (ilk okutma → Kargoya Verildi, teslim → Teslim Edildi).
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={load}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg border border-gray-300 text-sm text-gray-700 hover:bg-gray-50"
            title="Paneli yenile"
          >
            <RefreshCw className="w-4 h-4" /> Yenile
          </button>
          <button
            onClick={runTest}
            disabled={testing}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg border border-indigo-300 text-sm text-indigo-700 hover:bg-indigo-50 disabled:opacity-60"
            title="MNG/DHL servisine canlı bağlantı testi (Baglanti_Test)"
          >
            <Wifi className={`w-4 h-4 ${testing ? "animate-pulse" : ""}`} />
            {testing ? "Test ediliyor…" : "Bağlantı Testi"}
          </button>
          <button
            onClick={runBackfill}
            disabled={backfilling}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg border border-emerald-300 text-sm text-emerald-700 hover:bg-emerald-50 disabled:opacity-60"
            title="Takip no'su eksik tüm site siparişlerini kargo firmasından tarayıp doldurur"
          >
            <Search className={`w-4 h-4 ${backfilling ? "animate-pulse" : ""}`} />
            {backfilling ? "Taranıyor…" : "Takip No'ları Topla"}
          </button>
          <button
            onClick={runNow}
            disabled={running}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700 disabled:opacity-60"
          >
            <Play className={`w-4 h-4 ${running ? "animate-pulse" : ""}`} />
            {running ? "Çalışıyor…" : "Şimdi Çalıştır"}
          </button>
        </div>
      </div>

      {/* Durum rozeti + son çalışma */}
      <div className="flex items-center gap-3 flex-wrap">
        <span className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-sm font-medium border ${meta.cls}`}>
          <Icon className={`w-4 h-4 ${status === "running" ? "animate-spin" : ""}`} /> {meta.label}
        </span>
        <span className="inline-flex items-center gap-1.5 text-sm text-gray-500">
          <Clock className="w-4 h-4" />
          Son çalışma: <b className="text-gray-700">{relTime(h?.last_finish_at)}</b>
          <span className="text-gray-400">({fmtClock(h?.last_finish_at)})</span>
        </span>
      </div>

      {/* Panel verisi alınamadı (büyük olasılıkla backend henüz güncellenmedi) */}
      {h?._failed && (
        <div className="flex items-start gap-2 text-sm bg-orange-50 border border-orange-200 text-orange-800 rounded-lg p-3">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
          <div>
            <b>İzleme verisi alınamadı{h?._http ? ` (HTTP ${h._http})` : ""}.</b>{" "}
            {h?._http === 404
              ? "Backend (Railway) bu sürümle henüz güncellenmemiş olabilir — git push sonrası Railway deploy'unun bitmesini bekle, sonra Yenile'ye bas."
              : "Backend'e ulaşılamadı. Railway servisinin ayakta olduğunu kontrol et."}
          </div>
        </div>
      )}

      {/* Bağlantı testi sonucu (canlı MNG/DHL ping) */}
      {test && (
        <div className={`text-sm rounded-lg p-3 border ${test.ok ? "bg-emerald-50 border-emerald-200 text-emerald-800" : "bg-red-50 border-red-200 text-red-800"}`}>
          <div className="flex items-center gap-2 font-medium">
            {test.ok ? <CheckCircle2 className="w-4 h-4" /> : <AlertTriangle className="w-4 h-4" />}
            MNG/DHL bağlantı testi: {test.ok ? "BAŞARILI" : "BAŞARISIZ"}
            {typeof test.ms === "number" && <span className="text-xs opacity-70">({test.ms} ms)</span>}
          </div>
          {test.error && <div className="font-mono text-xs mt-1 break-all">{test.error}</div>}
          {test.result && <div className="text-xs mt-1 opacity-80">Yanıt: {test.result}</div>}
          {test.settings && (
            <div className="text-xs mt-1 opacity-80">
              Kullanıcı: {test.settings.username} · Şifre: {test.settings.has_password ? "var" : "yok"} · {test.settings.customer_code}
            </div>
          )}
        </div>
      )}

      {/* Takip no toplama (backfill) sonucu — neden bulunamadığının sayısal dökümü */}
      {backfill && (
        <div className="text-sm rounded-lg p-3 border bg-slate-50 border-slate-200 text-slate-700 space-y-2">
          <div className="flex items-center gap-2 font-medium text-slate-800">
            <Search className="w-4 h-4" />
            Takip no toplama sonucu — {backfill.site_taranan ?? backfill.scanned ?? 0} site siparişi tarandı
          </div>
          {backfill.limit_hit && (
            <div className="flex items-start gap-2 text-xs bg-orange-50 border border-orange-300 text-orange-800 rounded p-2">
              <Clock className="w-4 h-4 mt-0.5 shrink-0" />
              <span><b>MNG/DHL günlük sorgu limitine takıldı</b> — tarama bu noktada durduruldu (limiti uzatmamak için). Bu limit kargo firmasındadır. Yarın otomatik devam eder; eksik kalanları yarın tekrar topla.</span>
            </div>
          )}
          <div className="grid grid-cols-2 md:grid-cols-3 gap-2 text-xs">
            <div className="bg-emerald-50 border border-emerald-200 rounded p-2">
              <div className="text-emerald-700 font-bold text-lg">{backfill.diagnosis?.guncellendi ?? 0}</div>
              <div className="text-emerald-700">takip no yazıldı</div>
            </div>
            <div className="bg-white border border-gray-200 rounded p-2">
              <div className="font-bold text-lg">{backfill.diagnosis?.zaten_takip_no_vardi ?? 0}</div>
              <div className="text-gray-500">zaten vardı</div>
            </div>
            <div className="bg-amber-50 border border-amber-200 rounded p-2">
              <div className="text-amber-700 font-bold text-lg">{backfill.diagnosis?.mngde_kayit_var_takip_no_yok ?? 0}</div>
              <div className="text-amber-700">kayıt var, no atanmamış</div>
            </div>
            <div className="bg-red-50 border border-red-200 rounded p-2">
              <div className="text-red-700 font-bold text-lg">{backfill.diagnosis?.mngde_kayit_yok_veya_yetki ?? 0}</div>
              <div className="text-red-700">satır dönmedi (bulunamadı / whitelist)</div>
            </div>
            <div className="bg-red-50 border border-red-200 rounded p-2">
              <div className="text-red-700 font-bold text-lg">{backfill.diagnosis?.sorgu_hatasi ?? 0}</div>
              <div className="text-red-700">sorgu hatası</div>
            </div>
          </div>
          {/* Yorum: hangi kova baskınsa onun anlamı */}
          {(() => {
            const d = backfill.diagnosis || {};
            if ((d.guncellendi ?? 0) > 0) return <p className="text-xs text-emerald-700">✓ {d.guncellendi} sipariş dolduruldu — Siparişler sayfasında artık takip no görünmeli.</p>;
            if ((d.mngde_kayit_yok_veya_yetki ?? 0) > 0 || (d.sorgu_hatasi ?? 0) > 0)
              return <p className="text-xs text-red-700">MNG/DHL'den satır dönmüyor. En olası sebep: <b>IP whitelist / yetki</b> ya da takip no'nun bizim sipariş numaramızla (W…) eşleşmemesi. "Bağlantı Testi"ne bas; başarısızsa MNG paneli &gt; API IP izinlerini kontrol et.</p>;
            if ((d.mngde_kayit_var_takip_no_yok ?? 0) > 0)
              return <p className="text-xs text-amber-700">Kayıt var ama kargo firması henüz <b>gönderi no</b> atamamış (paket fiilen şubede okutulunca dolar). Okutma sonrası tekrar topla.</p>;
            return <p className="text-xs text-slate-500">Yazılabilecek yeni takip no bulunamadı.</p>;
          })()}
          {Array.isArray(backfill.failedList) && backfill.failedList.length > 0 && (
            <details className="text-xs">
              <summary className="cursor-pointer text-slate-600 select-none">Bulunamayanlar ({backfill.failed})</summary>
              <div className="mt-1 font-mono break-all text-slate-500">
                {backfill.failedList.slice(0, 40).map((f, i) => (
                  <div key={i}>{f.no} — {f.reason}{f.kargo_statu_aciklama ? ` (${f.kargo_statu_aciklama})` : f.err ? ` (${f.err})` : ""}</div>
                ))}
              </div>
            </details>
          )}
        </div>
      )}

      {/* Tek sipariş sorgula — kargo firmasından ham yanıt + bulunan takip no */}
      <div className="text-sm rounded-lg p-3 border bg-white border-gray-200 space-y-2">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-gray-700 font-medium">Tek sipariş sorgula:</span>
          <input
            value={qNo}
            onChange={(e) => setQNo(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") queryOne(); }}
            placeholder="W10063"
            className="px-2 py-1.5 border border-gray-300 rounded-lg text-sm font-mono w-36"
          />
          <button
            onClick={queryOne}
            disabled={querying}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-gray-300 text-sm hover:bg-gray-50 disabled:opacity-60"
          >
            <Search className={`w-4 h-4 ${querying ? "animate-pulse" : ""}`} />
            {querying ? "Sorgulanıyor…" : "Sorgula"}
          </button>
          <span className="text-xs text-gray-400">Kargo firmasından canlı yanıtı gösterir (DB'yi değiştirmez).</span>
        </div>
        {qRes && (
          <div className="text-xs space-y-1">
            {qRes.error ? (
              <div className="text-red-600 break-all">Hata: {qRes.error}</div>
            ) : (
              <>
                <div className="flex flex-wrap gap-x-4 gap-y-1">
                  <span>Gönderi No: <b className={qRes.gonderi_no ? "text-emerald-700 font-mono" : "text-gray-400"}>{qRes.gonderi_no || "(boş)"}</b></span>
                  <span>Referans: <span className="font-mono">{qRes.referans_no || "-"}</span></span>
                  <span>MNG Sip.No: <span className="font-mono">{qRes.mng_siparis_no || "-"}</span></span>
                  <span>Durum: {qRes.kargo_statu_aciklama || qRes.kargo_statu || "-"}</span>
                  {qRes.method && <span className="text-gray-400">({qRes.method})</span>}
                </div>
                {qRes.gonderi_no
                  ? <div className="text-emerald-700">✓ Takip no bulundu — "Takip No'ları Topla" ile bu siparişe yazılacak.</div>
                  : <div className="text-amber-700">Bu yanıtta gönderi no boş. Aşağıdaki ham yanıtta numara görünüyorsa bana ilet, parser'ı ona göre genişleteyim.</div>}
                {qRes.raw_preview && (
                  <details>
                    <summary className="cursor-pointer text-gray-500 select-none">Ham yanıt (kargo firması)</summary>
                    <pre className="mt-1 p-2 bg-gray-50 border border-gray-200 rounded overflow-x-auto whitespace-pre-wrap break-all text-[10px] text-gray-600">{qRes.raw_preview}</pre>
                  </details>
                )}
              </>
            )}
          </div>
        )}
      </div>

      {/* MNG/DHL günlük sorgu limiti (kargocu tarafı) — özel uyarı */}
      {h?.daily_limit && (
        <div className="flex items-start gap-2 text-sm bg-orange-50 border border-orange-300 text-orange-800 rounded-lg p-3">
          <Clock className="w-4 h-4 mt-0.5 shrink-0" />
          <div>
            <b>MNG/DHL günlük sorgu limitine takıldı.</b> Bu limit <u>kargo firması tarafındadır</u>,
            bizim kodumuzda limit yoktur. Bugünlük sorgu durduruldu; yarın otomatik devam eder.
            Limiti aşmamak için artık <b>sadece W/IW site siparişleri</b> ve aynı sipariş için
            <b> belirli aralıkla</b> sorgu yapılıyor.
          </div>
        </div>
      )}

      {/* Ayar kapalı / hata uyarısı */}
      {status === "skipped" && !h?.daily_limit && (
        <div className="flex items-start gap-2 text-sm bg-amber-50 border border-amber-200 text-amber-800 rounded-lg p-3">
          <PauseCircle className="w-4 h-4 mt-0.5 shrink-0" />
          <div>
            <b>Senkron atlanıyor.</b> {h?.skipped_reason || "MNG/DHL ayarı aktif değil."}
            {" "}Aşağıdaki kargo firması ayarlarından MNG/DHL'i aktif edip kullanıcı adını girin.
          </div>
        </div>
      )}
      {status === "error" && !h?._failed && (
        <div className="flex items-start gap-2 text-sm bg-red-50 border border-red-200 text-red-800 rounded-lg p-3">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
          <div>
            <b>Son taramada hata oluştu.</b> Aşağıdaki <b>Bağlantı Testi</b> ile MNG/DHL servisine
            erişimi kontrol et (genelde IP whitelist / WSDL erişimi).
            {h?.last_error ? <div className="font-mono text-xs mt-1 break-all">{h.last_error}</div> : null}
          </div>
        </div>
      )}
      {status !== "error" && h?.last_note && !h?._failed && (
        <div className="flex items-start gap-2 text-sm bg-slate-50 border border-slate-200 text-slate-600 rounded-lg p-3">
          <HelpCircle className="w-4 h-4 mt-0.5 shrink-0" />
          <div>Son tarama notu: {h.last_note}</div>
        </div>
      )}

      {/* Sayaçlar */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <StatCard label="Eşleşen sipariş" value={h?.matched ?? 0} />
        <StatCard label="MNG'den yanıt" value={h?.processed ?? 0} accent={(h?.matched ?? 0) > 0 && (h?.processed ?? 0) === 0 ? "text-red-600" : "text-gray-900"} />
        <StatCard label="Kargoya verilen" value={h?.shipped ?? 0} accent="text-indigo-600" />
        <StatCard label="Teslim edilen" value={h?.delivered ?? 0} accent="text-emerald-600" />
        <StatCard label="Hatalı sorgu" value={h?.errors ?? 0} accent={(h?.errors ?? 0) > 0 ? "text-red-600" : "text-gray-900"} />
      </div>
      {(h?.matched ?? 0) > 0 && (h?.processed ?? 0) === 0 && !h?._failed && (
        <p className="text-xs text-red-600">
          {h.matched} sipariş eşleşti ama MNG/DHL'den hiçbirine yanıt gelmedi — bağlantı/erişim sorunu güçlü ihtimal. "Bağlantı Testi"ne bas.
        </p>
      )}
      {(h?.matched ?? 0) === 0 && status === "ok" && !h?._failed && (
        <p className="text-xs text-amber-600">
          Sorguya uyan site siparişi yok (kargo barkodu oluşturulmuş, son 45 gün, durumu kargo aşamasında olan site siparişi bulunamadı).
        </p>
      )}

      {/* Ayar/süre özeti */}
      <div className="flex items-center gap-4 flex-wrap text-xs text-gray-500">
        <span>MNG/DHL ayarı: {h?.mng_active ? <b className="text-emerald-600">Aktif</b> : <b className="text-red-600">Kapalı</b>}</span>
        <span>Kullanıcı adı: {h?.mng_user_set ? <b className="text-emerald-600">Girilmiş</b> : <b className="text-red-600">Boş</b>}</span>
        {typeof h?.duration_ms === "number" && h?.duration_ms > 0 && (
          <span>Son tarama süresi: <b className="text-gray-700">{(h.duration_ms / 1000).toFixed(1)} sn</b></span>
        )}
      </div>

      {/* Geçmiş (son koşular) */}
      {Array.isArray(h?.history) && h.history.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-gray-600 hover:text-gray-900 select-none">
            Son taramalar ({h.history.length})
          </summary>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-xs border-collapse">
              <thead>
                <tr className="text-left text-gray-500 border-b border-gray-200">
                  <th className="py-1.5 pr-3 font-medium">Zaman</th>
                  <th className="py-1.5 pr-3 font-medium">Sonuç</th>
                  <th className="py-1.5 pr-3 font-medium text-right">Sorgulanan</th>
                  <th className="py-1.5 pr-3 font-medium text-right">Kargoya</th>
                  <th className="py-1.5 pr-3 font-medium text-right">Teslim</th>
                  <th className="py-1.5 pr-3 font-medium text-right">Hata</th>
                  <th className="py-1.5 pr-3 font-medium">Not</th>
                </tr>
              </thead>
              <tbody>
                {h.history.map((r, i) => {
                  const m = STATUS_META[r.status] || STATUS_META.unknown;
                  return (
                    <tr key={i} className="border-b border-gray-100">
                      <td className="py-1.5 pr-3 whitespace-nowrap text-gray-600">{fmtClock(r.at)}</td>
                      <td className="py-1.5 pr-3"><span className={`px-2 py-0.5 rounded-full border ${m.cls}`}>{m.label}</span></td>
                      <td className="py-1.5 pr-3 text-right">{r.processed ?? 0}</td>
                      <td className="py-1.5 pr-3 text-right">{r.shipped ?? 0}</td>
                      <td className="py-1.5 pr-3 text-right">{r.delivered ?? 0}</td>
                      <td className={`py-1.5 pr-3 text-right ${(r.errors ?? 0) > 0 ? "text-red-600 font-medium" : ""}`}>{r.errors ?? 0}</td>
                      <td className="py-1.5 pr-3 text-gray-500 max-w-[220px] truncate" title={r.error || ""}>{r.error || "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </details>
      )}

      {loading && !h && <div className="text-sm text-gray-400">İzleme verisi yükleniyor…</div>}
    </div>
  );
}

export default function CargoSettings() {
  return (
    <div className="space-y-6">
      <DhlPollMonitor />
      <ProviderSettings
        kind="cargo"
        title="Kargo Firması Ayarları"
        subtitle="Çalışacağınız kargo firmalarını yapılandırın. Birden fazla firma için bilgi girilebilir, sistem aktif seçilen firmayı kullanır (sipariş kargo oluşturma, etiket basma vb.)."
      />
    </div>
  );
}
