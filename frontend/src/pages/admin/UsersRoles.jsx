import { useState, useEffect } from "react";
import axios from "axios";
import { toast } from "sonner";
import {
  Users, Shield, Plus, Trash2, Edit, Save, X, ChevronDown, ChevronRight, UserPlus, Key
} from "lucide-react";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from "../../components/ui/dialog";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

function PermissionTree({ tree, selected, onChange, disabled }) {
  const [expanded, setExpanded] = useState(() => {
    // By default expand all top-level groups
    const init = {};
    tree.forEach(n => { init[n.key] = true; });
    return init;
  });

  const toggle = (key) => setExpanded(prev => ({ ...prev, [key]: !prev[key] }));

  const collectLeafKeys = (node) => {
    if (!node.children) return [node.key];
    return node.children.flatMap(collectLeafKeys);
  };

  const isChecked = (key) => selected.includes(key) || selected.includes("*");
  const isGroupChecked = (node) => {
    const leaves = collectLeafKeys(node);
    if (leaves.length === 0) return false;
    return leaves.every(k => isChecked(k));
  };
  const isGroupPartial = (node) => {
    const leaves = collectLeafKeys(node);
    const some = leaves.some(k => isChecked(k));
    return some && !isGroupChecked(node);
  };

  const toggleNode = (node, checked) => {
    if (disabled || selected.includes("*")) return;
    const leaves = collectLeafKeys(node);
    let next = [...selected];
    if (checked) {
      leaves.forEach(k => { if (!next.includes(k)) next.push(k); });
    } else {
      next = next.filter(k => !leaves.includes(k));
    }
    onChange(next);
  };

  const renderNode = (node, level = 0) => {
    const hasChildren = !!node.children;
    const checked = hasChildren ? isGroupChecked(node) : isChecked(node.key);
    const partial = hasChildren ? isGroupPartial(node) : false;
    return (
      <div key={node.key} className="select-none">
        <div
          className={`flex items-center gap-2 py-1.5 rounded hover:bg-gray-50 ${level === 0 ? 'font-bold bg-gray-50 px-2' : 'pl-2'}`}
          style={{ marginLeft: level * 16 }}
          data-testid={`perm-node-${node.key}`}
        >
          {hasChildren ? (
            <button type="button" onClick={() => toggle(node.key)} className="text-gray-400 hover:text-gray-600">
              {expanded[node.key] ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            </button>
          ) : (
            <span className="w-3.5" />
          )}
          <label className="flex items-center gap-2 cursor-pointer flex-1">
            <input
              type="checkbox"
              disabled={disabled}
              checked={checked}
              ref={el => { if (el) el.indeterminate = partial; }}
              onChange={(e) => hasChildren ? toggleNode(node, e.target.checked) :
                (e.target.checked
                  ? onChange([...selected, node.key])
                  : onChange(selected.filter(k => k !== node.key)))
              }
              className="rounded"
              data-testid={`perm-checkbox-${node.key}`}
            />
            <span className={`text-sm ${level === 0 ? 'uppercase tracking-wider text-gray-700' : 'text-gray-700'}`}>
              {node.label}
            </span>
            {level > 0 && (
              <span className="ml-auto text-[10px] text-gray-400 font-mono">{node.key}</span>
            )}
          </label>
        </div>
        {hasChildren && expanded[node.key] && (
          <div>{node.children.map(c => renderNode(c, level + 1))}</div>
        )}
      </div>
    );
  };

  return <div className="space-y-1">{tree.map(n => renderNode(n))}</div>;
}

export default function AdminUsersRoles() {
  const [tab, setTab] = useState("roles"); // 'roles' | 'users'
  const [permTree, setPermTree] = useState([]);
  const [roles, setRoles] = useState([]);
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);

  // Role edit modal
  const [roleModalOpen, setRoleModalOpen] = useState(false);
  const [editingRole, setEditingRole] = useState(null);
  const [rolePerms, setRolePerms] = useState([]);
  const [roleName, setRoleName] = useState("");
  const [roleDesc, setRoleDesc] = useState("");
  const [savingRole, setSavingRole] = useState(false);

  // User modal
  const [userModalOpen, setUserModalOpen] = useState(false);
  const [editingUser, setEditingUser] = useState(null);
  const [userForm, setUserForm] = useState({ email: "", password: "", first_name: "", last_name: "", phone: "", role_id: "", is_active: true });
  const [savingUser, setSavingUser] = useState(false);

  useEffect(() => { fetchAll(); }, []);

  const fetchAll = async () => {
    setLoading(true);
    try {
      const token = localStorage.getItem("token");
      const hdr = { headers: { Authorization: `Bearer ${token}` } };
      const [permRes, rolesRes, usersRes] = await Promise.all([
        axios.get(`${API}/admin/permissions`, hdr),
        axios.get(`${API}/admin/roles`, hdr),
        axios.get(`${API}/admin/users`, hdr),
      ]);
      setPermTree(permRes.data.tree || []);
      setRoles(rolesRes.data.roles || []);
      setUsers(usersRes.data.users || []);
    } catch (err) {
      toast.error("Veriler yüklenemedi");
    } finally {
      setLoading(false);
    }
  };

  const openCreateRole = () => {
    setEditingRole(null);
    setRoleName("");
    setRoleDesc("");
    setRolePerms([]);
    setRoleModalOpen(true);
  };

  const openEditRole = (role) => {
    setEditingRole(role);
    setRoleName(role.name);
    setRoleDesc(role.description || "");
    setRolePerms(role.permissions || []);
    setRoleModalOpen(true);
  };

  const saveRole = async (e) => {
    e?.preventDefault?.();
    if (!roleName.trim()) { toast.error("Rol adı gerekli"); return; }
    setSavingRole(true);
    try {
      const token = localStorage.getItem("token");
      const hdr = { headers: { Authorization: `Bearer ${token}` } };
      const body = { name: roleName.trim(), description: roleDesc.trim(), permissions: rolePerms };
      if (editingRole) {
        await axios.put(`${API}/admin/roles/${editingRole.id}`, body, hdr);
        toast.success("Rol güncellendi");
      } else {
        await axios.post(`${API}/admin/roles`, body, hdr);
        toast.success("Rol oluşturuldu");
      }
      setRoleModalOpen(false);
      fetchAll();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Kaydedilemedi");
    } finally {
      setSavingRole(false);
    }
  };

  const deleteRole = async (role) => {
    if (role.is_system) { toast.error("Sistem rolleri silinemez"); return; }
    if (!await window.appConfirm(`"${role.name}" rolünü silmek istiyor musunuz?`)) return;
    try {
      const token = localStorage.getItem("token");
      await axios.delete(`${API}/admin/roles/${role.id}`, { headers: { Authorization: `Bearer ${token}` } });
      toast.success("Rol silindi");
      fetchAll();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Silinemedi");
    }
  };

  const openCreateUser = () => {
    setEditingUser(null);
    setUserForm({ email: "", password: "", first_name: "", last_name: "", phone: "", role_id: roles[0]?.id || "", is_active: true });
    setUserModalOpen(true);
  };

  const openEditUser = (user) => {
    setEditingUser(user);
    setUserForm({
      email: user.email, password: "",
      first_name: user.first_name || "", last_name: user.last_name || "",
      phone: user.phone || "",
      role_id: user.role_id || "", is_active: !!user.is_active,
    });
    setUserModalOpen(true);
  };

  const saveUser = async (e) => {
    e?.preventDefault?.();
    setSavingUser(true);
    try {
      const token = localStorage.getItem("token");
      const hdr = { headers: { Authorization: `Bearer ${token}` } };
      if (editingUser) {
        const payload = { ...userForm };
        if (!payload.password) delete payload.password;
        await axios.put(`${API}/admin/users/${editingUser.id}`, payload, hdr);
        toast.success("Kullanıcı güncellendi");
      } else {
        if (!userForm.email || !userForm.password) { toast.error("E-posta ve parola gerekli"); setSavingUser(false); return; }
        await axios.post(`${API}/admin/users`, userForm, hdr);
        toast.success("Kullanıcı oluşturuldu");
      }
      setUserModalOpen(false);
      fetchAll();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Kaydedilemedi");
    } finally {
      setSavingUser(false);
    }
  };

  const deleteUser = async (user) => {
    if (!await window.appConfirm(`"${user.email}" kullanıcısını silmek istiyor musunuz?`)) return;
    try {
      const token = localStorage.getItem("token");
      await axios.delete(`${API}/admin/users/${user.id}`, { headers: { Authorization: `Bearer ${token}` } });
      toast.success("Kullanıcı silindi");
      fetchAll();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Silinemedi");
    }
  };

  const roleLabel = (role_id) => roles.find(r => r.id === role_id)?.name || "—";

  return (
    <div className="p-6 max-w-6xl mx-auto" data-testid="admin-users-roles-page">
      <div className="flex justify-between items-center mb-6">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Shield className="text-indigo-600" /> Kullanıcılar & Roller
          </h1>
          <p className="text-sm text-gray-500 mt-1">Admin paneline erişecek kullanıcıları ve her birinin sayfa/aksiyon bazlı yetkilerini yönetin.</p>
        </div>
      </div>

      <div className="flex border-b mb-6">
        <button
          onClick={() => setTab("roles")}
          data-testid="tab-roles"
          className={`px-6 py-3 text-sm font-bold border-b-2 transition-colors ${tab === "roles" ? "border-indigo-600 text-indigo-600" : "border-transparent text-gray-500 hover:text-gray-700"}`}
        >
          <Shield size={14} className="inline mr-2" /> Roller ({roles.length})
        </button>
        <button
          onClick={() => setTab("users")}
          data-testid="tab-users"
          className={`px-6 py-3 text-sm font-bold border-b-2 transition-colors ${tab === "users" ? "border-indigo-600 text-indigo-600" : "border-transparent text-gray-500 hover:text-gray-700"}`}
        >
          <Users size={14} className="inline mr-2" /> Kullanıcılar ({users.length})
        </button>
      </div>

      {loading ? (
        <div className="text-center py-20 text-gray-500">Yükleniyor...</div>
      ) : tab === "roles" ? (
        <div className="space-y-4">
          <div className="flex justify-end">
            <button onClick={openCreateRole} data-testid="create-role-btn"
              className="flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-bold hover:bg-indigo-700">
              <Plus size={16} /> Yeni Rol
            </button>
          </div>
          <div className="bg-white rounded-xl border shadow-sm">
            <table className="w-full">
              <thead className="border-b bg-gray-50">
                <tr>
                  <th className="text-left px-4 py-3 text-xs font-bold text-gray-500 uppercase">Rol</th>
                  <th className="text-left px-4 py-3 text-xs font-bold text-gray-500 uppercase">Açıklama</th>
                  <th className="text-left px-4 py-3 text-xs font-bold text-gray-500 uppercase">Yetki Sayısı</th>
                  <th className="text-left px-4 py-3 text-xs font-bold text-gray-500 uppercase">Tip</th>
                  <th className="px-4 py-3"></th>
                </tr>
              </thead>
              <tbody>
                {roles.map(r => (
                  <tr key={r.id} className="border-b hover:bg-gray-50" data-testid={`role-row-${r.id}`}>
                    <td className="px-4 py-3 font-medium text-gray-900">{r.name}</td>
                    <td className="px-4 py-3 text-sm text-gray-500">{r.description || '—'}</td>
                    <td className="px-4 py-3 text-sm">
                      {r.permissions?.includes("*") ? (
                        <span className="px-2 py-0.5 rounded bg-indigo-100 text-indigo-700 text-xs font-bold">Tüm Yetkiler</span>
                      ) : (
                        <span className="font-mono">{r.permissions?.length || 0}</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      {r.is_system ? (
                        <span className="px-2 py-0.5 rounded bg-gray-200 text-gray-700 text-xs font-bold">Sistem</span>
                      ) : (
                        <span className="px-2 py-0.5 rounded bg-green-100 text-green-700 text-xs font-bold">Özel</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <button onClick={() => openEditRole(r)} className="px-3 py-1.5 text-indigo-600 hover:bg-indigo-50 rounded text-xs font-medium" data-testid={`edit-role-${r.id}`}>
                        <Edit size={13} className="inline mr-1" /> Düzenle
                      </button>
                      {!r.is_system && (
                        <button onClick={() => deleteRole(r)} className="px-3 py-1.5 text-red-600 hover:bg-red-50 rounded text-xs font-medium" data-testid={`del-role-${r.id}`}>
                          <Trash2 size={13} className="inline mr-1" /> Sil
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <div className="flex justify-end">
            <button onClick={openCreateUser} data-testid="create-user-btn"
              className="flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-bold hover:bg-indigo-700">
              <UserPlus size={16} /> Yeni Kullanıcı
            </button>
          </div>
          <div className="bg-white rounded-xl border shadow-sm">
            <table className="w-full">
              <thead className="border-b bg-gray-50">
                <tr>
                  <th className="text-left px-4 py-3 text-xs font-bold text-gray-500 uppercase">E-posta</th>
                  <th className="text-left px-4 py-3 text-xs font-bold text-gray-500 uppercase">Ad Soyad</th>
                  <th className="text-left px-4 py-3 text-xs font-bold text-gray-500 uppercase">Rol</th>
                  <th className="text-left px-4 py-3 text-xs font-bold text-gray-500 uppercase">Durum</th>
                  <th className="px-4 py-3"></th>
                </tr>
              </thead>
              <tbody>
                {users.map(u => (
                  <tr key={u.id} className="border-b hover:bg-gray-50" data-testid={`user-row-${u.id}`}>
                    <td className="px-4 py-3 font-medium">{u.email}</td>
                    <td className="px-4 py-3 text-sm">{(u.first_name || '') + ' ' + (u.last_name || '')}</td>
                    <td className="px-4 py-3 text-sm">{roleLabel(u.role_id)}</td>
                    <td className="px-4 py-3 text-sm">
                      {u.is_active ? (
                        <span className="px-2 py-0.5 rounded bg-green-100 text-green-700 text-xs font-bold">Aktif</span>
                      ) : (
                        <span className="px-2 py-0.5 rounded bg-red-100 text-red-700 text-xs font-bold">Pasif</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <button onClick={() => openEditUser(u)} className="px-3 py-1.5 text-indigo-600 hover:bg-indigo-50 rounded text-xs font-medium" data-testid={`edit-user-${u.id}`}>
                        <Edit size={13} className="inline mr-1" /> Düzenle
                      </button>
                      <button onClick={() => deleteUser(u)} className="px-3 py-1.5 text-red-600 hover:bg-red-50 rounded text-xs font-medium" data-testid={`del-user-${u.id}`}>
                        <Trash2 size={13} className="inline mr-1" /> Sil
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Role Edit Modal */}
      <Dialog open={roleModalOpen} onOpenChange={setRoleModalOpen}>
        <DialogContent className="max-w-3xl max-h-[85vh] overflow-y-auto" data-testid="role-modal">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Shield size={18} className="text-indigo-600" />
              {editingRole ? `Rolü Düzenle: ${editingRole.name}` : "Yeni Rol Oluştur"}
            </DialogTitle>
          </DialogHeader>
          <form onSubmit={saveRole} className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium mb-1">Rol Adı <span className="text-red-500">*</span></label>
                <input
                  value={roleName}
                  onChange={(e) => setRoleName(e.target.value)}
                  required
                  disabled={editingRole?.is_system}
                  data-testid="role-name-input"
                  className="w-full border px-3 py-2 rounded text-sm"
                />
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">Açıklama</label>
                <input
                  value={roleDesc}
                  onChange={(e) => setRoleDesc(e.target.value)}
                  className="w-full border px-3 py-2 rounded text-sm"
                />
              </div>
            </div>

            {editingRole?.id === "super_admin" && (
              <div className="bg-amber-50 border border-amber-200 p-3 rounded text-xs text-amber-800">
                <b>Süper Admin</b> rolü düzenlenemez. Tüm yetkiler varsayılan olarak dahildir.
              </div>
            )}

            <div>
              <div className="flex justify-between items-center mb-2">
                <label className="text-sm font-bold">Yetki Ağacı</label>
                <div className="flex gap-2">
                  <button type="button" onClick={() => setRolePerms(["*"])}
                    disabled={editingRole?.id === "super_admin"}
                    className="text-xs px-2 py-1 bg-indigo-50 text-indigo-600 rounded hover:bg-indigo-100 disabled:opacity-50"
                    data-testid="select-all-perms">Tümünü Seç</button>
                  <button type="button" onClick={() => setRolePerms([])}
                    disabled={editingRole?.id === "super_admin"}
                    className="text-xs px-2 py-1 bg-gray-50 text-gray-600 rounded hover:bg-gray-100 disabled:opacity-50"
                    data-testid="clear-all-perms">Hiçbirini Seçme</button>
                </div>
              </div>
              <div className="border rounded-lg p-3 max-h-[400px] overflow-y-auto bg-white">
                <PermissionTree
                  tree={permTree}
                  selected={rolePerms}
                  onChange={setRolePerms}
                  disabled={editingRole?.id === "super_admin"}
                />
              </div>
              <p className="text-xs text-gray-500 mt-1">
                {rolePerms.includes("*") ? "Tüm yetkiler aktif" : `${rolePerms.length} yetki seçili`}
              </p>
            </div>

            <div className="flex justify-end gap-2 pt-4 border-t">
              <button type="button" onClick={() => setRoleModalOpen(false)} className="px-4 py-2 border rounded hover:bg-gray-50 text-sm">İptal</button>
              <button type="submit" disabled={savingRole || editingRole?.id === "super_admin"} data-testid="save-role-btn"
                className="px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 text-sm font-bold">
                <Save size={14} className="inline mr-1" /> {savingRole ? "Kaydediliyor..." : "Kaydet"}
              </button>
            </div>
          </form>
        </DialogContent>
      </Dialog>

      {/* User Modal */}
      <Dialog open={userModalOpen} onOpenChange={setUserModalOpen}>
        <DialogContent data-testid="user-modal">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Users size={18} className="text-indigo-600" />
              {editingUser ? `Kullanıcıyı Düzenle: ${editingUser.email}` : "Yeni Kullanıcı"}
            </DialogTitle>
          </DialogHeader>
          <form onSubmit={saveUser} className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-sm font-medium mb-1">E-posta <span className="text-red-500">*</span></label>
                <input type="email" required disabled={!!editingUser} value={userForm.email}
                  onChange={e => setUserForm({ ...userForm, email: e.target.value })}
                  data-testid="user-email-input"
                  className="w-full border px-3 py-2 rounded text-sm" />
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">
                  {editingUser ? "Yeni Parola (boş bırakırsanız değişmez)" : "Parola"} {!editingUser && <span className="text-red-500">*</span>}
                </label>
                <input type="password" required={!editingUser} value={userForm.password}
                  onChange={e => setUserForm({ ...userForm, password: e.target.value })}
                  data-testid="user-password-input"
                  className="w-full border px-3 py-2 rounded text-sm" />
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">Ad</label>
                <input value={userForm.first_name}
                  onChange={e => setUserForm({ ...userForm, first_name: e.target.value })}
                  className="w-full border px-3 py-2 rounded text-sm" />
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">Soyad</label>
                <input value={userForm.last_name}
                  onChange={e => setUserForm({ ...userForm, last_name: e.target.value })}
                  className="w-full border px-3 py-2 rounded text-sm" />
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Giriş Doğrulama Telefonu (SMS)</label>
              <input value={userForm.phone} type="tel" placeholder="05xx xxx xx xx"
                onChange={e => setUserForm({ ...userForm, phone: e.target.value })}
                className="w-full border px-3 py-2 rounded text-sm" />
              <p className="text-xs text-gray-500 mt-1">Giriş doğrulama kodu (SMS) bu numaraya gider. Yalnız yönetici belirler; kullanıcı kendisi değiştiremez. Boş bırakılırsa yönetici-kilidi kalkar.</p>
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Rol <span className="text-red-500">*</span></label>
              <select value={userForm.role_id}
                onChange={e => setUserForm({ ...userForm, role_id: e.target.value })}
                data-testid="user-role-select"
                className="w-full border px-3 py-2 rounded text-sm bg-white">
                <option value="">— Rol Seç —</option>
                {roles.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}
              </select>
            </div>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={userForm.is_active}
                onChange={e => setUserForm({ ...userForm, is_active: e.target.checked })} />
              <span className="text-sm">Aktif</span>
            </label>
            <div className="flex justify-end gap-2 pt-4 border-t">
              <button type="button" onClick={() => setUserModalOpen(false)} className="px-4 py-2 border rounded hover:bg-gray-50 text-sm">İptal</button>
              <button type="submit" disabled={savingUser} data-testid="save-user-btn"
                className="px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 text-sm font-bold">
                <Save size={14} className="inline mr-1" /> {savingUser ? "Kaydediliyor..." : "Kaydet"}
              </button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
