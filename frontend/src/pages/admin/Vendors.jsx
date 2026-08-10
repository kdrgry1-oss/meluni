import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { Plus, Search, Edit2, Trash2, Building2, User, Phone, Mail, MapPin, CreditCard, X, Check } from 'lucide-react';

const API = process.env.REACT_APP_BACKEND_URL + '/api';

export default function Vendors() {
  const [vendors, setVendors] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [vendorType, setVendorType] = useState('all');
  const [modalOpen, setModalOpen] = useState(false);
  const [editingVendor, setEditingVendor] = useState(null);
  const [formData, setFormData] = useState({
    name: '',
    vendor_type: 'supplier',
    company_name: '',
    tax_office: '',
    tax_number: '',
    identity_number: '',
    address: '',
    city: '',
    district: '',
    postal_code: '',
    country: 'Türkiye',
    phone: '',
    email: '',
    website: '',
    contact_person: '',
    contact_phone: '',
    bank_name: '',
    iban: '',
    notes: '',
    is_active: true
  });

  useEffect(() => {
    fetchVendors();
  }, [search, vendorType]);

  const fetchVendors = async () => {
    setLoading(true);
    try {
      const token = localStorage.getItem('token');
      const params = new URLSearchParams();
      if (search) params.append('search', search);
      if (vendorType !== 'all') params.append('vendor_type', vendorType);
      
      const res = await axios.get(`${API}/vendors?${params.toString()}`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      setVendors(res.data.vendors || []);
    } catch (err) {
      toast.error('Cariler yüklenemedi');
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    
    if (!formData.name.trim()) {
      toast.error('Cari adı zorunludur');
      return;
    }
    
    try {
      const token = localStorage.getItem('token');
      
      if (editingVendor) {
        await axios.put(`${API}/vendors/${editingVendor.id}`, formData, {
          headers: { Authorization: `Bearer ${token}` }
        });
        toast.success('Cari güncellendi');
      } else {
        await axios.post(`${API}/vendors`, formData, {
          headers: { Authorization: `Bearer ${token}` }
        });
        toast.success('Cari oluşturuldu');
      }
      
      setModalOpen(false);
      resetForm();
      fetchVendors();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Bir hata oluştu');
    }
  };

  const handleEdit = (vendor) => {
    setEditingVendor(vendor);
    setFormData({
      name: vendor.name || '',
      vendor_type: vendor.vendor_type || 'supplier',
      company_name: vendor.company_name || '',
      tax_office: vendor.tax_office || '',
      tax_number: vendor.tax_number || '',
      identity_number: vendor.identity_number || '',
      address: vendor.address || '',
      city: vendor.city || '',
      district: vendor.district || '',
      postal_code: vendor.postal_code || '',
      country: vendor.country || 'Türkiye',
      phone: vendor.phone || '',
      email: vendor.email || '',
      website: vendor.website || '',
      contact_person: vendor.contact_person || '',
      contact_phone: vendor.contact_phone || '',
      bank_name: vendor.bank_name || '',
      iban: vendor.iban || '',
      notes: vendor.notes || '',
      is_active: vendor.is_active ?? true
    });
    setModalOpen(true);
  };

  const handleDelete = async (vendor) => {
    if (!await window.appConfirm(`"${vendor.name}" silinecek. Emin misiniz?`)) return;
    
    try {
      const token = localStorage.getItem('token');
      await axios.delete(`${API}/vendors/${vendor.id}`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      toast.success('Cari silindi');
      fetchVendors();
    } catch (err) {
      toast.error('Silinemedi');
    }
  };

  const resetForm = () => {
    setEditingVendor(null);
    setFormData({
      name: '',
      vendor_type: 'supplier',
      company_name: '',
      tax_office: '',
      tax_number: '',
      identity_number: '',
      address: '',
      city: '',
      district: '',
      postal_code: '',
      country: 'Türkiye',
      phone: '',
      email: '',
      website: '',
      contact_person: '',
      contact_phone: '',
      bank_name: '',
      iban: '',
      notes: '',
      is_active: true
    });
  };

  const seedFacette = async () => {
    try {
      const token = localStorage.getItem('token');
      await axios.post(`${API}/vendors/seed-default`, {}, {
        headers: { Authorization: `Bearer ${token}` }
      });
      toast.success('FACETTE üretici eklendi');
      fetchVendors();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Hata oluştu');
    }
  };

  return (
    <div className="p-6">
      <div className="flex justify-between items-center mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Cariler</h1>
          <p className="text-sm text-gray-500">Tedarikçi ve üretici yönetimi</p>
        </div>
        <div className="flex gap-3">
          <button
            onClick={seedFacette}
            className="px-4 py-2 bg-purple-600 text-white rounded-lg text-sm font-semibold hover:bg-purple-700 transition-colors"
          >
            FACETTE Üretici Ekle
          </button>
          <button
            onClick={() => { resetForm(); setModalOpen(true); }}
            className="flex items-center gap-2 px-4 py-2 bg-black text-white rounded-lg text-sm font-semibold hover:bg-gray-800 transition-colors"
          >
            <Plus size={18} />
            Yeni Cari
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="bg-white rounded-xl border p-4 mb-6">
        <div className="flex gap-4 items-center">
          <div className="relative flex-1 max-w-md">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" size={18} />
            <input
              type="text"
              placeholder="Cari ara (ad, şirket, vergi no)..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full pl-10 pr-4 py-2 border rounded-lg outline-none focus:border-black"
            />
          </div>
          <select
            value={vendorType}
            onChange={(e) => setVendorType(e.target.value)}
            className="px-4 py-2 border rounded-lg outline-none focus:border-black"
          >
            <option value="all">Tümü</option>
            <option value="supplier">Tedarikçiler</option>
            <option value="manufacturer">Üreticiler</option>
          </select>
        </div>
      </div>

      {/* Vendors List */}
      <div className="bg-white rounded-xl border overflow-hidden">
        {loading ? (
          <div className="p-8 text-center text-gray-500">Yükleniyor...</div>
        ) : vendors.length === 0 ? (
          <div className="p-8 text-center text-gray-500">
            <Building2 size={48} className="mx-auto mb-3 text-gray-300" />
            <p>Henüz cari bulunmuyor</p>
          </div>
        ) : (
          <table className="w-full">
            <thead className="bg-gray-50">
              <tr>
                <th className="text-left px-4 py-3 text-xs font-bold text-gray-500 uppercase">Cari Adı</th>
                <th className="text-left px-4 py-3 text-xs font-bold text-gray-500 uppercase">Tip</th>
                <th className="text-left px-4 py-3 text-xs font-bold text-gray-500 uppercase">Şirket</th>
                <th className="text-left px-4 py-3 text-xs font-bold text-gray-500 uppercase">Vergi No</th>
                <th className="text-left px-4 py-3 text-xs font-bold text-gray-500 uppercase">Telefon</th>
                <th className="text-left px-4 py-3 text-xs font-bold text-gray-500 uppercase">Şehir</th>
                <th className="text-left px-4 py-3 text-xs font-bold text-gray-500 uppercase">Durum</th>
                <th className="text-right px-4 py-3 text-xs font-bold text-gray-500 uppercase">İşlemler</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {vendors.map((vendor) => (
                <tr key={vendor.id} className="hover:bg-gray-50">
                  <td className="px-4 py-3">
                    <div className="font-semibold text-gray-900">{vendor.name}</div>
                    {vendor.email && <div className="text-xs text-gray-500">{vendor.email}</div>}
                  </td>
                  <td className="px-4 py-3">
                    <span className={`px-2 py-1 rounded-full text-xs font-semibold ${
                      vendor.vendor_type === 'manufacturer' 
                        ? 'bg-purple-100 text-purple-800' 
                        : 'bg-blue-100 text-blue-800'
                    }`}>
                      {vendor.vendor_type === 'manufacturer' ? 'Üretici' : 'Tedarikçi'}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-600">{vendor.company_name || '-'}</td>
                  <td className="px-4 py-3 text-sm font-mono text-gray-600">{vendor.tax_number || '-'}</td>
                  <td className="px-4 py-3 text-sm text-gray-600">{vendor.phone || '-'}</td>
                  <td className="px-4 py-3 text-sm text-gray-600">{vendor.city || '-'}</td>
                  <td className="px-4 py-3">
                    {vendor.is_active ? (
                      <span className="flex items-center gap-1 text-green-600 text-xs font-semibold">
                        <Check size={14} /> Aktif
                      </span>
                    ) : (
                      <span className="text-gray-400 text-xs font-semibold">Pasif</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="flex justify-end gap-2">
                      <button
                        onClick={() => handleEdit(vendor)}
                        className="p-2 text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
                      >
                        <Edit2 size={16} />
                      </button>
                      <button
                        onClick={() => handleDelete(vendor)}
                        className="p-2 text-red-600 hover:bg-red-50 rounded-lg transition-colors"
                      >
                        <Trash2 size={16} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Modal */}
      {modalOpen && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl w-full max-w-3xl max-h-[90vh] overflow-y-auto">
            <div className="sticky top-0 bg-white border-b px-6 py-4 flex justify-between items-center">
              <h2 className="text-xl font-bold">
                {editingVendor ? 'Cari Düzenle' : 'Yeni Cari'}
              </h2>
              <button onClick={() => { setModalOpen(false); resetForm(); }} className="p-2 hover:bg-gray-100 rounded-lg">
                <X size={20} />
              </button>
            </div>
            
            <form onSubmit={handleSubmit} className="p-6 space-y-6">
              {/* Basic Info */}
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Cari Adı *</label>
                  <input
                    type="text"
                    value={formData.name}
                    onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                    className="w-full border px-3 py-2 rounded-lg outline-none focus:border-black"
                    required
                  />
                </div>
                <div>
                  <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Tip *</label>
                  <select
                    value={formData.vendor_type}
                    onChange={(e) => setFormData({ ...formData, vendor_type: e.target.value })}
                    className="w-full border px-3 py-2 rounded-lg outline-none focus:border-black"
                  >
                    <option value="supplier">Tedarikçi</option>
                    <option value="manufacturer">Üretici</option>
                  </select>
                </div>
              </div>

              {/* Company Info */}
              <div className="border-t pt-4">
                <h3 className="font-semibold mb-3 flex items-center gap-2">
                  <Building2 size={18} /> Şirket Bilgileri
                </h3>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Şirket Unvanı</label>
                    <input
                      type="text"
                      value={formData.company_name}
                      onChange={(e) => setFormData({ ...formData, company_name: e.target.value })}
                      className="w-full border px-3 py-2 rounded-lg outline-none focus:border-black"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Vergi Dairesi</label>
                    <input
                      type="text"
                      value={formData.tax_office}
                      onChange={(e) => setFormData({ ...formData, tax_office: e.target.value })}
                      className="w-full border px-3 py-2 rounded-lg outline-none focus:border-black"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Vergi Kimlik No</label>
                    <input
                      type="text"
                      value={formData.tax_number}
                      onChange={(e) => setFormData({ ...formData, tax_number: e.target.value })}
                      className="w-full border px-3 py-2 rounded-lg outline-none focus:border-black font-mono"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-bold text-gray-500 uppercase mb-1">TC Kimlik No</label>
                    <input
                      type="text"
                      value={formData.identity_number}
                      onChange={(e) => setFormData({ ...formData, identity_number: e.target.value })}
                      className="w-full border px-3 py-2 rounded-lg outline-none focus:border-black font-mono"
                    />
                  </div>
                </div>
              </div>

              {/* Contact Info */}
              <div className="border-t pt-4">
                <h3 className="font-semibold mb-3 flex items-center gap-2">
                  <User size={18} /> İletişim Bilgileri
                </h3>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Telefon</label>
                    <input
                      type="text"
                      value={formData.phone}
                      onChange={(e) => setFormData({ ...formData, phone: e.target.value })}
                      className="w-full border px-3 py-2 rounded-lg outline-none focus:border-black"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-bold text-gray-500 uppercase mb-1">E-posta</label>
                    <input
                      type="email"
                      value={formData.email}
                      onChange={(e) => setFormData({ ...formData, email: e.target.value })}
                      className="w-full border px-3 py-2 rounded-lg outline-none focus:border-black"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Yetkili Kişi</label>
                    <input
                      type="text"
                      value={formData.contact_person}
                      onChange={(e) => setFormData({ ...formData, contact_person: e.target.value })}
                      className="w-full border px-3 py-2 rounded-lg outline-none focus:border-black"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Yetkili Telefon</label>
                    <input
                      type="text"
                      value={formData.contact_phone}
                      onChange={(e) => setFormData({ ...formData, contact_phone: e.target.value })}
                      className="w-full border px-3 py-2 rounded-lg outline-none focus:border-black"
                    />
                  </div>
                  <div className="col-span-2">
                    <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Website</label>
                    <input
                      type="text"
                      value={formData.website}
                      onChange={(e) => setFormData({ ...formData, website: e.target.value })}
                      className="w-full border px-3 py-2 rounded-lg outline-none focus:border-black"
                    />
                  </div>
                </div>
              </div>

              {/* Address */}
              <div className="border-t pt-4">
                <h3 className="font-semibold mb-3 flex items-center gap-2">
                  <MapPin size={18} /> Adres Bilgileri
                </h3>
                <div className="grid grid-cols-2 gap-4">
                  <div className="col-span-2">
                    <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Adres</label>
                    <textarea
                      value={formData.address}
                      onChange={(e) => setFormData({ ...formData, address: e.target.value })}
                      className="w-full border px-3 py-2 rounded-lg outline-none focus:border-black"
                      rows={2}
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Şehir</label>
                    <input
                      type="text"
                      value={formData.city}
                      onChange={(e) => setFormData({ ...formData, city: e.target.value })}
                      className="w-full border px-3 py-2 rounded-lg outline-none focus:border-black"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-bold text-gray-500 uppercase mb-1">İlçe</label>
                    <input
                      type="text"
                      value={formData.district}
                      onChange={(e) => setFormData({ ...formData, district: e.target.value })}
                      className="w-full border px-3 py-2 rounded-lg outline-none focus:border-black"
                    />
                  </div>
                </div>
              </div>

              {/* Bank Info */}
              <div className="border-t pt-4">
                <h3 className="font-semibold mb-3 flex items-center gap-2">
                  <CreditCard size={18} /> Banka Bilgileri
                </h3>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Banka Adı</label>
                    <input
                      type="text"
                      value={formData.bank_name}
                      onChange={(e) => setFormData({ ...formData, bank_name: e.target.value })}
                      className="w-full border px-3 py-2 rounded-lg outline-none focus:border-black"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-bold text-gray-500 uppercase mb-1">IBAN</label>
                    <input
                      type="text"
                      value={formData.iban}
                      onChange={(e) => setFormData({ ...formData, iban: e.target.value })}
                      className="w-full border px-3 py-2 rounded-lg outline-none focus:border-black font-mono"
                    />
                  </div>
                </div>
              </div>

              {/* Notes & Status */}
              <div className="border-t pt-4">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Notlar</label>
                    <textarea
                      value={formData.notes}
                      onChange={(e) => setFormData({ ...formData, notes: e.target.value })}
                      className="w-full border px-3 py-2 rounded-lg outline-none focus:border-black"
                      rows={3}
                    />
                  </div>
                  <div className="flex items-center">
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={formData.is_active}
                        onChange={(e) => setFormData({ ...formData, is_active: e.target.checked })}
                        className="w-5 h-5 rounded border-gray-300"
                      />
                      <span className="font-semibold">Aktif</span>
                    </label>
                  </div>
                </div>
              </div>

              {/* Submit */}
              <div className="border-t pt-4 flex justify-end gap-3">
                <button
                  type="button"
                  onClick={() => { setModalOpen(false); resetForm(); }}
                  className="px-6 py-2 border rounded-lg hover:bg-gray-50"
                >
                  İptal
                </button>
                <button
                  type="submit"
                  className="px-6 py-2 bg-black text-white rounded-lg hover:bg-gray-800"
                >
                  {editingVendor ? 'Güncelle' : 'Oluştur'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
