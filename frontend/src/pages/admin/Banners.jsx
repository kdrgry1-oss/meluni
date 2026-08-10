import { useState, useEffect } from "react";
import { Plus, Edit, Trash2 } from "lucide-react";
import axios from "axios";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const positionOptions = [
  { value: "hero_slider", label: "Ana Slider" },
  { value: "single_banner", label: "Tekli Banner" },
  { value: "double_banner", label: "İkili Banner" },
  { value: "instashop", label: "InstaShop" },
];

export default function AdminBanners() {
  const [banners, setBanners] = useState([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingBanner, setEditingBanner] = useState(null);
  const [formData, setFormData] = useState({
    title: "",
    subtitle: "",
    image_url: "",
    video_url: "",
    link_url: "",
    position: "hero_slider",
    sort_order: 0,
    is_active: true,
    device: "all",
  });

  useEffect(() => {
    fetchBanners();
  }, []);

  const fetchBanners = async () => {
    setLoading(true);
    try {
      const res = await axios.get(`${API}/banners`);
      setBanners(res.data || []);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      if (editingBanner) {
        await axios.put(`${API}/banners/${editingBanner.id}`, formData);
        toast.success("Banner güncellendi");
      } else {
        await axios.post(`${API}/banners`, formData);
        toast.success("Banner eklendi");
      }
      setModalOpen(false);
      resetForm();
      fetchBanners();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Hata oluştu");
    }
  };

  const handleDelete = async (id) => {
    toast("Banner'ı silmek istediğinize emin misiniz?", {
      action: {
        label: 'Sil',
        onClick: async () => {
          try {
            const token = localStorage.getItem("token");
            await axios.delete(`${API}/banners/${id}`, {
              headers: { Authorization: `Bearer ${token}` },
            });
            toast.success("Banner silindi");
            fetchBanners();
          } catch (err) {
            toast.error("Silme başarısız");
          }
        }
      },
      cancel: { label: 'İptal', onClick: () => {} },
      duration: 8000,
    });
  };

  const openEditModal = (banner) => {
    setEditingBanner(banner);
    setFormData({
      title: banner.title || "",
      subtitle: banner.subtitle || "",
      image_url: banner.image_url,
      video_url: banner.video_url || "",
      link_url: banner.link_url || "",
      position: banner.position,
      sort_order: banner.sort_order || 0,
      is_active: banner.is_active,
      device: banner.device || "all",
    });
    setModalOpen(true);
  };

  const resetForm = () => {
    setEditingBanner(null);
    setFormData({
      title: "", subtitle: "", image_url: "", video_url: "", link_url: "",
      position: "hero_slider", sort_order: 0, is_active: true, device: "all"
    });
  };

  const getPositionLabel = (position) => {
    return positionOptions.find(p => p.value === position)?.label || position;
  };

  return (
    <div data-testid="admin-banners">
      {/* DENETİM FIX (#34): Asıl vitrin banner yönetimi Sayfa Tasarımı blok sistemidir; bu ekran
          ikincil/legacy API tüketicileri içindir. Karışıklığı önlemek için bilgilendirme. */}
      <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
        <strong>Not:</strong> Ana sayfa vitrin banner'ları <strong>Sayfa Tasarımı</strong> (blok sistemi)
        üzerinden yönetilir ve mağazada oradan gösterilir. Bu ekrandaki banner'lar ikincil/legacy
        entegrasyonlar içindir; ana sayfada otomatik görünmez.
      </div>

      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Bannerlar</h1>
        <button 
          onClick={() => { resetForm(); setModalOpen(true); }}
          className="flex items-center gap-2 bg-black text-white px-4 py-2 rounded hover:bg-gray-800"
        >
          <Plus size={18} />
          Yeni Banner
        </button>
      </div>

      {/* Group by position */}
      {positionOptions.map((pos) => {
        const positionBanners = banners.filter(b => b.position === pos.value);
        if (positionBanners.length === 0) return null;

        return (
          <div key={pos.value} className="mb-8">
            <h2 className="text-lg font-medium mb-4">{pos.label}</h2>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              {positionBanners.map((banner) => (
                <div key={banner.id} className="bg-white rounded-lg shadow-sm overflow-hidden">
                  <div className="aspect-video relative">
                    <img 
                      src={banner.image_url} 
                      alt={banner.title || "Banner"} 
                      className="w-full h-full object-cover"
                    />
                    {!banner.is_active && (
                      <div className="absolute inset-0 bg-black/50 flex items-center justify-center">
                        <span className="text-white text-sm">Pasif</span>
                      </div>
                    )}
                  </div>
                  <div className="p-3">
                    <p className="font-medium truncate">{banner.title || "Başlıksız"}</p>
                    <p className="text-xs text-gray-500">Sıra: {banner.sort_order}</p>
                    <div className="flex gap-2 mt-2">
                      <button onClick={() => openEditModal(banner)} className="text-blue-600 text-sm hover:underline">
                        Düzenle
                      </button>
                      <button onClick={() => handleDelete(banner.id)} className="text-red-500 text-sm hover:underline">
                        Sil
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        );
      })}

      {banners.length === 0 && !loading && (
        <div className="text-center py-16 text-gray-500">
          Henüz banner eklenmemiş
        </div>
      )}

      {/* Modal */}
      <Dialog open={modalOpen} onOpenChange={setModalOpen}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>{editingBanner ? "Banner Düzenle" : "Yeni Banner"}</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="grid md:grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium mb-1">Başlık</label>
                <input
                  type="text"
                  value={formData.title}
                  onChange={(e) => setFormData({ ...formData, title: e.target.value })}
                  className="w-full border px-3 py-2 rounded text-sm"
                />
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">Alt Başlık</label>
                <input
                  type="text"
                  value={formData.subtitle}
                  onChange={(e) => setFormData({ ...formData, subtitle: e.target.value })}
                  className="w-full border px-3 py-2 rounded text-sm"
                />
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Görsel URL *</label>
              <input
                type="url"
                value={formData.image_url}
                onChange={(e) => setFormData({ ...formData, image_url: e.target.value })}
                required
                className="w-full border px-3 py-2 rounded text-sm"
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Video URL (opsiyonel)</label>
              <input
                type="url"
                value={formData.video_url}
                onChange={(e) => setFormData({ ...formData, video_url: e.target.value })}
                className="w-full border px-3 py-2 rounded text-sm"
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Link URL</label>
              <input
                type="text"
                value={formData.link_url}
                onChange={(e) => setFormData({ ...formData, link_url: e.target.value })}
                placeholder="/kategori/en-yeniler"
                className="w-full border px-3 py-2 rounded text-sm"
              />
            </div>
            <div className="grid md:grid-cols-3 gap-4">
              <div>
                <label className="block text-sm font-medium mb-1">Pozisyon</label>
                <select
                  value={formData.position}
                  onChange={(e) => setFormData({ ...formData, position: e.target.value })}
                  className="w-full border px-3 py-2 rounded text-sm"
                >
                  {positionOptions.map((opt) => (
                    <option key={opt.value} value={opt.value}>{opt.label}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">Cihaz</label>
                <select
                  value={formData.device}
                  onChange={(e) => setFormData({ ...formData, device: e.target.value })}
                  className="w-full border px-3 py-2 rounded text-sm"
                >
                  <option value="all">Tümü</option>
                  <option value="desktop">Masaüstü</option>
                  <option value="mobile">Mobil</option>
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">Sıra</label>
                <input
                  type="number"
                  value={formData.sort_order}
                  onChange={(e) => setFormData({ ...formData, sort_order: parseInt(e.target.value) })}
                  className="w-full border px-3 py-2 rounded text-sm"
                />
              </div>
            </div>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={formData.is_active}
                onChange={(e) => setFormData({ ...formData, is_active: e.target.checked })}
              />
              <span className="text-sm">Aktif</span>
            </label>
            <div className="flex justify-end gap-2 pt-4 border-t">
              <button type="button" onClick={() => setModalOpen(false)} className="px-4 py-2 border rounded hover:bg-gray-50">
                İptal
              </button>
              <button type="submit" className="px-4 py-2 bg-black text-white rounded hover:bg-gray-800">
                {editingBanner ? "Güncelle" : "Ekle"}
              </button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
