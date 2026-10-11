// KVKK: TCKN/VKN sunucuda şifreli saklanır; listeler/müşteri ekranları yalnız son 2 haneyi
// "*********12" biçiminde döndürür. Bu yardımcılar maskeli değeri ekranda güvenle göstermeyi ve
// düzenleme formunda "değiştirilmedi" ile "yeni değer yazıldı"yı ayırmayı sağlar. Maskeli değer
// sunucuya aynen geri giderse sunucu mevcut (şifreli) değeri korur.

export const isMaskedTaxId = (v) => /[*•]/.test(String(v ?? ""));

// Ekran: değer zaten maskeliyse aynen, düz ise son 2 hane dışında gizle.
export const maskTaxId = (v) => {
  const s = String(v ?? "").trim();
  if (!s) return "";
  if (isMaskedTaxId(s)) return s;
  return `${"*".repeat(9)}${s.slice(-2)}`;
};

// Düzenleme: maskeli alana yazılınca maske atılır, yalnız kullanıcının girdiği rakamlar kalır.
// (Tümünü seçip yazma → yeni değer; sona ekleme → maske + son 2 hane atılıp eklenen rakamlar.)
export const editTaxIdValue = (prev, next) => {
  const n = String(next ?? "");
  if (!isMaskedTaxId(prev)) return n.replace(/\D/g, "");
  if (!isMaskedTaxId(n)) return n.replace(/\D/g, "");
  return n.replace(/^[*•]+\d{0,2}/, "").replace(/\D/g, "");
};
