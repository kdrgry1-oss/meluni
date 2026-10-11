import { useEffect, useState } from "react";
import { APP_UPDATE_EVENT } from "../lib/appVersionGate";

/**
 * ADM-15: Sunucu 426 (app_update_required) döndüğünde engelleyici "güncelleme gerekli" ekranı.
 * Yalnız native uygulamada tetiklenir (web istemcisi sürüm başlığı göndermez → 426 almaz).
 * Görsel düzen: tek dikey eksen, tüm öğeler ortalı, öğeler arası boşluk her yerde aynı (mt-6).
 */
export default function AppUpdateRequired() {
  const [info, setInfo] = useState(null);

  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const onRequired = (e) => setInfo((e && e.detail) || {});
    window.addEventListener(APP_UPDATE_EVENT, onRequired);
    return () => window.removeEventListener(APP_UPDATE_EVENT, onRequired);
  }, []);

  if (!info) return null;
  const isAdminApp = info.app === "admin";

  return (
    <div
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="app-update-title"
      data-testid="app-update-required"
      className="fixed inset-0 z-[10000] flex items-center justify-center bg-white px-6"
    >
      <div className="flex w-full max-w-sm flex-col items-center text-center">
        <img src="/logo.png" alt="" aria-hidden="true" className="h-10 w-auto object-contain" />
        <h1 id="app-update-title" className="mt-6 text-xl font-semibold text-gray-900">
          Uygulamanın güncellenmesi gerekiyor
        </h1>
        <p className="mt-6 text-sm leading-relaxed text-gray-600">
          {isAdminApp
            ? "Devam etmek için yönetim uygulamasının güncel sürümünü yükleyin. Güncellemeyi bulamıyorsanız yöneticinize başvurun."
            : "Devam etmek için uygulamanın güncel sürümünü mağazadan yükleyin."}
        </p>
        {info.minVersion ? (
          <p className="mt-6 text-xs uppercase tracking-wider text-gray-400" data-testid="app-update-min-version">
            Gerekli en düşük sürüm: {info.minVersion}
          </p>
        ) : null}
        {info.storeUrl ? (
          <a
            href={info.storeUrl}
            rel="noopener noreferrer"
            className="mt-6 inline-flex h-11 w-full items-center justify-center bg-black px-6 text-sm font-medium text-white"
            data-testid="app-update-store-link"
          >
            Mağazada Güncelle
          </a>
        ) : null}
      </div>
    </div>
  );
}
