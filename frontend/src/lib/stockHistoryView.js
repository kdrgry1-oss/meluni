export const stockSyncLabel = (syncResult = {}) => {
  const labels = {
    not_applicable: "Yerel işlem",
    not_recorded: "Geçmişte kaydedilmedi",
    submitted: "Gönderildi",
    success: "Başarılı",
    failed: "Başarısız",
    error: "Hata",
  };
  return labels[syncResult?.status] || syncResult?.status || "—";
};

export const stockSyncDetails = (syncResult = {}) =>
  [syncResult?.platform, syncResult?.batch_id, syncResult?.message]
    .filter(Boolean)
    .join(" · ");

export const stockActorDetails = (move = {}) =>
  [move.actor?.login_method, move.context?.session_hash, move.context?.ip]
    .filter(Boolean)
    .join(" · ");
