import { useEffect, useState } from "react";
import { fetchAdminDocument } from "../../lib/adminDocuments";

export default function InvoiceDocument({ orderId, externalUrl }) {
  const [document, setDocument] = useState(null);
  useEffect(() => {
    let active = true;
    let blobUrl;
    setDocument(null);
    if (typeof externalUrl === "string" && externalUrl.startsWith("https://")) {
      setDocument({ orderId, externalUrl, url: externalUrl });
      return;
    }
    fetchAdminDocument(`/orders/${encodeURIComponent(orderId)}/invoice/print`)
      .then((blob) => {
        if (!active) return;
        blobUrl = URL.createObjectURL(blob);
        setDocument({ orderId, externalUrl, url: blobUrl });
      })
      .catch(() => { if (active) setDocument({ orderId, externalUrl, error: true }); });
    return () => { active = false; if (blobUrl) URL.revokeObjectURL(blobUrl); };
  }, [orderId, externalUrl]);
  if (!document || document.orderId !== orderId || document.externalUrl !== externalUrl) return <p>Fatura yükleniyor…</p>;
  if (document.error) return <p role="alert">Fatura yüklenemedi. Oturumunuzu ve fatura görüntüleme yetkinizi kontrol edin.</p>;
  return <>
    <a href={document.url} target="_blank" rel="noreferrer" className="text-sm text-blue-600 hover:underline">Yeni sekmede aç ↗</a>
    <iframe src={document.url} title="Fatura" sandbox="allow-scripts allow-modals" referrerPolicy="no-referrer" className="w-full rounded border bg-white" style={{ height: 520 }} />
  </>;
}
