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

export default function AdminPages() {
  const [pages, setPages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingPage, setEditingPage] = useState(null);
  const [formData, setFormData] = useState({
    title: "",
    slug: "",
    content: "",
    meta_title: "",
    meta_description: "",
    is_active: true,
  });

  useEffect(() => {
    fetchPages();
  }, []);

  const fetchPages = async () => {
    setLoading(true);
    try {
      const res = await axios.get(`${API}/pages`);
      setPages(res.data || []);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      if (editingPage) {
        await axios.put(`${API}/pages/${editingPage.id}`, formData);
        toast.success("Sayfa güncellendi");
      } else {
        await axios.post(`${API}/pages`, formData);
        toast.success("Sayfa oluşturuldu");
      }
      setModalOpen(false);
      resetForm();
      fetchPages();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Hata oluştu");
    }
  };

  const openEditModal = (page) => {
    setEditingPage(page);
    setFormData({
      title: page.title,
      slug: page.slug,
      content: page.content,
      meta_title: page.meta_title || "",
      meta_description: page.meta_description || "",
      is_active: page.is_active,
    });
    setModalOpen(true);
  };

  const resetForm = () => {
    setEditingPage(null);
    setFormData({
      title: "", slug: "", content: "", meta_title: "", meta_description: "", is_active: true
    });
  };

  const generateSlug = (title) => {
    return title.toLowerCase()
      .replace(/ğ/g, 'g').replace(/ü/g, 'u').replace(/ş/g, 's')
      .replace(/ı/g, 'i').replace(/ö/g, 'o').replace(/ç/g, 'c')
      .replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  };

  return (
    <div data-testid="admin-pages">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Sayfalar</h1>
        <button 
          onClick={() => { resetForm(); setModalOpen(true); }}
          className="flex items-center gap-2 bg-black text-white px-4 py-2 rounded hover:bg-gray-800"
        >
          <Plus size={18} />
          Yeni Sayfa
        </button>
      </div>

      <div className="bg-white rounded-lg shadow-sm overflow-hidden">
        <table className="admin-table">
          <thead>
            <tr>
              <th>Sayfa Başlığı</th>
              <th>Slug</th>
              <th>Durum</th>
              <th>İşlemler</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={4} className="text-center py-8">Yükleniyor...</td>
              </tr>
            ) : pages.length === 0 ? (
              <tr>
                <td colSpan={4} className="text-center py-8 text-gray-500">Sayfa bulunamadı</td>
              </tr>
            ) : (
              pages.map((page) => (
                <tr key={page.id}>
                  <td className="font-medium">{page.title}</td>
                  <td className="text-gray-500">/sayfa/{page.slug}</td>
                  <td>
                    <span className={`px-2 py-1 text-xs rounded ${page.is_active ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-500"}`}>
                      {page.is_active ? "Aktif" : "Pasif"}
                    </span>
                  </td>
                  <td>
                    <button onClick={() => openEditModal(page)} className="p-1 hover:bg-gray-100 rounded text-blue-600">
                      <Edit size={16} />
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Modal */}
      <Dialog open={modalOpen} onOpenChange={setModalOpen}>
        <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editingPage ? "Sayfa Düzenle" : "Yeni Sayfa"}</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="grid md:grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium mb-1">Sayfa Başlığı *</label>
                <input
                  type="text"
                  value={formData.title}
                  onChange={(e) => setFormData({ ...formData, title: e.target.value, slug: editingPage ? formData.slug : generateSlug(e.target.value) })}
                  required
                  className="w-full border px-3 py-2 rounded text-sm"
                />
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">Slug</label>
                <input
                  type="text"
                  value={formData.slug}
                  onChange={(e) => setFormData({ ...formData, slug: e.target.value })}
                  className="w-full border px-3 py-2 rounded text-sm"
                />
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">İçerik (HTML)</label>
              <textarea
                value={formData.content}
                onChange={(e) => setFormData({ ...formData, content: e.target.value })}
                rows={10}
                className="w-full border px-3 py-2 rounded text-sm font-mono"
              />
            </div>
            <div className="grid md:grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium mb-1">SEO Başlık</label>
                <input
                  type="text"
                  value={formData.meta_title}
                  onChange={(e) => setFormData({ ...formData, meta_title: e.target.value })}
                  className="w-full border px-3 py-2 rounded text-sm"
                />
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">SEO Açıklama</label>
                <input
                  type="text"
                  value={formData.meta_description}
                  onChange={(e) => setFormData({ ...formData, meta_description: e.target.value })}
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
                {editingPage ? "Güncelle" : "Oluştur"}
              </button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
