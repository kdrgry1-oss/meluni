/**
 * AttrCacheUploadDialog.jsx — HB/Temu gibi canlı API entegrasyonu olmayan
 * pazaryerleri için, kullanıcının kendi panelinden export ettiği attribute
 * listesini (JSON) sisteme yüklemesine olanak sağlar.
 *
 * Props:
 *   - open, onClose(ok: boolean)
 *   - marketplace: "hepsiburada" | "temu" | ...
 *   - mpCategoryId: otomatik doldurulur (modal açan bilir)
 */
import { useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { FileJson, Upload, AlertCircle } from "lucide-react";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from "../ui/dialog";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const auth = () => ({ Authorization: `Bearer ${localStorage.getItem("token")}` });

const EXAMPLE = [
  {
    id: 1,
    name: "Renk",
    required: true,
    attributeValues: [
      { id: 100, name: "Kırmızı" },
      { id: 101, name: "Mavi" },
    ],
  },
  {
    id: 2,
    name: "Beden",
    required: true,
    allowCustom: false,
    attributeValues: [
      { id: 200, name: "S" },
      { id: 201, name: "M" },
      { id: 202, name: "L" },
    ],
  },
];

export default function AttrCacheUploadDialog({ open, onClose, marketplace, mpCategoryId }) {
  const [mpCatId, setMpCatId] = useState(mpCategoryId || "");
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (open) {
      setMpCatId(mpCategoryId || "");
      setText("");
      setErr("");
    }
  }, [open, mpCategoryId]);

  const onFile = (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    const r = new FileReader();
    r.onload = () => setText(String(r.result || ""));
    r.readAsText(f);
  };

  const submit = async () => {
    setErr("");
    let attrs;
    try {
      attrs = JSON.parse(text);
    } catch (e) {
      setErr("Geçersiz JSON: " + e.message);
      return;
    }
    if (!Array.isArray(attrs)) {
      setErr("JSON bir dizi (array) olmalı. Örneğe bakın.");
      return;
    }
    if (!mpCatId) {
      setErr("Pazaryeri kategori ID'si zorunlu.");
      return;
    }
    setSaving(true);
    try {
      const r = await axios.post(
        `${API}/category-mapping/${marketplace}/attr-cache`,
        { marketplace_category_id: String(mpCatId), attributes: attrs },
        { headers: auth() }
      );
      toast.success(r.data?.message || "Cache yüklendi");
      onClose(true);
    } catch (e) {
      setErr(e.response?.data?.detail || e.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={() => onClose(false)}>
      <DialogContent className="max-w-2xl max-h-[90vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-base">
            <FileJson size={18} className="text-blue-500" />
            {marketplace} — Attribute Listesi Yükle (JSON)
          </DialogTitle>
        </DialogHeader>

        <div className="bg-blue-50 border border-blue-200 rounded-lg px-3 py-2 text-xs text-blue-900 flex items-start gap-2">
          <AlertCircle size={14} className="mt-0.5 shrink-0" />
          <div>
            <b>{marketplace}</b> için canlı API entegrasyonu henüz yok. Kendi pazaryeri panelinizden
            attribute listesini JSON olarak export edip buraya yapıştırın ya da dosya olarak yükleyin.
            Bu liste cache'e yazılır ve "Özellik Eşleştirme" modal'ında görünür.
          </div>
        </div>

        <div className="mt-3 space-y-2">
          <div>
            <label className="text-xs text-gray-600 font-medium">Pazaryeri Kategori ID</label>
            <input
              type="text"
              value={mpCatId}
              onChange={(e) => setMpCatId(e.target.value)}
              placeholder="örn. 12345"
              className="w-full border rounded px-3 py-1.5 text-sm mt-1"
              data-testid="attr-cache-cat-id"
            />
          </div>

          <div className="flex items-center justify-between">
            <label className="text-xs text-gray-600 font-medium">
              Attribute JSON (array)
            </label>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setText(JSON.stringify(EXAMPLE, null, 2))}
                className="text-xs text-blue-600 hover:underline"
              >
                Örneği Yükle
              </button>
              <label className="text-xs bg-gray-100 hover:bg-gray-200 px-2 py-1 rounded cursor-pointer">
                <Upload size={12} className="inline mr-1" /> Dosyadan Yükle
                <input type="file" accept=".json,application/json" className="hidden" onChange={onFile} />
              </label>
            </div>
          </div>

          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder='[{"id":1,"name":"Renk","required":true,"attributeValues":[{"id":100,"name":"Kırmızı"}]}]'
            rows={12}
            className="w-full border rounded px-3 py-2 font-mono text-xs"
            data-testid="attr-cache-textarea"
          />

          {err && (
            <div className="text-xs text-red-600 bg-red-50 border border-red-200 rounded px-3 py-2">
              {err}
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 pt-3 border-t">
          <button
            onClick={() => onClose(false)}
            className="px-4 py-2 border rounded text-sm hover:bg-gray-50"
          >
            İptal
          </button>
          <button
            onClick={submit}
            disabled={saving || !text.trim() || !mpCatId}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded text-sm hover:bg-blue-700 disabled:opacity-50"
            data-testid="attr-cache-submit"
          >
            {saving ? "Yükleniyor..." : "Cache'e Yükle"}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
