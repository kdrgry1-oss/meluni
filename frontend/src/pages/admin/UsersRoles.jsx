import { useState, useEffect } from "react";
import axios from "axios";
import { toast } from "sonner";
import {
  Users, Shield, Plus, Trash2, Edit, Save, ChevronDown, ChevronRight, UserPlus,
  CheckCircle2, XCircle, Send, Mail, ShieldCheck,
} from "lucide-react";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from "../../components/ui/dialog";
import { useAuth } from "../../context/AuthContext";

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

// Telefon karşılaştırması: biçim farkı (boşluk, +90, 0) değişiklik sayılmaz — backend ile aynı kural.
const phoneKey = (v) => String(v || "").replace(/\D/g, "").slice(-10);

const hdr = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } });

const fmtDate = (iso) => {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("tr-TR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch { return iso; }
};

// Durum rozetleri — aynı boyut/boşluk her tabloda (CLAUDE.md simetri kuralı).
const BADGE = "inline-flex items-center px-2 py-0.5 rounded text-xs font-bold";
const APPROVAL_STATUS = {
  pending: { label: "Onay Bekliyor", cls: "bg-amber-100 text-amber-800" },
  executing: { label: "Uygulanıyor", cls: "bg-blue-100 text-blue-700" },
  executed: { label: "Onaylandı", cls: "bg-green-100 text-green-700" },
  rejected: { label: "Reddedildi", cls: "bg-red-100 text-red-700" },
  cancelled: { label: "İptal Edildi", cls: "bg-gray-200 text-gray-700" },
  expired: { label: "Süresi Doldu", cls: "bg-gray-200 text-gray-700" },
  failed: { label: "Başarısız", cls: "bg-red-100 text-red-700" },
};
const INVITE_STATUS = {
  pending: { label: "Bekliyor", cls: "bg-amber-100 text-amber-800" },
  accepted: { label: "Kabul Edildi", cls: "bg-green-100 text-green-700" },
  accepting: { label: "İşleniyor", cls: "bg-blue-100 text-blue-700" },
  revoked: { label: "İptal", cls: "bg-gray-200 text-gray-700" },
  expired: { label: "Süresi Doldu", cls: "bg-gray-200 text-gray-700" },
  locked: { label: "Kilitlendi", cls: "bg-red-100 text-red-700" },
};

// 202 = işlem ikinci süper admin onayına gönderildi (iki kişi kuralı).
const isPendingApproval = (res) => res?.status === 202 || res?.data?.pending_approval === true;

export default function AdminUsersRoles() {
  const { user: me } = useAuth();
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

  // Davet (ADM-01) + iki kişi onayı (BL-027) — yalnız süper admin görür (diğerlerine 403 → gizli).
  const [invites, setInvites] = useState([]);
  const [canManage, setCanManage] = useState(false);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteForm, setInviteForm] = useState({ email: "", first_name: "", last_name: "", phone: "", role_id: "" });
  const [sendingInvite, setSendingInvite] = useState(false);
  const [approvals, setApprovals] = useState([]);
  const [approvalMeta, setApprovalMeta] = useState({ pending: 0, me: "", active_super_admins: 0, two_person_disabled: false });
  const [busyApproval, setBusyApproval] = useState("");
  // Denetim #3: geçişin otomatik eklediği hassas izinler — süper admin kaldırır ya da korur.
  const [grantReview, setGrantReview] = useState([]);
  const [busyReview, setBusyReview] = useState("");

  useEffect(() => { fetchAll(); }, []);

  const fetchAll = async () => {
    setLoading(true);
    try {
      const [permRes, rolesRes, usersRes] = await Promise.all([
        axios.get(`${API}/admin/permissions`, hdr()),
        axios.get(`${API}/admin/roles`, hdr()),
        axios.get(`${API}/admin/users`, hdr()),
      ]);
      setPermTree(permRes.data.tree || []);
      setRoles(rolesRes.data.roles || []);
      setUsers(usersRes.data.users || []);
    } catch (err) {
      toast.error("Veriler yüklenemedi");
    } finally {
      setLoading(false);
    }
    const [invRes, apRes, revRes] = await Promise.allSettled([
      axios.get(`${API}/admin/users/invites`, hdr()),
      axios.get(`${API}/admin/approvals`, hdr()),
      axios.get(`${API}/admin/roles/autogrant-review`, hdr()),
    ]);
    setGrantReview(revRes.status === "fulfilled" ? (revRes.value.data?.items || []) : []);
    if (invRes.status === "fulfilled") {
      setInvites(invRes.value.data?.invites || []);
      setCanManage(true);
    } else {
      setCanManage(false);
    }
    if (apRes.status === "fulfilled") {
      const d = apRes.value.data || {};
      setApprovals(d.items || []);
      setApprovalMeta({ pending: d.pending || 0, me: d.me || "", active_super_admins: d.active_super_admins || 0,
                        two_person_disabled: !!d.two_person_disabled });
    }
  };

  const permLabel = (key) => {
    for (const g of permTree) {
      const c = (g.children || []).find((x) => x.key === key);
      if (c) return `${g.label} › ${c.label}`;
    }
    return key;
  };

  const resolveGrant = async (item, action) => {
    if (action === "revoke" && !window.confirm(`${item.role_name} rolünden ${item.permissions.length} izin kaldırılsın mı?`)) return;
    setBusyReview(item.role_id);
    try {
      const res = await axios.post(`${API}/admin/roles/autogrant-review`,
        { role_id: item.role_id, action, permissions: item.permissions }, hdr());
      if (isPendingApproval(res)) notifyPending(res);
      else toast.success(action === "revoke" ? "İzinler kaldırıldı" : "İzinler korunacak");
      fetchAll();
    } catch (err) {
      toast.error(err.response?.data?.detail || "İşlem yapılamadı");
    } finally {
      setBusyReview("");
    }
  };

  const notifyPending = (res) => {
    toast.info(res?.data?.message || "İşlem ikinci bir süper admin onayına gönderildi.");
  };

  const openInvite = () => {
    setInviteForm({ email: "", first_name: "", last_name: "", phone: "", role_id: roles.find((r) => r.id !== "super_admin")?.id || "" });
    setInviteOpen(true);
  };

  const sendInvite = async (e) => {
    e?.preventDefault?.();
    if (!inviteForm.email || !inviteForm.role_id) { toast.error("E-posta ve rol gerekli"); return; }
    setSendingInvite(true);
    try {
      const res = await axios.post(`${API}/admin/users/invite`, inviteForm, hdr());
      if (isPendingApproval(res)) {
        notifyPending(res);
      } else if (res.data?.invite?.email_sent === false) {
        toast.warning("Davet oluşturuldu ancak e-posta gönderilemedi — e-posta ayarlarını kontrol edip yeniden gönderin.");
      } else {
        toast.success("Davet e-postası gönderildi (48 saat geçerli)");
      }
      setInviteOpen(false);
      fetchAll();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Davet gönderilemedi");
    } finally {
      setSendingInvite(false);
    }
  };

  const resendInvite = async (inv) => {
    try {
      const res = await axios.post(`${API}/admin/users/invites/${inv.id}/resend`, {}, hdr());
      if (isPendingApproval(res)) notifyPending(res);
      else toast.success("Davet yeniden gönderildi");
      fetchAll();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Gönderilemedi");
    }
  };

  const revokeInvite = async (inv) => {
    if (!await window.appConfirm(`"${inv.email}" davetini iptal etmek istiyor musunuz?`)) return;
    try {
      await axios.delete(`${API}/admin/users/invites/${inv.id}`, hdr());
      toast.success("Davet iptal edildi");
      fetchAll();
    } catch (err) {
      toast.error(err.response?.data?.detail || "İptal edilemedi");
    }
  };

  const decideApproval = async (ap, decision) => {
    if (decision === "approve"
      && !await window.appConfirm(`Bu işlemi onaylıyor musunuz?\n\n${ap.summary}`)) return;
    if (decision !== "approve"
      && !await window.appConfirm(ap.requested_by?.id === approvalMeta.me ? "Talebi geri çekmek istiyor musunuz?" : "Talebi reddetmek istiyor musunuz?")) return;
    setBusyApproval(ap.id);
    try {
      await axios.post(`${API}/admin/approvals/${ap.id}/${decision}`, {}, hdr());
      toast.success(decision === "approve" ? "Onaylandı ve uygulandı" : "Talep kapatıldı");
      fetchAll();
    } catch (err) {
      toast.error(err.response?.data?.detail || "İşlem yapılamadı");
    } finally {
      setBusyApproval("");
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
      const res = editingRole
        ? await axios.put(`${API}/admin/roles/${editingRole.id}`, body, hdr)
        : await axios.post(`${API}/admin/roles`, body, hdr);
      if (isPendingApproval(res)) notifyPending(res);
      else toast.success(editingRole ? "Rol güncellendi" : "Rol oluşturuldu");
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
      const res = await axios.delete(`${API}/admin/roles/${role.id}`, { headers: { Authorization: `Bearer ${token}` } });
      if (isPendingApproval(res)) notifyPending(res);
      else toast.success("Rol silindi");
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
      current_password: "",
    });
    setUserModalOpen(true);
  };

  const selfPhoneChanged = !!(editingUser && me && editingUser.id === me.id
    && phoneKey(userForm.phone) !== phoneKey(editingUser.phone));

  const saveUser = async (e) => {
    e?.preventDefault?.();
    setSavingUser(true);
    try {
      const token = localStorage.getItem("token");
      const hdr = { headers: { Authorization: `Bearer ${token}` } };
      if (editingUser) {
        const payload = { ...userForm };
        if (!payload.password) delete payload.password;
        if (!payload.current_password) delete payload.current_password;
        const res = await axios.put(`${API}/admin/users/${editingUser.id}`, payload, hdr);
        if (isPendingApproval(res)) notifyPending(res);
        else toast.success("Kullanıcı güncellendi");
      } else {
        if (!userForm.email || !userForm.password) { toast.error("E-posta ve parola gerekli"); setSavingUser(false); return; }
        const res = await axios.post(`${API}/admin/users`, userForm, hdr);
        if (isPendingApproval(res)) notifyPending(res);
        else toast.success("Kullanıcı oluşturuldu");
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
      const res = await axios.delete(`${API}/admin/users/${user.id}`, { headers: { Authorization: `Bearer ${token}` } });
      if (isPendingApproval(res)) notifyPending(res);
      else toast.success("Kullanıcı silindi");
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
        {canManage && (
          <button
            onClick={() => setTab("approvals")}
            data-testid="tab-approvals"
            className={`px-6 py-3 text-sm font-bold border-b-2 transition-colors ${tab === "approvals" ? "border-indigo-600 text-indigo-600" : "border-transparent text-gray-500 hover:text-gray-700"}`}
          >
            <ShieldCheck size={14} className="inline mr-2" /> Onay Bekleyenler ({approvalMeta.pending})
          </button>
        )}
      </div>

      {loading ? (
        <div className="text-center py-20 text-gray-500">Yükleniyor...</div>
      ) : tab === "roles" ? (
        <div className="space-y-4">
          {grantReview.length > 0 && (
            <div className="bg-amber-50 border border-amber-200 rounded-xl p-4" data-testid="autogrant-review">
              <div className="flex items-center gap-2 text-sm font-bold text-amber-900">
                <ShieldCheck size={16} /> Gözden geçirilmesi gereken izinler
              </div>
              <p className="text-xs text-amber-800 mt-1">
                Bir sistem güncellemesi bu hassas izinleri rollere otomatik eklemişti. Rolün gerçekten ihtiyacı yoksa kaldırın; varsa koruyun (bir daha sorulmaz).
              </p>
              <div className="mt-3 space-y-3">
                {grantReview.map((item) => (
                  <div key={item.role_id} className="bg-white border border-amber-200 rounded-lg p-3" data-testid={`autogrant-${item.role_id}`}>
                    <div className="flex items-center justify-between gap-4">
                      <span className="text-sm font-bold text-gray-900">{item.role_name}</span>
                      <div className="flex items-center gap-2">
                        <button disabled={busyReview === item.role_id} onClick={() => resolveGrant(item, "revoke")}
                          className="px-3 py-1.5 rounded-lg text-xs font-bold bg-red-600 text-white hover:bg-red-700 disabled:opacity-50">Kaldır</button>
                        <button disabled={busyReview === item.role_id} onClick={() => resolveGrant(item, "keep")}
                          className="px-3 py-1.5 rounded-lg text-xs font-bold bg-gray-100 text-gray-700 hover:bg-gray-200 disabled:opacity-50">Koru</button>
                      </div>
                    </div>
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      {item.permissions.map((k) => (
                        <span key={k} className="inline-flex items-center px-2 py-0.5 rounded bg-amber-100 text-amber-900 text-xs">{permLabel(k)}</span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
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
      ) : tab === "approvals" ? (
        <div className="space-y-4" data-testid="approvals-panel">
          <div className="bg-white rounded-xl border shadow-sm p-4 text-sm text-gray-600">
            Tüm yetkili ('*') rol/kullanıcı değişiklikleri ve banka hesabı (IBAN) değişiklikleri, birden fazla aktif
            süper admin varsa <b>başka bir süper admin</b> onaylayana kadar uygulanmaz. Talepler 24 saat geçerlidir.
            Onaylamak için son 5 dakika içinde giriş yapmış olmanız gerekir.
            {approvalMeta.two_person_disabled && (
              <span className="block mt-2 text-red-600 font-medium">Acil kapatma (TWO_PERSON_RULE=off) aktif: işlemler onaysız uygulanıyor.</span>
            )}
          </div>
          <div className="bg-white rounded-xl border shadow-sm overflow-x-auto">
            <table className="w-full">
              <thead className="border-b bg-gray-50">
                <tr>
                  <th className="text-left px-4 py-3 text-xs font-bold text-gray-500 uppercase">İşlem</th>
                  <th className="text-left px-4 py-3 text-xs font-bold text-gray-500 uppercase">Talep Eden</th>
                  <th className="text-left px-4 py-3 text-xs font-bold text-gray-500 uppercase">Tarih</th>
                  <th className="text-left px-4 py-3 text-xs font-bold text-gray-500 uppercase">Durum</th>
                  <th className="px-4 py-3"></th>
                </tr>
              </thead>
              <tbody>
                {approvals.length === 0 ? (
                  <tr><td colSpan={5} className="px-4 py-10 text-center text-sm text-gray-400">Onay bekleyen işlem yok</td></tr>
                ) : approvals.map((ap) => {
                  const st = APPROVAL_STATUS[ap.status] || APPROVAL_STATUS.pending;
                  const mine = ap.requested_by?.id === approvalMeta.me;
                  const decidedBy = ap.approved_by?.email || ap.rejected_by?.email || "";
                  return (
                    <tr key={ap.id} className="border-b hover:bg-gray-50 align-middle" data-testid={`approval-row-${ap.id}`}>
                      <td className="px-4 py-3">
                        <div className="text-sm font-medium text-gray-900">{ap.label || ap.action}</div>
                        <div className="text-xs text-gray-500 mt-0.5">{ap.summary}</div>
                      </td>
                      <td className="px-4 py-3 text-sm">{ap.requested_by?.email || "—"}</td>
                      <td className="px-4 py-3 text-sm tabular-nums whitespace-nowrap">{fmtDate(ap.created_at)}</td>
                      <td className="px-4 py-3">
                        <span className={`${BADGE} ${st.cls}`}>{st.label}</span>
                        {decidedBy && <div className="text-xs text-gray-500 mt-1">{decidedBy}</div>}
                        {ap.error && <div className="text-xs text-red-600 mt-1">{ap.error}</div>}
                      </td>
                      <td className="px-4 py-3 text-right whitespace-nowrap">
                        {ap.status === "pending" && !mine && (
                          <button onClick={() => decideApproval(ap, "approve")} disabled={busyApproval === ap.id}
                            data-testid={`approve-${ap.id}`}
                            className="inline-flex items-center gap-1 px-3 py-1.5 text-green-700 hover:bg-green-50 rounded text-xs font-medium disabled:opacity-50">
                            <CheckCircle2 size={13} /> Onayla
                          </button>
                        )}
                        {ap.status === "pending" && (
                          <button onClick={() => decideApproval(ap, "reject")} disabled={busyApproval === ap.id}
                            data-testid={`reject-${ap.id}`}
                            className="inline-flex items-center gap-1 px-3 py-1.5 text-red-600 hover:bg-red-50 rounded text-xs font-medium disabled:opacity-50">
                            <XCircle size={13} /> {mine ? "Geri Çek" : "Reddet"}
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <div className="flex justify-end">
            <button onClick={canManage ? openInvite : openCreateUser} data-testid="create-user-btn"
              className="flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-bold hover:bg-indigo-700">
              <UserPlus size={16} /> {canManage ? "Personel Davet Et" : "Yeni Kullanıcı"}
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

          {canManage && invites.length > 0 && (
            <div className="bg-white rounded-xl border shadow-sm overflow-x-auto" data-testid="invites-table">
              <div className="px-4 py-3 border-b bg-gray-50 text-xs font-bold text-gray-500 uppercase flex items-center gap-2">
                <Mail size={13} /> Davetler
              </div>
              <table className="w-full">
                <thead className="border-b">
                  <tr>
                    <th className="text-left px-4 py-3 text-xs font-bold text-gray-500 uppercase">E-posta</th>
                    <th className="text-left px-4 py-3 text-xs font-bold text-gray-500 uppercase">Rol</th>
                    <th className="text-left px-4 py-3 text-xs font-bold text-gray-500 uppercase">Doğrulama</th>
                    <th className="text-left px-4 py-3 text-xs font-bold text-gray-500 uppercase">Durum</th>
                    <th className="text-left px-4 py-3 text-xs font-bold text-gray-500 uppercase">Son Geçerlilik</th>
                    <th className="px-4 py-3"></th>
                  </tr>
                </thead>
                <tbody>
                  {invites.map((inv) => {
                    const st = INVITE_STATUS[inv.status] || INVITE_STATUS.pending;
                    const open = ["pending", "expired", "locked"].includes(inv.status);
                    return (
                      <tr key={inv.id} className="border-b hover:bg-gray-50" data-testid={`invite-row-${inv.id}`}>
                        <td className="px-4 py-3 font-medium text-sm">{inv.email}</td>
                        <td className="px-4 py-3 text-sm">{inv.role_name || roleLabel(inv.role_id)}</td>
                        <td className="px-4 py-3 text-sm">{inv.mfa_method === "sms" ? `SMS (***${inv.phone_tail || ""})` : "Authenticator"}</td>
                        <td className="px-4 py-3"><span className={`${BADGE} ${st.cls}`}>{st.label}</span></td>
                        <td className="px-4 py-3 text-sm tabular-nums whitespace-nowrap">{fmtDate(inv.expires_at)}</td>
                        <td className="px-4 py-3 text-right whitespace-nowrap">
                          {open && (
                            <button onClick={() => resendInvite(inv)}
                              className="inline-flex items-center gap-1 px-3 py-1.5 text-indigo-600 hover:bg-indigo-50 rounded text-xs font-medium">
                              <Send size={13} /> Yeniden Gönder
                            </button>
                          )}
                          {open && (
                            <button onClick={() => revokeInvite(inv)}
                              className="inline-flex items-center gap-1 px-3 py-1.5 text-red-600 hover:bg-red-50 rounded text-xs font-medium">
                              <Trash2 size={13} /> İptal
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Invite Modal (ADM-01) */}
      <Dialog open={inviteOpen} onOpenChange={setInviteOpen}>
        <DialogContent data-testid="invite-modal">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <UserPlus size={18} className="text-indigo-600" /> Personel Davet Et
            </DialogTitle>
          </DialogHeader>
          <form onSubmit={sendInvite} className="space-y-3">
            <p className="text-xs text-gray-500">
              Davetli e-postadaki bağlantıyla kendi şifresini belirler ve iki adımlı doğrulamayı (MFA) kurar; ancak
              ondan sonra panele girebilir. Bağlantı 48 saat geçerli ve tek kullanımlıktır.
            </p>
            <div>
              <label className="block text-sm font-medium mb-1">E-posta <span className="text-red-500">*</span></label>
              <input type="email" required value={inviteForm.email}
                onChange={(e) => setInviteForm({ ...inviteForm, email: e.target.value })}
                data-testid="invite-email-input" className="w-full border px-3 py-2 rounded text-sm" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-sm font-medium mb-1">Ad</label>
                <input value={inviteForm.first_name}
                  onChange={(e) => setInviteForm({ ...inviteForm, first_name: e.target.value })}
                  className="w-full border px-3 py-2 rounded text-sm" />
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">Soyad</label>
                <input value={inviteForm.last_name}
                  onChange={(e) => setInviteForm({ ...inviteForm, last_name: e.target.value })}
                  className="w-full border px-3 py-2 rounded text-sm" />
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Giriş Doğrulama Telefonu (SMS — isteğe bağlı)</label>
              <input type="tel" placeholder="05xx xxx xx xx" value={inviteForm.phone}
                onChange={(e) => setInviteForm({ ...inviteForm, phone: e.target.value })}
                className="w-full border px-3 py-2 rounded text-sm" />
              <p className="text-xs text-gray-500 mt-1">Girilirse doğrulama kodu bu numaraya SMS ile gider (kullanıcı değiştiremez). Boş bırakılırsa davetli Authenticator uygulaması kurar.</p>
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Rol <span className="text-red-500">*</span></label>
              <select value={inviteForm.role_id} onChange={(e) => setInviteForm({ ...inviteForm, role_id: e.target.value })}
                data-testid="invite-role-select" className="w-full border px-3 py-2 rounded text-sm bg-white">
                <option value="">— Rol Seç —</option>
                {roles.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
              </select>
              {roles.find((r) => r.id === inviteForm.role_id)?.permissions?.includes("*") && (
                <p className="text-xs text-amber-700 mt-1">Tüm yetkili rol: başka bir süper admin onaylayana kadar davet gönderilmez.</p>
              )}
            </div>
            <div className="flex justify-end gap-2 pt-4 border-t">
              <button type="button" onClick={() => setInviteOpen(false)} className="px-4 py-2 border rounded hover:bg-gray-50 text-sm">İptal</button>
              <button type="submit" disabled={sendingInvite} data-testid="send-invite-btn"
                className="inline-flex items-center gap-1.5 px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 text-sm font-bold">
                <Send size={14} /> {sendingInvite ? "Gönderiliyor..." : "Davet Gönder"}
              </button>
            </div>
          </form>
        </DialogContent>
      </Dialog>

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
            {selfPhoneChanged && (
              <div>
                <label className="block text-sm font-medium mb-1">Mevcut Parolanız <span className="text-red-500">*</span></label>
                <input type="password" required autoComplete="current-password" value={userForm.current_password || ""}
                  onChange={e => setUserForm({ ...userForm, current_password: e.target.value })}
                  data-testid="user-current-password-input"
                  className="w-full border px-3 py-2 rounded text-sm" />
                <p className="text-xs text-gray-500 mt-1">Kendi giriş doğrulama telefonunuzu değiştirmek için mevcut parolanızı girin.</p>
              </div>
            )}
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
