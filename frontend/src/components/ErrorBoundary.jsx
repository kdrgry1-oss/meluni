import React from "react";

// Deploy sonrası eski chunk hash'i 404 verince lazy() import REDDEDİLİR → ErrorBoundary yoksa
// TÜM uygulama beyaz ekran olur (özellikle mobil/in-app tarayıcıda eski index.html cache'lenince).
// Bu sınır: chunk-yükleme hatasında TAZE index.html + bundle ile BİR KEZ tazeler (sonsuz döngü
// koruması: URL _r param + sessionStorage). Diğer render hatalarında boş beyaz yerine "Yenile"
// düğmeli bir kurtarma ekranı gösterir.
const CHUNK_ERR = /ChunkLoadError|Loading chunk|dynamically imported module|Importing a module script failed|Failed to fetch dynamically|error loading dynamically imported module/i;

function _healedAlready() {
  if (/[?&]_r=/.test(window.location.search)) return true;
  try { return sessionStorage.getItem("__heal") === "1"; } catch (e) { return false; }
}

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error) {
    try {
      const msg = String((error && (error.message || error.name)) || error || "");
      if (CHUNK_ERR.test(msg) && !_healedAlready()) {
        try { sessionStorage.setItem("__heal", "1"); } catch (e) {}
        const u = window.location.pathname +
          (window.location.search ? window.location.search + "&" : "?") +
          "_r=" + Date.now() + window.location.hash;
        window.location.replace(u); // taze index.html + taze chunk çek
      }
    } catch (e) { /* yut */ }
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{ minHeight: "70vh", display: "flex", flexDirection: "column",
          alignItems: "center", justifyContent: "center", gap: 14, padding: 24,
          textAlign: "center", fontFamily: "system-ui, -apple-system, sans-serif" }}>
          <div style={{ fontSize: 18, fontWeight: 700, color: "#111" }}>Bir şeyler ters gitti</div>
          <div style={{ fontSize: 14, color: "#666", maxWidth: 320 }}>
            Sayfa yüklenirken bir sorun oluştu. Lütfen sayfayı yenileyin.
          </div>
          <button
            onClick={() => { try { sessionStorage.removeItem("__heal"); } catch (e) {} window.location.reload(); }}
            style={{ padding: "10px 22px", background: "#111", color: "#fff", border: "none",
              borderRadius: 10, fontSize: 14, fontWeight: 700, cursor: "pointer" }}>
            Yenile
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
