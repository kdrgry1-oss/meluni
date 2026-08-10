/**
 * AppConfirm.jsx — global in-app onay diyaloğu.
 *
 * Amaç: tarayıcının native `window.confirm(...)` (sekme üstü küçük popup)
 * yerine uygulama içinde Shadcn AlertDialog bazlı pop-up göstermek.
 * Kullanım:
 *   import { appConfirm } from "../components/admin/AppConfirm";
 *   if (!(await appConfirm("Silinsin mi?"))) return;
 *
 *   // Detaylı kullanım:
 *   const ok = await appConfirm({
 *     title: "Ürünleri sil",
 *     description: "12 ürün geri alınamayacak şekilde silinecek.",
 *     confirmText: "Evet, Sil",
 *     cancelText: "Vazgeç",
 *     variant: "danger",  // "danger" | "default" | "warning"
 *   });
 *
 * Root bileşen <AppConfirmRoot /> tek bir yerde (AdminLayout vb.) mount
 * edilmelidir. AppConfirm mantığı tamamen state-machine + promise'a dayalı.
 */
import { useEffect, useState } from "react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "../ui/alert-dialog";

// Tek global resolver — aynı anda 2 onay açılmaz
let _resolver = null;
let _setState = null;

/**
 * @param {string|object} optionsOrMessage
 * @returns {Promise<boolean>}
 */
export function appConfirm(optionsOrMessage) {
  if (typeof optionsOrMessage === "string") {
    optionsOrMessage = { description: optionsOrMessage };
  }
  return new Promise((resolve) => {
    // Önceki promise iptal
    if (_resolver) _resolver(false);
    _resolver = resolve;
    _setState?.({
      open: true,
      title: optionsOrMessage.title || "Emin misiniz?",
      description: optionsOrMessage.description || "",
      confirmText: optionsOrMessage.confirmText || "Evet",
      cancelText: optionsOrMessage.cancelText || "Vazgeç",
      variant: optionsOrMessage.variant || "default",
    });
  });
}

export function AppConfirmRoot() {
  const [state, setState] = useState({
    open: false,
    title: "",
    description: "",
    confirmText: "Evet",
    cancelText: "Vazgeç",
    variant: "default",
  });

  useEffect(() => {
    _setState = setState;
    // Eski window.confirm'e düşen kodlar için fallback: window.appConfirm
    try { window.appConfirm = appConfirm; } catch { /* ignore */ }
    return () => { _setState = null; };
  }, []);

  const close = (ok) => {
    setState((s) => ({ ...s, open: false }));
    if (_resolver) { _resolver(ok); _resolver = null; }
  };

  const confirmStyle =
    state.variant === "danger"
      ? "bg-red-600 hover:bg-red-700 text-white"
      : state.variant === "warning"
      ? "bg-amber-500 hover:bg-amber-600 text-white"
      : "bg-black hover:bg-gray-800 text-white";

  return (
    <AlertDialog open={state.open} onOpenChange={(o) => !o && close(false)}>
      <AlertDialogContent data-testid="app-confirm-dialog">
        <AlertDialogHeader>
          <AlertDialogTitle>{state.title}</AlertDialogTitle>
          {state.description && (
            <AlertDialogDescription className="whitespace-pre-line">
              {state.description}
            </AlertDialogDescription>
          )}
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel onClick={() => close(false)} data-testid="app-confirm-cancel">
            {state.cancelText}
          </AlertDialogCancel>
          <AlertDialogAction
            onClick={() => close(true)}
            className={confirmStyle}
            data-testid="app-confirm-ok"
          >
            {state.confirmText}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
