import { useState, useEffect } from "react";
import { Plus, Edit, Trash2, GripVertical, Image, Upload, X, Eye, EyeOff, ChevronUp, ChevronDown } from "lucide-react";
import axios from "axios";
import { toast } from "sonner";
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
} from '@dnd-kit/core';
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
// Origin'i dogru turet (API.replace('/api','') ilk //api'yi silip bozuk URL uretirdi).
const BACKEND_ORIGIN = String(process.env.REACT_APP_BACKEND_URL || "").replace(/\/+$/, "").replace(/\/api$/, "");

const BLOCK_TYPES = [
  { value: "countdown_bar", label: "Geri Sayım Barı (Üst Bar)", icon: "⏱️", description: "Sitenin EN ÜST'ünde, planlanabilir tarih/saatli countdown" },
  { value: "hero_slider", label: "Hero Slider", icon: "🎠", description: "Ana sayfa slider - Dönen görseller" },
  { value: "rotating_text", label: "Dönen Yazı", icon: "📢", description: "Üst banner'da dönen metin" },
  { value: "full_banner", label: "Tam Genişlik Banner", icon: "🖼️", description: "Tek görsel tam genişlik" },
  { value: "half_banners", label: "Yarı Yarıya Banner", icon: "◧", description: "İki görsel yan yana" },
  { value: "product_slider", label: "Ürün Slider", icon: "🛍️", description: "Yatay ürün listesi" },
  { value: "instashop", label: "InstaShop", icon: "📸", description: "Instagram tarzı görseller" },
  { value: "text_block", label: "Yazı Bloğu", icon: "📝", description: "Başlık ve açıklama" },
  { value: "video_banner", label: "Video Banner", icon: "🎬", description: "Video arka planlı banner" },
];

// Sortable Block Item Component
function SortableBlockItem({ block, onEdit, onDelete, onToggleActive, getBlockTypeInfo }) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: block.id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
    zIndex: isDragging ? 1000 : 1,
  };

  const typeInfo = getBlockTypeInfo(block.type);

  return (
    <div 
      ref={setNodeRef}
      style={style}
      className={`bg-white rounded-lg shadow-sm border-2 transition-all ${
        isDragging ? 'border-blue-500 shadow-lg' : 'border-transparent hover:border-gray-200'
      } ${!block.is_active ? 'opacity-60' : ''}`}
    >
      <div className="flex items-start p-4 gap-4">
        {/* Drag Handle */}
        <div 
          {...attributes} 
          {...listeners}
          className="pt-2 cursor-grab active:cursor-grabbing text-gray-400 hover:text-gray-600"
        >
          <GripVertical size={20} />
        </div>

        {/* Preview */}
        <div className="flex-shrink-0 w-40">
          {block.images?.[0] ? (
            /\.(mp4|webm|mov|m4v|ogg)(\?|$)/i.test(block.images[0]) ? (
              <div className="relative w-full h-20">
                <video src={block.images[0]} className="w-full h-20 object-cover rounded" muted playsInline preload="metadata" />
                <span className="absolute bottom-1 left-1 bg-black/70 text-white text-[9px] px-1 rounded">🎬</span>
              </div>
            ) : (
              <img
                src={block.images[0]}
                alt=""
                className="w-full h-20 object-cover rounded"
              />
            )
          ) : (
            <div className="w-full h-20 bg-gray-100 rounded flex items-center justify-center text-2xl">
              {typeInfo.icon || "📦"}
            </div>
          )}
        </div>

        {/* Info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <span className="text-xs bg-gray-100 px-2 py-0.5 rounded font-medium">
              {typeInfo.icon} {typeInfo.label}
            </span>
            <span className={`text-xs px-2 py-0.5 rounded ${
              block.is_active ? 'bg-green-100 text-green-700' : 'bg-orange-100 text-orange-600'
            }`}>
              {block.is_active ? 'Yayında' : 'Taslak'}
            </span>
            {block.show_desktop === false && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 font-medium" title="Masaüstünde gizli">
                🖥️ Gizli
              </span>
            )}
            {block.show_mobile === false && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 font-medium" title="Mobilde gizli">
                📱 Gizli
              </span>
            )}
          </div>
          <h3 className="font-medium truncate">{block.title || "Başlıksız"}</h3>
          <p className="text-sm text-gray-500">
            {block.type === "countdown_bar"
              ? (block.settings?.end_at
                  ? `⏱️ Bitiş: ${new Date(block.settings.end_at).toLocaleString("tr-TR")} ${block.settings?.start_at ? `· Başlangıç: ${new Date(block.settings.start_at).toLocaleString("tr-TR")}` : ""}`
                  : "⏱️ Tarih ayarlanmamış")
              : block.type === "product_slider"
              ? block.settings?.product_ids?.length
                ? `${block.settings.product_ids.length} ürün seçili`
                : `En yeni ürünler gösterilir`
              : block.type === "text_block"
              ? block.settings?.text
                ? block.settings.text.slice(0, 60) + (block.settings.text.length > 60 ? "…" : "")
                : "Metin girilmemiş"
              : block.type === "video_banner"
              ? block.settings?.video_url ? "Video eklendi ✓" : "Video eklenmemiş"
              : block.type === "rotating_text"
              ? `${block.settings?.texts?.length || 0} metin`
              : `${block.images?.length || 0} görsel`
            }
          </p>
          {block.links?.[0] && (
            <p className="text-xs text-gray-400 mt-1 truncate">
              → {block.links[0]}
            </p>
          )}
        </div>

        {/* Actions */}
        <div className="flex flex-col gap-1">
          <button 
            onClick={() => onToggleActive(block)}
            className={`p-2 rounded transition-colors ${
              block.is_active ? 'hover:bg-gray-100' : 'hover:bg-green-50 text-green-600'
            }`}
            title={block.is_active ? 'Taslağa Al' : 'Yayınla'}
          >
            {block.is_active ? <EyeOff size={16} /> : <Eye size={16} />}
          </button>
          <button 
            onClick={() => onEdit(block)}
            className="p-2 hover:bg-gray-100 rounded"
            title="Düzenle"
          >
            <Edit size={16} />
          </button>
          <button 
            onClick={() => onDelete(block.id)}
            className="p-2 hover:bg-red-50 rounded text-red-600"
            title="Sil"
          >
            <Trash2 size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}

export default function PageDesign() {
  const [blocks, setBlocks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingBlock, setEditingBlock] = useState(null);
  const [uploading, setUploading] = useState(false);
  // Ürün slider "kategori" kaynağı için kategori listesi (bir kez çekilir)
  const [sliderCategories, setSliderCategories] = useState([]);
  useEffect(() => {
    axios.get(`${API}/categories`)
      .then((r) => {
        const rows = Array.isArray(r.data) ? r.data : (r.data?.categories || []);
        setSliderCategories(rows.map((c) => ({ id: String(c.id), name: c.name || c.title || c.slug })));
      })
      .catch(() => setSliderCategories([]));
  }, []);
  const [hasChanges, setHasChanges] = useState(false);
  const [previewMode, setPreviewMode] = useState("mobile"); // "mobile" | "desktop"
  
  const refreshPreview = () => {
    const iframe = document.getElementById('preview-frame');
    if (iframe) iframe.src = iframe.src;
  };
  
  const [formData, setFormData] = useState({
    type: "hero_slider",
    title: "",
    images: [],
    links: [],
    settings: { texts: [""] },
    sort_order: 0,
    is_active: true,
    page: "home"
  });

  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 8,
      },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  );

  useEffect(() => {
    fetchBlocks();
  }, []);

  const fetchBlocks = async () => {
    setLoading(true);
    try {
      const token = localStorage.getItem('token');
      const res = await axios.get(`${API}/page-blocks?page=home`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      // Sort by sort_order
      const sorted = (res.data || []).sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0));
      setBlocks(sorted);
    } catch (err) {
      // Default blocks if API fails
      setBlocks([
        {
          id: "hero",
          type: "hero_slider",
          title: "Hero Slider",
          images: [
            "https://cdn.facette.com.tr/pagedesign/en-yeniler-dc2e-1920.webp",
            "https://cdn.facette.com.tr/pagedesign/ae79c961-ba0b-49e3-b274-2c6cc78ab700-1920.webp"
          ],
          links: ["/kategori/en-yeniler", "/kategori/sale"],
          is_active: true,
          sort_order: 1
        },
        {
          id: "full",
          type: "full_banner",
          title: "Bloom Together",
          images: ["https://cdn.facette.com.tr/pagedesign/title-cb23757c-6-1920.webp"],
          links: ["/kategori/en-yeniler"],
          is_active: true,
          sort_order: 2
        },
        {
          id: "half",
          type: "half_banners",
          title: "Kategori Bannerları",
          images: [
            "https://cdn.facette.com.tr/pagedesign/title-65777bd3-0-1920.webp",
            "https://cdn.facette.com.tr/pagedesign/title-7b3e27f9-5-1920.webp"
          ],
          links: ["/kategori/gomlek", "/kategori/aksesuar"],
          is_active: true,
          sort_order: 3
        }
      ]);
    } finally {
      setLoading(false);
    }
  };

  const handleDragEnd = (event) => {
    const { active, over } = event;

    if (active.id !== over?.id) {
      setBlocks((items) => {
        const oldIndex = items.findIndex((item) => item.id === active.id);
        const newIndex = items.findIndex((item) => item.id === over.id);
        const newItems = arrayMove(items, oldIndex, newIndex);
        // Update sort_order
        return newItems.map((item, index) => ({ ...item, sort_order: index + 1 }));
      });
      setHasChanges(true);
    }
  };

  const handleSaveOrder = async () => {
    setSaving(true);
    try {
      const token = localStorage.getItem('token');
      const headers = { Authorization: `Bearer ${token}` };
      // Bulk reorder — tek API call
      await axios.post(`${API}/page-blocks/reorder`,
        { ids: blocks.map(b => b.id) },
        { headers }
      );
      toast.success("Sıralama kaydedildi");
      setHasChanges(false);
      refreshPreview();
    } catch (err) {
      toast.error("Kaydetme başarısız");
    } finally {
      setSaving(false);
    }
  };

  // Büyük dosyaları (DSLR çıkışı 10-25MB) yüklemeden önce tarayıcıda küçült:
  // uzun kenar 2400px, JPEG 0.86 — hem limit aşımını hem yavaş yüklemeyi önler.
  // HEIC gibi canvas'ın decode edemediği formatlar olduğu gibi gönderilir.
  const shrinkImageFile = (file) =>
    new Promise((resolve) => {
      if (!file.type.startsWith("image/") || file.type === "image/gif") return resolve(file);
      // DİKKAT: 'Image' burada lucide-react ikonu (new Image() → "is not a constructor").
      // DOM görsel nesnesi için window.Image kullanılır.
      const img = new window.Image();
      const objUrl = URL.createObjectURL(file);
      img.onload = () => {
        URL.revokeObjectURL(objUrl);
        const MAXW = 2400;
        const scale = Math.min(1, MAXW / Math.max(img.naturalWidth, img.naturalHeight));
        if (scale >= 1 && file.size < 4 * 1024 * 1024) return resolve(file); // zaten küçük
        const canvas = document.createElement("canvas");
        canvas.width = Math.round(img.naturalWidth * scale);
        canvas.height = Math.round(img.naturalHeight * scale);
        canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
        canvas.toBlob(
          (blob) => {
            if (!blob) return resolve(file);
            resolve(new File([blob], (file.name || "gorsel").replace(/\.[^.]+$/, "") + ".jpg", { type: "image/jpeg" }));
          },
          "image/jpeg",
          0.86
        );
      };
      img.onerror = () => { URL.revokeObjectURL(objUrl); resolve(file); }; // HEIC vb. → olduğu gibi
      img.src = objUrl;
    });

  const handleImageUpload = async (e, index = null) => {
    const file = e.target.files?.[0];
    if (!file) return;
    await uploadMediaFile(file, index);
    try { e.target.value = ""; } catch (_) { /* aynı dosya tekrar seçilebilsin */ }
  };

  // Görsel VEYA video yükler. Video ise optimize edilmeden Cloudflare R2/CDN'e gider
  // ve aynı images[] dizisine eklenir → sürükleyerek sıralama ikisi için de çalışır.
  const uploadMediaFile = async (rawFile, index = null) => {
    if (!rawFile) return;
    const isVideo = (rawFile.type || "").startsWith("video/");
    setUploading(true);
    try {
      const token = localStorage.getItem('token');
      if (isVideo) {
        const fd = new FormData();
        fd.append('file', rawFile);
        const res = await axios.post(`${API}/upload/video`, fd, {
          headers: { Authorization: `Bearer ${token}` },
          timeout: 300000, // video büyük olabilir
        });
        if (res.data.url || res.data.path) {
          const raw = res.data.url || `/api/upload/files/${res.data.path}`;
          const url = raw.startsWith('http') ? raw : `${BACKEND_ORIGIN}${raw}`;
          const newImages = [...formData.images];
          const newLinks = [...formData.links];
          if (index !== null) {
            newImages[index] = url;
          } else {
            newImages.push(url);
            newLinks.push("/");
          }
          setFormData({ ...formData, images: newImages, links: newLinks });
          toast.success("Video yüklendi");
        }
        return;
      }

      let file = await shrinkImageFile(rawFile);
      // Görselin gerçek piksel boyutunu client-side oku — kaydedilince storefront'ta
      // (HeroSlider/FullBanner) doğru en-boy oranında, kırpılmadan gösterilsin.
      const dims = await new Promise((resolve) => {
        const probe = new window.Image();
        const objUrl = URL.createObjectURL(file);
        probe.onload = () => { resolve([probe.naturalWidth, probe.naturalHeight]); URL.revokeObjectURL(objUrl); };
        probe.onerror = () => { resolve(null); URL.revokeObjectURL(objUrl); };
        probe.src = objUrl;
      });

      const fd = new FormData();
      fd.append('file', file);
      const res = await axios.post(`${API}/upload/image`, fd, {
        headers: { Authorization: `Bearer ${token}` }, // Content-Type + boundary'yi tarayıcı koyar
        timeout: 90000,
      });
      
      if (res.data.url || res.data.path) {
        const raw = res.data.url || `/api/upload/files/${res.data.path}`;
        const url = raw.startsWith('http') ? raw : `${BACKEND_ORIGIN}${raw}`;
        const newImages = [...formData.images];
        const newLinks = [...formData.links];
        const targetIndex = index !== null ? index : newImages.length;
        
        if (index !== null) {
          newImages[index] = url;
        } else {
          newImages.push(url);
          newLinks.push("/");
        }

        const newSettings = { ...formData.settings };
        if (dims) {
          const imgDims = Array.isArray(newSettings.img_dims) ? [...newSettings.img_dims] : [];
          imgDims[targetIndex] = dims;
          newSettings.img_dims = imgDims;
          if (targetIndex === 0) {
            newSettings.img_width = dims[0];
            newSettings.img_height = dims[1];
          }
        }
        
        setFormData({ ...formData, images: newImages, links: newLinks, settings: newSettings });
        toast.success("Görsel yüklendi");
      }
    } catch (err) {
      const detail = err?.response?.data?.detail;
      const code = err?.response?.status || err?.code || err?.message || "";
      toast.error(detail ? `Görsel yüklenemedi: ${detail}` : `Görsel yüklenemedi (${code || "ağ hatası"})`);
      console.error("[upload]", err);
    } finally {
      setUploading(false);
    }
  };

  const removeImage = (index) => {
    const newImages = [...formData.images];
    const newLinks = [...formData.links];
    newImages.splice(index, 1);
    newLinks.splice(index, 1);
    const newSettings = { ...formData.settings };
    if (Array.isArray(newSettings.img_dims)) {
      const imgDims = [...newSettings.img_dims];
      imgDims.splice(index, 1);
      newSettings.img_dims = imgDims;
    }
    setFormData({ ...formData, images: newImages, links: newLinks, settings: newSettings });
  };

  // Slaytları SÜRÜKLEYEREK yeniden sırala (görsel + video birlikte). images/links/img_dims
  // paralel taşınır ki her slaytın linki ve en-boy oranı doğru kalsın.
  const [dragIdx, setDragIdx] = useState(null);
  const isVideoUrl = (u) => typeof u === "string" && /\.(mp4|webm|mov|m4v|ogg)(\?|$)/i.test(u);
  const moveSlide = (from, to) => {
    if (from == null || to == null || from === to) return;
    const imgs = [...formData.images];
    const lnks = formData.links && formData.links.length ? [...formData.links] : imgs.map(() => "/");
    while (lnks.length < imgs.length) lnks.push("/");
    const dims = Array.isArray(formData.settings?.img_dims) ? [...formData.settings.img_dims] : null;
    const [mi] = imgs.splice(from, 1); imgs.splice(to, 0, mi);
    const [ml] = lnks.splice(from, 1); lnks.splice(to, 0, ml);
    const newSettings = { ...formData.settings };
    if (dims) { const [md] = dims.splice(from, 1); dims.splice(to, 0, md); newSettings.img_dims = dims; }
    setFormData({ ...formData, images: imgs, links: lnks, settings: newSettings });
  };

  const [productSearch, setProductSearch] = useState("");
  const [searchResults, setSearchResults] = useState([]);
  const [searchingProducts, setSearchingProducts] = useState(false);
  const [selectedProductDetails, setSelectedProductDetails] = useState([]);

  // Load details for already selected products when editing
  useEffect(() => {
    if (modalOpen && editingBlock && editingBlock.type === "product_slider" && editingBlock.settings?.product_ids?.length > 0) {
      loadSelectedProducts(editingBlock.settings.product_ids);
    } else if (modalOpen && !editingBlock) {
      setSelectedProductDetails([]);
    }
  }, [modalOpen, editingBlock]);

  const loadSelectedProducts = async (ids) => {
    try {
      const token = localStorage.getItem('token');
      // A quick parallel fetch for each id (since we don't have a bulk endpoint in admin by default)
      const details = [];
      for (const id of ids) {
        const res = await axios.get(`${API}/products/${id}`, {
          headers: { Authorization: `Bearer ${token}` }
        });
        if (res.data) details.push(res.data);
      }
      setSelectedProductDetails(details);
    } catch (err) {
      console.error(err);
    }
  };

  const handleProductSearch = async (e) => {
    e.preventDefault();
    if (!productSearch.trim()) return;
    setSearchingProducts(true);
    try {
      const token = localStorage.getItem('token');
      const res = await axios.get(`${API}/products?search=${productSearch}&limit=10`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      setSearchResults(res.data.products || []);
    } catch (err) {
      toast.error("Arama yapılamadı");
    } finally {
      setSearchingProducts(false);
    }
  };

  const addProductToBlock = (product) => {
    const currentIds = formData.settings?.product_ids || [];
    if (currentIds.includes(product._id)) {
      return toast.error("Bu ürün zaten ekli");
    }
    const newIds = [...currentIds, product._id];
    setFormData({ 
      ...formData, 
      settings: { ...formData.settings, product_ids: newIds } 
    });
    setSelectedProductDetails([...selectedProductDetails, product]);
  };

  const removeProductFromBlock = (index) => {
    const currentIds = [...(formData.settings?.product_ids || [])];
    currentIds.splice(index, 1);
    setFormData({ 
      ...formData, 
      settings: { ...formData.settings, product_ids: currentIds } 
    });
    const newDetails = [...selectedProductDetails];
    newDetails.splice(index, 1);
    setSelectedProductDetails(newDetails);
  };


  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      const token = localStorage.getItem('token');
      const headers = { Authorization: `Bearer ${token}` };
      
      const payload = {
        ...formData,
        sort_order: editingBlock ? formData.sort_order : blocks.length + 1
      };
      
      if (editingBlock) {
        await axios.put(`${API}/page-blocks/${editingBlock.id}`, payload, { headers });
        toast.success("Blok güncellendi");
      } else {
        await axios.post(`${API}/page-blocks`, payload, { headers });
        toast.success("Blok eklendi");
      }
      setModalOpen(false);
      resetForm();
      fetchBlocks();
      refreshPreview();
    } catch (err) {
      toast.error("İşlem başarısız");
    }
  };

  const handleDelete = async (id) => {
    if (!await window.appConfirm("Bloğu silmek istediğinize emin misiniz?")) return;
    try {
      const token = localStorage.getItem('token');
      await axios.delete(`${API}/page-blocks/${id}`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      toast.success("Blok silindi");
      fetchBlocks();
      refreshPreview();
    } catch (err) {
      toast.error("Silme başarısız");
    }
  };

  const handleToggleActive = async (block) => {
    try {
      const token = localStorage.getItem('token');
      await axios.put(`${API}/page-blocks/${block.id}`, {
        ...block,
        is_active: !block.is_active
      }, { headers: { Authorization: `Bearer ${token}` } });
      
      setBlocks(blocks.map(b => 
        b.id === block.id ? { ...b, is_active: !b.is_active } : b
      ));
      toast.success(block.is_active ? "Blok taslağa alındı" : "Blok yayınlandı");
      refreshPreview();
    } catch (err) {
      toast.error("İşlem başarısız");
    }
  };

  const moveBlock = (index, direction) => {
    const newIndex = direction === 'up' ? index - 1 : index + 1;
    if (newIndex < 0 || newIndex >= blocks.length) return;
    
    const newBlocks = arrayMove(blocks, index, newIndex).map((item, i) => ({
      ...item,
      sort_order: i + 1
    }));
    setBlocks(newBlocks);
    setHasChanges(true);
  };

  const openEditModal = (block) => {
    setEditingBlock(block);
    setFormData({
      type: block.type,
      title: block.title || "",
      images: block.images || [],
      links: block.links || [],
      settings: block.settings || {},
      sort_order: block.sort_order || 0,
      is_active: block.is_active ?? true,
      show_desktop: block.show_desktop !== false,
      show_mobile: block.show_mobile !== false,
      page: block.page || "home"
    });
    setModalOpen(true);
  };

  const resetForm = () => {
    setEditingBlock(null);
    setFormData({
      type: "hero_slider",
      title: "",
      images: [],
      links: [],
      settings: { texts: [""] },
      sort_order: 0,
      is_active: true,
      show_desktop: true,
      show_mobile: true,
      page: "home"
    });
    setProductSearch("");
    setSearchResults([]);
    setSelectedProductDetails([]);
  };

  const getBlockTypeInfo = (type) => {
    return BLOCK_TYPES.find(t => t.value === type) || { label: type, icon: "📦" };
  };

  // text_block'a görsel eklenebilir (görsel + yazı kompozisyonu); rotating_text ve
  // countdown salt metin barları olduğundan görsel alanı almaz.
  const needsImages = ["hero_slider", "full_banner", "half_banners", "instashop", "video_banner", "text_block"].includes(formData.type);


  return (
    <div data-testid="page-design" className="flex flex-col lg:flex-row gap-8 h-screen pb-20">
      {/* Sol Panel: Yönetim */}
      <div className="flex-1 overflow-y-auto pr-4">
        <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">Sayfa Tasarımı</h1>
          <p className="text-sm text-gray-500 mt-1">Ana sayfa blokları - Sürükle bırak ile sırala</p>
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          {hasChanges && (
            <button 
              onClick={handleSaveOrder}
              disabled={saving}
              className="flex items-center gap-2 bg-green-600 text-white px-4 py-2 rounded hover:bg-green-700 disabled:opacity-50"
              data-testid="save-order-btn"
            >
              {saving ? "Kaydediliyor..." : "Sıralamayı Kaydet"}
            </button>
          )}
          <button
            onClick={async () => {
              if (blocks.length > 0 && !window.confirm("Anasayfada zaten bloklar var. Bunları silip varsayılan tasarımla değiştirmek istiyor musunuz?")) return;
              try {
                const token = localStorage.getItem('token');
                const res = await axios.post(
                  `${API}/page-blocks/seed-default-home?overwrite=${blocks.length > 0}`,
                  {},
                  { headers: { Authorization: `Bearer ${token}` } }
                );
                if (res.data?.success) {
                  toast.success(res.data.message || "Varsayılan anasayfa yüklendi");
                  fetchBlocks();
                } else {
                  toast.error(res.data?.message || "İşlem başarısız");
                }
              } catch (e) {
                toast.error(e.response?.data?.detail || "Hata oluştu");
              }
            }}
            className="flex items-center gap-2 bg-stone-800 text-white px-4 py-2 rounded hover:bg-black"
            data-testid="seed-default-home-btn"
          >
            <Image size={16} /> Varsayılanı Yükle
          </button>
          <button 
            onClick={() => { resetForm(); setModalOpen(true); }}
            className="flex items-center gap-2 bg-black text-white px-4 py-2 rounded hover:bg-gray-800"
          >
            <Plus size={18} />
            Yeni Blok
          </button>
        </div>
      </div>

      {/* Info Box */}
      <div className="bg-gradient-to-r from-blue-50 to-indigo-50 border border-blue-200 rounded-lg p-4 mb-6">
        <div className="flex items-start gap-3">
          <div className="text-2xl">💡</div>
          <div>
            <p className="text-sm font-medium text-blue-900">Sürükle & Bırak ile Düzenle</p>
            <p className="text-xs text-blue-700 mt-1">
              Blokları sol taraftaki tutma noktasından sürükleyerek sıralayabilirsiniz. 
              Değişikliklerinizi kaydetmek için yeşil &quot;Sıralamayı Kaydet&quot; butonuna tıklayın.
            </p>
          </div>
        </div>
      </div>

      {/* Blocks List with Drag & Drop */}
      <DndContext 
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragEnd={handleDragEnd}
      >
        <SortableContext 
          items={blocks.map(b => b.id)}
          strategy={verticalListSortingStrategy}
        >
          <div className="space-y-3">
            {loading ? (
              <div className="text-center py-8">Yükleniyor...</div>
            ) : blocks.length === 0 ? (
              <div className="text-center py-12 bg-gray-50 rounded-lg border-2 border-dashed border-gray-200">
                <div className="text-4xl mb-3">📦</div>
                <p className="text-gray-500 mb-4">Henüz blok eklenmemiş</p>
                <button 
                  onClick={() => { resetForm(); setModalOpen(true); }}
                  className="inline-flex items-center gap-2 bg-black text-white px-4 py-2 rounded hover:bg-gray-800"
                >
                  <Plus size={16} /> İlk Bloğu Ekle
                </button>
              </div>
            ) : (
              blocks.map((block, index) => (
                <SortableBlockItem
                  key={block.id}
                  block={block}
                  onEdit={openEditModal}
                  onDelete={handleDelete}
                  onToggleActive={handleToggleActive}
                  getBlockTypeInfo={getBlockTypeInfo}
                />
              ))
            )}
          </div>
        </SortableContext>
      </DndContext>

      {/* Block Types Legend */}
      {blocks.length > 0 && (
        <div className="mt-8 p-4 bg-gray-50 rounded-lg">
          <h3 className="text-sm font-medium mb-3">Blok Tipleri</h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            {BLOCK_TYPES.map(type => (
              <div key={type.value} className="flex items-center gap-2 text-xs text-gray-600">
                <span>{type.icon}</span>
                <span>{type.label}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      </div>

      {/* Sağ Panel: Canlı Önizleme */}
      <div 
        className={`hidden lg:flex flex-col shrink-0 border-[6px] border-gray-900 bg-gray-100 overflow-hidden shadow-2xl relative mb-8 transition-all duration-300 ${
          previewMode === "mobile" 
            ? "w-[375px] xl:w-[414px] rounded-[2.5rem]" 
            : "flex-1 rounded-xl max-w-4xl"
        }`}
      >
        <div className="bg-gray-900 text-white py-2 px-6 flex justify-between items-center text-xs font-medium">
          <div className="flex items-center gap-4">
            <span>Canlı Önizleme</span>
            <div className="flex bg-gray-800 rounded p-1">
              <button 
                onClick={() => setPreviewMode("mobile")}
                className={`px-3 py-1 rounded transition-colors ${previewMode === "mobile" ? "bg-gray-700 text-white" : "text-gray-400 hover:text-white"}`}
              >
                Mobil
              </button>
              <button 
                onClick={() => setPreviewMode("desktop")}
                className={`px-3 py-1 rounded transition-colors ${previewMode === "desktop" ? "bg-gray-700 text-white" : "text-gray-400 hover:text-white"}`}
              >
                Masaüstü
              </button>
            </div>
          </div>
          <button 
            type="button"
            onClick={refreshPreview} 
            className="text-gray-300 hover:text-white transition-colors flex items-center gap-1"
          >
            Yenile
          </button>
        </div>
        <div className="flex-1 w-full bg-white relative">
          <iframe
            id="preview-frame"
            src="/?preview=true"
            title="Canlı Önizleme"
            className="w-full h-full border-0 absolute inset-0 bg-white"
          />
        </div>
      </div>

      {/* Block Modal */}
      <Dialog open={modalOpen} onOpenChange={setModalOpen}>
        <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editingBlock ? "Blok Düzenle" : "Yeni Blok Ekle"}</DialogTitle>
          </DialogHeader>

          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Block Type Selection */}
            <div>
              <label className="block text-sm font-medium mb-2">Blok Tipi</label>
              <div className="grid grid-cols-2 gap-2">
                {BLOCK_TYPES.map(type => (
                  <button
                    key={type.value}
                    type="button"
                    onClick={() => setFormData({ ...formData, type: type.value })}
                    className={`p-3 border rounded-lg text-left transition-all ${
                      formData.type === type.value 
                        ? 'border-black bg-gray-50 ring-1 ring-black' 
                        : 'border-gray-200 hover:border-gray-400'
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      <span className="text-xl">{type.icon}</span>
                      <div>
                        <p className="text-sm font-medium">{type.label}</p>
                        <p className="text-xs text-gray-500">{type.description}</p>
                      </div>
                    </div>
                  </button>
                ))}
              </div>
            </div>

            {/* Title */}
            <div>
              <label className="block text-sm font-medium mb-1">Başlık</label>
              <input
                type="text"
                value={formData.title}
                onChange={(e) => setFormData({ ...formData, title: e.target.value })}
                placeholder="Blok başlığı (opsiyonel)"
                className="w-full border px-3 py-2 rounded"
              />
            </div>

            {/* Active Toggle */}
            <div className="flex items-center gap-3">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={formData.is_active}
                  onChange={(e) => setFormData({ ...formData, is_active: e.target.checked })}
                  className="w-4 h-4"
                />
                <span className="text-sm">Yayında (Ana sayfada müşterilere göster)</span>
              </label>
              <p className="text-xs text-gray-500 ml-6 block w-full">- Seçilmezse &quot;Taslak&quot; olur, sadece önizlemede görünür.</p>
            </div>

            {/* Visibility Toggles (Mobile / Desktop) */}
            <div className="border border-gray-200 rounded-lg p-3 bg-gray-50">
              <p className="text-xs font-semibold uppercase tracking-wider text-gray-600 mb-2">Cihaz Görünürlüğü</p>
              <div className="flex items-center gap-6 flex-wrap">
                <label className="flex items-center gap-2 cursor-pointer" data-testid="block-show-desktop">
                  <input
                    type="checkbox"
                    checked={formData.show_desktop !== false}
                    onChange={(e) => setFormData({ ...formData, show_desktop: e.target.checked })}
                    className="w-4 h-4"
                  />
                  <span className="text-sm">🖥️ Masaüstünde Göster</span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer" data-testid="block-show-mobile">
                  <input
                    type="checkbox"
                    checked={formData.show_mobile !== false}
                    onChange={(e) => setFormData({ ...formData, show_mobile: e.target.checked })}
                    className="w-4 h-4"
                  />
                  <span className="text-sm">📱 Mobilde Göster</span>
                </label>
              </div>
              <p className="text-[11px] text-gray-500 mt-2">İkisi de seçili değilse blok hiçbir cihazda görünmez (etkin olarak gizlenir).</p>
            </div>

            {/* Dynamic Block Settings */}
            
            {needsImages && (
              <div>
                {formData.type === "instashop" && (
                  <div className="mb-4 bg-gradient-to-br from-fuchsia-50 to-purple-50 border border-fuchsia-200 rounded-lg p-3.5">
                    <p className="text-sm font-bold text-fuchsia-800 mb-1">📸 Instagram gönderilerini seçmek + ürün eklemek için</p>
                    <p className="text-[12px] text-fuchsia-900/80 leading-relaxed mb-2">
                      Anasayfadaki <b>SHOP THE LOOK</b> bölümü, artık <b>Instagram Akışı</b> sayfasından yönetilir:
                      oradan gönderileri çekersin (kendi + etiketli), hangileri görünsün seçersin ve her gönderiye
                      <b> ürün bağlarsın</b> (müşteri tıklayınca sepete ekler). Bağlı feed varsa <b>otomatik</b> anasayfaya gelir.
                    </p>
                    <a href="/admin/instagram"
                      className="inline-flex items-center gap-1.5 bg-fuchsia-600 hover:bg-fuchsia-700 text-white text-xs font-semibold px-3 py-2 rounded-lg">
                      Instagram Akışı sayfasına git →
                    </a>
                    <p className="text-[11px] text-fuchsia-900/60 mt-2">
                      Aşağıdaki görseller yalnızca <b>Instagram bağlı değilken</b> (feed boşsa) yedek olarak gösterilir.
                    </p>
                  </div>
                )}
                {formData.type === "hero_slider" && (
                  <div className="mb-4">
                    <label className="block text-sm font-medium mb-1">Slider Stili</label>
                    <select
                      value={formData.settings?.hero_style || "klasik"}
                      onChange={(e) => setFormData({ ...formData, settings: { ...formData.settings, hero_style: e.target.value } })}
                      className="w-full border px-3 py-2 rounded text-sm"
                    >
                      <option value="klasik">Klasik — yatay geçiş (fade + oklar)</option>
                      <option value="dikey">Dikey Editorial (Zara) — tam ekran, dikey kaydırma</option>
                    </select>
                    <p className="text-[11px] text-gray-500 mt-1">
                      Dikey Editorial'de her slayt ekranı doldurur; sayfa kaydırıldıkça slaytlar birbiri ardına gelir. Her slayta üst yazı + başlık girebilirsin (aşağıda).
                    </p>
                  </div>
                )}
                <label className="block text-sm font-medium mb-2">Görseller</label>
                <p className="text-[11px] text-gray-500 mb-2">Slaytları <b>sürükleyerek</b> sıralayabilirsin. Görsel veya <b>video</b> (mp4/webm) yükleyebilir, kutuya <b>sürükleyip bırakarak</b> da ekleyebilirsin.</p>
                <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
                  {formData.images.map((img, index) => (
                    <div
                      key={index}
                      className={`relative group cursor-move ${dragIdx === index ? "opacity-40" : ""}`}
                      draggable
                      onDragStart={() => setDragIdx(index)}
                      onDragOver={(e) => e.preventDefault()}
                      onDrop={(e) => { e.preventDefault(); moveSlide(dragIdx, index); setDragIdx(null); }}
                      onDragEnd={() => setDragIdx(null)}
                    >
                      {isVideoUrl(img) ? (
                        <div className="relative">
                          <video src={img} className="w-full aspect-video object-cover rounded border" muted playsInline preload="metadata" />
                          <span className="absolute bottom-1 left-1 bg-black/70 text-white text-[9px] px-1.5 py-0.5 rounded flex items-center gap-1">🎬 Video</span>
                        </div>
                      ) : (
                        <img src={img} alt="" className="w-full aspect-video object-cover rounded border" />
                      )}
                      <span className="absolute top-1 left-1 bg-black/60 text-white text-[10px] px-1.5 py-0.5 rounded">{index + 1}</span>
                      <button
                        type="button"
                        onClick={() => removeImage(index)}
                        className="absolute top-1 right-1 w-6 h-6 bg-red-500 text-white rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                      >
                        <X size={14} />
                      </button>
                      <input
                        type="text"
                        value={formData.links[index] || ""}
                        onChange={(e) => {
                          const newLinks = [...formData.links];
                          newLinks[index] = e.target.value;
                          setFormData({ ...formData, links: newLinks });
                        }}
                        placeholder="/kategori/..."
                        className="w-full text-xs border px-2 py-1.5 rounded mt-2"
                      />
                      {formData.type === "hero_slider" && formData.settings?.hero_style === "dikey" && (() => {
                        const caps = Array.isArray(formData.settings?.captions) ? formData.settings.captions : [];
                        const cap = caps[index] || {};
                        const setCap = (patch) => {
                          const next = [...(Array.isArray(formData.settings?.captions) ? formData.settings.captions : [])];
                          while (next.length <= index) next.push({});
                          next[index] = { ...next[index], ...patch };
                          setFormData({ ...formData, settings: { ...formData.settings, captions: next } });
                        };
                        return (
                          <div className="mt-1.5 space-y-1">
                            <input type="text" value={cap.eyebrow || ""} onChange={(e) => setCap({ eyebrow: e.target.value })}
                              placeholder="Üst yazı (ör. YENİ SEZON)" className="w-full text-[11px] border px-2 py-1 rounded" />
                            <input type="text" value={cap.title || ""} onChange={(e) => setCap({ title: e.target.value })}
                              placeholder="Başlık (ör. Deniz Kıyısı)" className="w-full text-[11px] border px-2 py-1 rounded" />
                            <input type="text" value={cap.cta || ""} onChange={(e) => setCap({ cta: e.target.value })}
                              placeholder="Buton yazısı (varsayılan: Keşfet)" className="w-full text-[11px] border px-2 py-1 rounded" />
                          </div>
                        );
                      })()}
                    </div>
                  ))}

                  {/* Upload — görsel VEYA video; sürükle-bırak destekli */}
                  <label
                    className="aspect-video border-2 border-dashed border-gray-300 flex flex-col items-center justify-center cursor-pointer hover:border-black hover:bg-gray-50 rounded transition-colors"
                    onDragOver={(e) => { e.preventDefault(); }}
                    onDrop={(e) => {
                      e.preventDefault();
                      const f = e.dataTransfer?.files?.[0];
                      if (f) uploadMediaFile(f);
                    }}
                  >
                    <input
                      type="file"
                      accept="image/*,video/*"
                      onChange={(e) => handleImageUpload(e)}
                      className="hidden"
                    />
                    {uploading ? (
                      <div className="text-center">
                        <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-black mx-auto mb-2"></div>
                        <span className="text-xs text-gray-500">Yükleniyor...</span>
                      </div>
                    ) : (
                      <>
                        <Upload size={24} className="text-gray-400 mb-1" />
                        <span className="text-xs text-gray-500">Görsel / Video Ekle</span>
                        <span className="text-[10px] text-gray-400 mt-0.5">sürükle-bırak</span>
                      </>
                    )}
                  </label>
                </div>
              </div>
            )}

            {formData.type === "text_block" && (
              <div>
                <label className="block text-sm font-medium mb-1">Açıklama Metni</label>
                <textarea
                  value={formData.settings?.text || ""}
                  onChange={(e) => setFormData({ ...formData, settings: { ...formData.settings, text: e.target.value } })}
                  placeholder="Yazı bloğu içeriği..."
                  className="w-full border px-3 py-2 rounded h-24"
                />
                <div className="mt-2 text-xs text-gray-500">Buton Linki eklemek isterseniz Görseller altındaki link yapısını veya doğrudan buraya buton şeklinde eklemeyi desteklemediğimiz için, text bloklarında ilk link URL&apos;i buton linki olarak kullanılır.</div>
                <input
                  type="text"
                  value={formData.links[0] || ""}
                  onChange={(e) => {
                    const newLinks = [...formData.links];
                    newLinks[0] = e.target.value;
                    setFormData({ ...formData, links: newLinks });
                  }}
                  placeholder="Buton Linki URL (örn: /iletisim)"
                  className="w-full border px-3 py-2 rounded mt-2 text-sm"
                />
              </div>
            )}

            {formData.type === "video_banner" && (
              <div>
                <label className="block text-sm font-medium mb-1">Video URL (m3u8 veya mp4)</label>
                <input
                  type="text"
                  value={formData.settings?.video_url || ""}
                  onChange={(e) => setFormData({ ...formData, settings: { ...formData.settings, video_url: e.target.value } })}
                  placeholder="https://.../video.mp4"
                  className="w-full border px-3 py-2 rounded text-sm"
                />
              </div>
            )}

            {/* COUNTDOWN BAR — sitenin en üstünde admin tarafından planlanabilen geri sayım */}
            {formData.type === "countdown_bar" && (
              <div className="space-y-4 p-4 bg-gradient-to-br from-amber-50 to-yellow-50 border border-amber-200 rounded-lg" data-testid="countdown-form">
                <div className="text-xs text-amber-800 leading-relaxed">
                  ⏱️ Bu blok sitenin <strong>EN ÜST barı</strong>nda görünür.
                  <ul className="mt-1 ml-4 list-disc space-y-0.5">
                    <li><strong>Başlangıç tarihi</strong> gelene kadar bar gizli kalır.</li>
                    <li>Başlangıç gelince countdown otomatik aktifleşir.</li>
                    <li><strong>Bitiş tarihi</strong>nde bar otomatik kaybolur (yedek metin tanımlıysa o gösterilir).</li>
                  </ul>
                </div>

                <div className="grid md:grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs font-semibold uppercase tracking-wider mb-1">Sol Metin</label>
                    <input
                      type="text"
                      value={formData.settings?.left_text || ""}
                      onChange={(e) => setFormData({ ...formData, settings: { ...formData.settings, left_text: e.target.value } })}
                      placeholder="TÜM ALIŞVERİŞLERDE KARGO BEDAVA"
                      className="w-full border border-gray-300 px-3 py-2 rounded text-sm"
                      data-testid="countdown-left-text"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-semibold uppercase tracking-wider mb-1">Sayaç Etiketi</label>
                    <input
                      type="text"
                      value={formData.settings?.timer_label || ""}
                      onChange={(e) => setFormData({ ...formData, settings: { ...formData.settings, timer_label: e.target.value } })}
                      placeholder="KALAN SÜRE:"
                      className="w-full border border-gray-300 px-3 py-2 rounded text-sm"
                      data-testid="countdown-timer-label"
                    />
                  </div>
                </div>

                <div className="grid md:grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs font-semibold uppercase tracking-wider mb-1">
                      🟢 Başlangıç Tarihi/Saati (planlama)
                    </label>
                    <input
                      type="datetime-local"
                      value={formData.settings?.start_at || ""}
                      onChange={(e) => setFormData({ ...formData, settings: { ...formData.settings, start_at: e.target.value } })}
                      className="w-full border border-gray-300 px-3 py-2 rounded text-sm font-mono"
                      data-testid="countdown-start-at"
                    />
                    <p className="text-[10px] text-gray-500 mt-1">Boş bırakılırsa hemen aktif olur.</p>
                  </div>
                  <div>
                    <label className="block text-xs font-semibold uppercase tracking-wider mb-1">
                      🔴 Bitiş Tarihi/Saati (countdown hedef)
                    </label>
                    <input
                      type="datetime-local"
                      value={formData.settings?.end_at || ""}
                      onChange={(e) => setFormData({ ...formData, settings: { ...formData.settings, end_at: e.target.value } })}
                      className="w-full border border-gray-300 px-3 py-2 rounded text-sm font-mono"
                      data-testid="countdown-end-at"
                    />
                    <p className="text-[10px] text-gray-500 mt-1">Bu tarihte sayaç sıfırlanıp bar gizlenir.</p>
                  </div>
                </div>

                <div className="grid md:grid-cols-3 gap-3">
                  <div>
                    <label className="block text-xs font-semibold uppercase tracking-wider mb-1">Arkaplan Rengi</label>
                    <div className="flex gap-2">
                      <input
                        type="color"
                        value={formData.settings?.bg_color || "#000000"}
                        onChange={(e) => setFormData({ ...formData, settings: { ...formData.settings, bg_color: e.target.value } })}
                        className="w-10 h-10 border border-gray-300 rounded cursor-pointer"
                      />
                      <input
                        type="text"
                        value={formData.settings?.bg_color || "#000000"}
                        onChange={(e) => setFormData({ ...formData, settings: { ...formData.settings, bg_color: e.target.value } })}
                        className="flex-1 border border-gray-300 px-2 py-2 rounded text-sm font-mono"
                      />
                    </div>
                  </div>
                  <div>
                    <label className="block text-xs font-semibold uppercase tracking-wider mb-1">Yazı Rengi</label>
                    <div className="flex gap-2">
                      <input
                        type="color"
                        value={formData.settings?.text_color || "#ffffff"}
                        onChange={(e) => setFormData({ ...formData, settings: { ...formData.settings, text_color: e.target.value } })}
                        className="w-10 h-10 border border-gray-300 rounded cursor-pointer"
                      />
                      <input
                        type="text"
                        value={formData.settings?.text_color || "#ffffff"}
                        onChange={(e) => setFormData({ ...formData, settings: { ...formData.settings, text_color: e.target.value } })}
                        className="flex-1 border border-gray-300 px-2 py-2 rounded text-sm font-mono"
                      />
                    </div>
                  </div>
                  <div>
                    <label className="block text-xs font-semibold uppercase tracking-wider mb-1">Yedek Metin (bar pasifken)</label>
                    <input
                      type="text"
                      value={formData.settings?.fallback_text || ""}
                      onChange={(e) => setFormData({ ...formData, settings: { ...formData.settings, fallback_text: e.target.value } })}
                      placeholder="500 TL Üzeri Ücretsiz Kargo"
                      className="w-full border border-gray-300 px-3 py-2 rounded text-sm"
                    />
                    <p className="text-[10px] text-gray-500 mt-1">Boşsa pasif iken bar tamamen gizli.</p>
                  </div>
                </div>

                {/* Canlı Önizleme */}
                <div className="border-t border-amber-200 pt-3">
                  <p className="text-xs font-semibold uppercase tracking-wider mb-2 text-amber-800">📺 Canlı Önizleme</p>
                  <div
                    className="text-center py-2.5 rounded"
                    style={{ backgroundColor: formData.settings?.bg_color || "#000000", color: formData.settings?.text_color || "#ffffff" }}
                  >
                    <div className="flex items-center justify-center gap-3 flex-wrap text-xs uppercase tracking-[0.2em]">
                      {formData.settings?.left_text && <span>{formData.settings.left_text}</span>}
                      {formData.settings?.timer_label && <span className="hidden md:inline">{formData.settings.timer_label}</span>}
                      <CountdownPreviewMini endAt={formData.settings?.end_at} bg={formData.settings?.bg_color || "#000"} fg={formData.settings?.text_color || "#fff"} />
                    </div>
                  </div>
                </div>
              </div>
            )}


            {formData.type === "rotating_text" && (
              <div className="space-y-4 bg-amber-50/40 border border-amber-200 rounded-lg p-4">
                <p className="text-xs text-amber-800">
                  📢 Bu blok sitenin <strong>üst duyuru barında</strong> (header altı) görünür.
                  Birden fazla metin eklersen sırayla döner.
                </p>

                <div>
                  <label className="block text-xs font-semibold uppercase tracking-wider mb-2">Duyuru Metinleri</label>
                  <div className="space-y-2">
                    {(formData.settings?.texts || [""]).map((txt, i) => (
                      <div key={i} className="flex gap-2 items-center">
                        <input
                          type="text"
                          value={txt}
                          onChange={(e) => {
                            const arr = [...(formData.settings?.texts || [""])];
                            arr[i] = e.target.value;
                            setFormData({ ...formData, settings: { ...formData.settings, texts: arr } });
                          }}
                          placeholder="Örn: 500 TL Üzeri Ücretsiz Kargo"
                          className="flex-1 border border-gray-300 px-3 py-2 rounded text-sm"
                          data-testid={`rotating-text-input-${i}`}
                        />
                        <button
                          type="button"
                          onClick={() => {
                            const arr = [...(formData.settings?.texts || [""])];
                            arr.splice(i, 1);
                            setFormData({ ...formData, settings: { ...formData.settings, texts: arr.length ? arr : [""] } });
                          }}
                          className="text-red-500 hover:text-red-700 px-2 py-2 shrink-0"
                          title="Sil"
                          data-testid={`rotating-text-remove-${i}`}
                        >✕</button>
                      </div>
                    ))}
                  </div>
                  <button
                    type="button"
                    onClick={() => setFormData({ ...formData, settings: { ...formData.settings, texts: [...(formData.settings?.texts || [""]), ""] } })}
                    className="mt-2 text-xs bg-stone-800 text-white px-3 py-1.5 rounded hover:bg-black"
                    data-testid="rotating-text-add"
                  >+ Metin Ekle</button>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xs font-semibold uppercase tracking-wider mb-1">Arka Plan</label>
                    <div className="flex gap-2">
                      <input type="color" value={formData.settings?.bg_color || "#ffffff"}
                        onChange={(e) => setFormData({ ...formData, settings: { ...formData.settings, bg_color: e.target.value } })}
                        className="w-10 h-10 border border-gray-300 rounded cursor-pointer" />
                      <input type="text" value={formData.settings?.bg_color || "#ffffff"}
                        onChange={(e) => setFormData({ ...formData, settings: { ...formData.settings, bg_color: e.target.value } })}
                        className="flex-1 border border-gray-300 px-2 py-2 rounded text-sm font-mono" />
                    </div>
                  </div>
                  <div>
                    <label className="block text-xs font-semibold uppercase tracking-wider mb-1">Yazı Rengi</label>
                    <div className="flex gap-2">
                      <input type="color" value={formData.settings?.text_color || "#374151"}
                        onChange={(e) => setFormData({ ...formData, settings: { ...formData.settings, text_color: e.target.value } })}
                        className="w-10 h-10 border border-gray-300 rounded cursor-pointer" />
                      <input type="text" value={formData.settings?.text_color || "#374151"}
                        onChange={(e) => setFormData({ ...formData, settings: { ...formData.settings, text_color: e.target.value } })}
                        className="flex-1 border border-gray-300 px-2 py-2 rounded text-sm font-mono" />
                    </div>
                  </div>
                </div>

                <div>
                  <label className="block text-xs font-semibold uppercase tracking-wider mb-1">Dönüş Süresi (saniye)</label>
                  <input type="number" min="2" value={formData.settings?.interval || 4}
                    onChange={(e) => setFormData({ ...formData, settings: { ...formData.settings, interval: Number(e.target.value) } })}
                    className="w-28 border border-gray-300 px-3 py-2 rounded text-sm" />
                </div>

                {/* Canlı Önizleme */}
                <div className="border-t border-amber-200 pt-3">
                  <p className="text-xs font-semibold uppercase tracking-wider mb-2 text-amber-800">📺 Canlı Önizleme</p>
                  <div className="text-center py-1.5 rounded border border-gray-200"
                    style={{ backgroundColor: formData.settings?.bg_color || "#ffffff" }}>
                    <p className="text-[10px] tracking-[0.3em] uppercase font-light"
                      style={{ color: formData.settings?.text_color || "#374151" }}>
                      {(formData.settings?.texts || []).filter((t) => (t || "").trim())[0] || "Metin girilmedi"}
                    </p>
                  </div>
                </div>
              </div>
            )}


            {formData.type === "product_slider" && (
              <div>
                {/* Kaynak seçimi: elle seçim yerine dinamik listeler (favori/indirim/kategori) */}
                <label className="block text-sm font-medium mb-2">Ürün Kaynağı</label>
                <select
                  value={formData.settings?.source || "manual"}
                  onChange={(e) => setFormData({ ...formData, settings: { ...formData.settings, source: e.target.value } })}
                  className="w-full border px-3 py-2 rounded text-sm mb-3"
                  data-testid="slider-source-select"
                >
                  <option value="manual">Elle Seçim (aşağıdan ürün ekle)</option>
                  <option value="newest">En Yeni Ürünler (otomatik)</option>
                  <option value="favorites">En Çok Favorilenenler (otomatik)</option>
                  <option value="discounted">İndirimdeki Ürünler (sale + kampanya, otomatik)</option>
                  <option value="category">Seçili Kategorilerden (otomatik)</option>
                </select>

                <div className="flex gap-3 mb-3">
                  <div className="flex-1">
                    <label className="block text-xs font-medium mb-1 text-gray-600">Gösterilecek Ürün Adedi</label>
                    <input
                      type="number" min={1} max={24}
                      value={formData.settings?.limit || 8}
                      onChange={(e) => setFormData({ ...formData, settings: { ...formData.settings, limit: Math.max(1, Math.min(24, Number(e.target.value) || 8)) } })}
                      className="w-full border px-3 py-2 rounded text-sm"
                    />
                  </div>
                  <div className="w-32">
                    <label className="block text-xs font-medium mb-1 text-gray-600">Kaç Satır (alt alta)</label>
                    <select
                      value={formData.settings?.rows || 1}
                      onChange={(e) => setFormData({ ...formData, settings: { ...formData.settings, rows: Number(e.target.value) } })}
                      className="w-full border px-3 py-2 rounded text-sm"
                    >
                      <option value={1}>1 satır</option>
                      <option value={2}>2 satır</option>
                      <option value={3}>3 satır</option>
                    </select>
                  </div>
                </div>

                {/* Başlık bloğu (2. görsel tarzı): Başlık = üstteki "Blok Başlığı" alanı.
                    Alt yazı + "Tümünü Gör" bağlantısı buradan. Başlık girilmezse blok başlıksız çıkar. */}
                <div className="grid grid-cols-1 gap-2 mb-3 bg-gray-50 border rounded-lg p-3">
                  <p className="text-[11px] text-gray-500">Üstteki <b>Blok Başlığı</b> girilirse ürünlerin üstünde büyük başlık olarak çıkar (ör. "Senin için seçtik."). Aşağıdakiler opsiyonel:</p>
                  <input
                    type="text"
                    value={formData.settings?.subtitle || ""}
                    onChange={(e) => setFormData({ ...formData, settings: { ...formData.settings, subtitle: e.target.value } })}
                    placeholder="Alt yazı (ör. Koleksiyonumuzdan senin stilini tamamlayacak parçalar.)"
                    className="w-full border px-3 py-2 rounded text-sm"
                  />
                  <div className="flex gap-2">
                    <input
                      type="text"
                      value={formData.settings?.cta_label || ""}
                      onChange={(e) => setFormData({ ...formData, settings: { ...formData.settings, cta_label: e.target.value } })}
                      placeholder="Buton yazısı (varsayılan: Tümünü Gör)"
                      className="flex-1 border px-3 py-2 rounded text-sm"
                    />
                    <input
                      type="text"
                      value={formData.settings?.cta_link || ""}
                      onChange={(e) => setFormData({ ...formData, settings: { ...formData.settings, cta_link: e.target.value } })}
                      placeholder="Buton linki (ör. /elbise)"
                      className="flex-1 border px-3 py-2 rounded text-sm"
                    />
                  </div>
                  <div className="w-44">
                    <label className="block text-xs font-medium mb-1 text-gray-600">Arka Plan Rengi <span className="text-gray-400">(boş = şeffaf)</span></label>
                    <div className="flex gap-1.5">
                      <input type="color" value={formData.settings?.bg_color || "#ffffff"}
                        onChange={(e) => setFormData({ ...formData, settings: { ...formData.settings, bg_color: e.target.value } })}
                        className="w-10 h-9 border rounded cursor-pointer" />
                      <input type="text" value={formData.settings?.bg_color || ""}
                        placeholder="#EFECE6"
                        onChange={(e) => setFormData({ ...formData, settings: { ...formData.settings, bg_color: e.target.value } })}
                        className="flex-1 border px-2 py-2 rounded text-sm font-mono" />
                    </div>
                  </div>
                </div>

                {(formData.settings?.source === "category") && (
                  <div className="mb-4">
                    <label className="block text-xs font-medium mb-1 text-gray-600">Kategoriler (çoklu seçim: ⌘/Ctrl ile)</label>
                    <select
                      multiple
                      value={formData.settings?.category_ids || []}
                      onChange={(e) => {
                        const vals = Array.from(e.target.selectedOptions).map((o) => o.value);
                        setFormData({ ...formData, settings: { ...formData.settings, category_ids: vals } });
                      }}
                      className="w-full border px-3 py-2 rounded text-sm h-40"
                      data-testid="slider-category-select"
                    >
                      {sliderCategories.map((c) => (
                        <option key={c.id} value={c.id}>{c.name}</option>
                      ))}
                    </select>
                  </div>
                )}

                {(formData.settings?.source || "manual") === "manual" && (
                <>
                <label className="block text-sm font-medium mb-2">Ürün Seçimi</label>
                <div className="bg-gray-50 p-4 rounded-lg border">
                  
                  {/* Search */}
                  <div className="flex gap-2 mb-4">
                    <input
                      type="text"
                      value={productSearch}
                      onChange={(e) => setProductSearch(e.target.value)}
                      placeholder="Ürün adı veya barkod ile ara..."
                      className="flex-1 border px-3 py-2 rounded text-sm"
                      onKeyDown={(e) => e.key === 'Enter' && handleProductSearch(e)}
                    />
                    <button
                      type="button"
                      onClick={handleProductSearch}
                      disabled={searchingProducts}
                      className="px-4 py-2 bg-black text-white rounded text-sm disabled:opacity-50"
                    >
                      {searchingProducts ? "Aranıyor..." : "Ara"}
                    </button>
                  </div>

                  {/* Search Results */}
                  {searchResults.length > 0 && (
                    <div className="mb-4 max-h-40 overflow-y-auto border bg-white rounded shadow-sm">
                      {searchResults.map(p => (
                        <div key={p._id} className="flex items-center justify-between p-2 border-b last:border-0 hover:bg-gray-50 text-sm">
                          <div className="flex items-center gap-2">
                            <img src={p.images?.[0]?.url || ""} alt="" className="w-8 h-8 rounded object-cover bg-gray-100" />
                            <span className="truncate max-w-[200px]">{p.name}</span>
                          </div>
                          <button
                            type="button"
                            onClick={() => addProductToBlock(p)}
                            className="text-blue-600 text-xs font-semibold px-2 py-1 bg-blue-50 rounded hover:bg-blue-100"
                          >
                            Ekle
                          </button>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Selected Products */}
                  <div>
                    <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">Seçili Ürünler ({selectedProductDetails.length})</h4>
                    <div className="space-y-2">
                      {selectedProductDetails.length === 0 && (
                        <p className="text-sm text-gray-400 italic">Henüz ürün seçilmedi. (Boş bırakılırsa en yeni ürünler gösterilir)</p>
                      )}
                      {selectedProductDetails.map((p, index) => (
                        <div key={`${p._id}-${index}`} className="flex items-center justify-between p-2 bg-white border rounded shadow-sm text-sm">
                          <div className="flex items-center gap-2">
                            <span className="text-gray-400 font-mono text-xs">{index + 1}.</span>
                            <img src={p.images?.[0]?.url || ""} alt="" className="w-8 h-8 rounded object-cover bg-gray-100" />
                            <span className="truncate max-w-[200px]">{p.name}</span>
                          </div>
                          <button
                            type="button"
                            onClick={() => removeProductFromBlock(index)}
                            className="text-red-500 hover:text-red-700 p-1"
                          >
                            <X size={16} />
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                  
                </div>
                </>
                )}
              </div>
            )}


            {/* Footer */}
            <div className="flex justify-end gap-2 pt-4 border-t">
              <button
                type="button"
                onClick={() => setModalOpen(false)}
                className="px-4 py-2 border rounded hover:bg-gray-50"
              >
                İptal
              </button>
              <button
                type="submit"
                className="px-4 py-2 bg-black text-white rounded hover:bg-gray-800"
              >
                {editingBlock ? "Güncelle" : "Ekle"}
              </button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// Mini countdown kutusu (modül seviyesinde — render içinde tanımlanmaz)
function MiniCountBox({ v, l, bg, fg }) {
  const pad = (n) => String(n).padStart(2, "0");
  return (
    <span className="inline-flex items-center gap-1">
      <span className="inline-flex items-center justify-center min-w-[26px] h-6 px-1 text-xs font-semibold tabular-nums"
        style={{ backgroundColor: fg, color: bg }}>{pad(v)}</span>
      <span className="text-[10px] tracking-wider">{l}</span>
    </span>
  );
}

// Mini preview countdown for the form (admin-side, isolated)
function CountdownPreviewMini({ endAt, bg, fg }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  if (!endAt) return <span className="opacity-60 italic">— bitiş tarihi giriniz —</span>;
  const target = new Date(endAt).getTime();
  let ms = target - now;
  if (ms < 0) ms = 0;
  const d = Math.floor(ms / 86400000);
  const h = Math.floor((ms % 86400000) / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  const s = Math.floor((ms % 60000) / 1000);
  return (
    <span className="flex items-center gap-2">
      <MiniCountBox v={d} l="GÜN" bg={bg} fg={fg} />
      <MiniCountBox v={h} l="SAAT" bg={bg} fg={fg} />
      <MiniCountBox v={m} l="DK" bg={bg} fg={fg} />
      <MiniCountBox v={s} l="SN" bg={bg} fg={fg} />
    </span>
  );
}
