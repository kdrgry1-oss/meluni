/**
 * KATEGORİ SIRALAMA — ürünleri kategori sayfasındaki gibi görerek sürükle-bırak ile sırala.
 *
 * Kullanıcı isteği: aynı ürün birden çok kategoride yer alabiliyor; her kategoride AYRI
 * sıraya konabilmeli ve bu canlı canlı görülerek yapılabilmeli.
 *
 * - Kartlar vitrindeki ızgara düzeniyle (görsel + ad + fiyat) gösterilir.
 * - Sıra YALNIZ seçili kategori için kaydedilir (db.category_product_order).
 * - Tükenen ürünler vitrinde her zaman sona düşer; burada da soluk gösterilir.
 */
import { useState, useEffect, useCallback } from "react";
import axios from "axios";
import { toast } from "sonner";
import {
  DndContext, closestCenter, PointerSensor, KeyboardSensor,
  useSensor, useSensors,
} from "@dnd-kit/core";
import {
  SortableContext, arrayMove, useSortable, rectSortingStrategy,
  sortableKeyboardCoordinates,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { GripVertical, Save, RotateCcw, LayoutGrid, ExternalLink } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const auth = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } });
const tl = (n) => `${(Number(n) || 0).toLocaleString("tr-TR", { maximumFractionDigits: 2 })} TL`;

function Card({ p, index, total, onJump }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: p.id });
  const [pos, setPos] = useState("");
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.4 : 1,
    zIndex: isDragging ? 20 : undefined,
    // Sürükleme sırasında tarayıcının metin seçmesi / sayfayı kaydırması engellenir
    // (dokunmatik yüzeyde ve mobilde sürüklemeyi bozan en yaygın sebep).
    userSelect: "none",
    WebkitUserSelect: "none",
    touchAction: "none",
  };
  const jump = () => {
    const n = parseInt(pos, 10);
    setPos("");
    if (!Number.isNaN(n)) onJump(p.id, n);
  };
  const out = !p.stock || p.stock <= 0;
  return (
    <div ref={setNodeRef} style={style} {...attributes} {...listeners}
      data-testid={`catorder-card-${p.id}`}
      className={`relative bg-white border rounded-lg overflow-hidden cursor-grab active:cursor-grabbing
        ${isDragging ? "ring-2 ring-black shadow-lg" : "hover:border-gray-400"} ${out ? "opacity-60" : ""}`}>
      {/* Sıra numarası AYNI ZAMANDA giriş kutusu: 1 yazıp Enter → ürün 1. sıraya taşınır.
          Uzun mesafeli taşımalarda sürüklemeye mahkûm kalmamak için (kullanıcı geri bildirimi). */}
      <input
        value={pos === "" ? index + 1 : pos}
        onChange={(e) => setPos(e.target.value.replace(/[^0-9]/g, ""))}
        onKeyDown={(e) => {
          e.stopPropagation();            // kart klavye sürüklemesini tetiklemesin
          if (e.key === "Enter") { e.preventDefault(); jump(); e.target.blur(); }
        }}
        onBlur={jump}
        onPointerDown={(e) => e.stopPropagation()}
        onClick={(e) => { e.stopPropagation(); e.target.select(); }}
        title={"Sıra: " + (index + 1) + " / " + total + " — yeni sırayı yazıp Enter'a basın"}
        data-testid={"catorder-pos-" + p.id}
        className="absolute top-1.5 left-1.5 z-10 bg-black text-white text-[10px] font-bold
          rounded px-1 py-0.5 w-9 text-center tabular-nums outline-none focus:ring-2 focus:ring-blue-400"
      />
      <span className="absolute top-1.5 right-1.5 z-10 text-gray-400 bg-white/80 rounded p-0.5">
        <GripVertical size={13} />
      </span>
      <div className="aspect-[3/4] bg-gray-50">
        {p.image
          ? <img src={p.image} alt={p.name || ""} loading="lazy" draggable={false}
              className="w-full h-full object-cover" />
          : <div className="w-full h-full flex items-center justify-center text-[10px] text-gray-300">görsel yok</div>}
      </div>
      <div className="p-2">
        <p className="text-[11px] leading-tight text-gray-900 line-clamp-2 min-h-[28px]">{p.name || "—"}</p>
        <div className="mt-1 flex items-baseline gap-1.5">
          {Number(p.sale_price) > 0 && Number(p.sale_price) < Number(p.price) ? (
            <>
              <span className="text-[11px] text-gray-400 line-through">{tl(p.price)}</span>
              <span className="text-[11px] font-semibold text-red-600">{tl(p.sale_price)}</span>
            </>
          ) : <span className="text-[11px] font-semibold">{tl(p.price)}</span>}
        </div>
        {out && <p className="text-[10px] text-amber-600 mt-0.5">tükendi · vitrinde sonda</p>}
      </div>
    </div>
  );
}

export default function CategoryOrder() {
  const [cats, setCats] = useState([]);
  const [catId, setCatId] = useState("");
  const [items, setItems] = useState([]);
  const [initial, setInitial] = useState([]);
  const [info, setInfo] = useState(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  );

  useEffect(() => {
    axios.get(`${API}/categories`)
      .then(({ data }) => {
        const list = Array.isArray(data) ? data : [];
        const byParent = {};
        list.forEach((c) => { const k = c.parent_id || ""; (byParent[k] = byParent[k] || []).push(c); });
        const out = [];
        const walk = (pk, d) => {
          (byParent[pk] || [])
            .slice().sort((a, b) => String(a.name).localeCompare(String(b.name), "tr"))
            .forEach((c) => { out.push({ id: c.id, name: `${"— ".repeat(d)}${c.name}` }); walk(c.id, d + 1); });
        };
        walk("", 0);
        list.forEach((c) => { if (!out.find((o) => o.id === c.id)) out.push({ id: c.id, name: c.name }); });
        setCats(out);
      })
      .catch(() => toast.error("Kategoriler yüklenemedi"));
  }, []);

  const load = useCallback(async (id) => {
    if (!id) { setItems([]); setInfo(null); return; }
    setLoading(true);
    try {
      const { data } = await axios.get(`${API}/products/category-order/${id}`, auth());
      setItems(data.products || []);
      setInitial((data.products || []).map((p) => p.id));
      setInfo(data);
    } catch (e) {
      toast.error(e.response?.data?.detail || "Ürünler yüklenemedi");
      setItems([]); setInfo(null);
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { load(catId); }, [catId, load]);

  const onDragEnd = ({ active, over }) => {
    if (!over || active.id === over.id) return;
    setItems((cur) => {
      const from = cur.findIndex((x) => x.id === active.id);
      const to = cur.findIndex((x) => x.id === over.id);
      return from < 0 || to < 0 ? cur : arrayMove(cur, from, to);
    });
  };

  const dirty = items.map((p) => p.id).join(",") !== initial.join(",");

  // Sıra kutusundan taşıma: ürünü yazılan sıraya (1 = en başa) getirir.
  const jumpTo = (id, human) => {
    setItems((cur) => {
      const from = cur.findIndex((x) => x.id === id);
      if (from < 0) return cur;
      const to = Math.max(0, Math.min(cur.length - 1, (Number(human) || 1) - 1));
      return to === from ? cur : arrayMove(cur, from, to);
    });
  };

  const save = async () => {
    setSaving(true);
    try {
      await axios.post(`${API}/products/category-order/${catId}`,
        { ids: items.map((p) => p.id) }, auth());
      setInitial(items.map((p) => p.id));
      toast.success("Sıra kaydedildi — kategori sayfasında bu sırayla görünecek");
    } catch (e) {
      const st = e.response?.status ? " (HTTP " + e.response.status + ")" : "";
      toast.error((e.response?.data?.detail || "Sıra kaydedilemedi") + st);
    } finally { setSaving(false); }
  };

  const reset = async () => {
    if (!window.confirm("Bu kategorinin manuel sırası kaldırılsın mı? Vitrin varsayılan sıraya döner.")) return;
    setSaving(true);
    try {
      await axios.post(`${API}/products/category-order/${catId}`, { ids: [] }, auth());
      toast.success("Manuel sıra kaldırıldı");
      load(catId);
    } catch (e) {
      toast.error("Kaldırılamadı");
    } finally { setSaving(false); }
  };

  return (
    <div className="p-4 md:p-6" data-testid="category-order-page">
      <div className="flex items-center gap-2 mb-1">
        <LayoutGrid className="w-5 h-5" />
        <h1 className="text-xl font-bold">Kategori Sıralama</h1>
      </div>
      <p className="text-sm text-gray-500 mb-4">
        Ürünleri sürükleyerek kategori sayfasındaki sırayı belirleyin. Sıra <b>yalnız seçtiğiniz
        kategoride</b> geçerlidir; aynı ürün başka kategoride farklı sırada durabilir.
      </p>

      <div className="flex flex-wrap items-center gap-2 mb-4">
        <select value={catId} onChange={(e) => setCatId(e.target.value)}
          className="border rounded px-3 py-2 text-sm bg-white min-w-[240px]" data-testid="catorder-select">
          <option value="">— Kategori seçin —</option>
          {cats.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        {info?.category?.slug && (
          <a href={`/kategori/${info.category.slug}`} target="_blank" rel="noreferrer"
            className="inline-flex items-center gap-1 text-sm border rounded px-3 py-2 hover:border-black">
            <ExternalLink size={14} /> Sayfayı aç
          </a>
        )}
        <span className="flex-1" />
        {catId && info?.manual_count > 0 && (
          <button onClick={reset} disabled={saving}
            className="inline-flex items-center gap-1 text-sm border rounded px-3 py-2 text-gray-600 hover:border-black disabled:opacity-50">
            <RotateCcw size={14} /> Varsayılana dön
          </button>
        )}
        {/* Tuş YALNIZ kayıt sırasında veya kategori seçilmemişken kapalıdır. Eskiden
            "değişiklik yok" durumunda da kapanıyordu; kullanıcı sürüklediğini sanıp tuşu ölü
            bulunca sıkışıyordu. Artık her zaman basılabilir — ekranda görünen sırayı yazar. */}
        <button onClick={save} disabled={saving || !catId} data-testid="catorder-save"
          className="inline-flex items-center gap-1.5 px-4 py-2 bg-black text-white rounded text-sm disabled:opacity-40">
          <Save size={15} /> {saving ? "Kaydediliyor…" : dirty ? "Sırayı Kaydet *" : "Sırayı Kaydet"}
        </button>
      </div>

      {info?.truncated && (
        <div className="mb-3 text-xs bg-blue-50 border border-blue-200 text-blue-800 rounded px-3 py-2">
          Bu kategoride {info.total_all} aktif ürün var; ekranda ilk {items.length} tanesi
          gösteriliyor. Kaydettiğinizde yalnız görünen ürünlerin sırası yazılır, kalanlar
          vitrin varsayılan sırasıyla arkalarında listelenir.
        </div>
      )}
      {dirty && (
        <div className="mb-3 text-xs bg-amber-50 border border-amber-200 text-amber-800 rounded px-3 py-2">
          Sıra değişti — kaydetmeden sayfadan ayrılırsanız değişiklik kaybolur.
        </div>
      )}

      {!catId ? (
        <div className="text-sm text-gray-400 border rounded-lg p-10 text-center">
          Sıralamak istediğiniz kategoriyi seçin.
        </div>
      ) : loading ? (
        <div className="text-sm text-gray-500 py-8 text-center">Yükleniyor…</div>
      ) : items.length === 0 ? (
        <div className="text-sm text-gray-500 border rounded-lg p-10 text-center">Bu kategoride ürün yok.</div>
      ) : (
        <>
          <p className="text-xs text-gray-400 mb-2">
            {items.length} ürün · Kartları sürükleyip bırakın <b>veya</b> sol üstteki sıra
            numarasına yeni sırayı yazıp Enter'a basın (uzun mesafeli taşımalarda daha kolay).
            Tükenen ürünler vitrinde her zaman listenin sonunda gösterilir.
          </p>
          <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
            <SortableContext items={items.map((p) => p.id)} strategy={rectSortingStrategy}>
              <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3">
                {items.map((p, i) => (
                  <Card key={p.id} p={p} index={i} total={items.length} onJump={jumpTo} />
                ))}
              </div>
            </SortableContext>
          </DndContext>
        </>
      )}
    </div>
  );
}
