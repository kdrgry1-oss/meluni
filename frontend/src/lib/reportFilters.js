export const REPORT_MIN_DATE = "2026-06-05";

export const localYmd = (date) => {
  const d = date instanceof Date ? date : new Date(date);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

// Raporların "bugün"ü TÜRKİYE günüdür (arka uç YYYY-MM-DD'yi Europe/Istanbul günü
// olarak yorumlar). Tarayıcı başka saat dilimindeyse (ör. UTC'de TR 00:00-03:00 arası)
// yerel new Date() bir gün geride kalıyor, ay başında "Bu Ay" önceki ayı açıyordu.
// Dönen Date, TR takvim gününün YEREL ÖĞLE saatidir → yerel bileşenlerle yapılan
// gün/ay aritmetiği ve localYmd güvenlidir.
export function trTodayYmd(now = new Date()) {
  try {
    return new Intl.DateTimeFormat("en-CA", {
      timeZone: "Europe/Istanbul", year: "numeric", month: "2-digit", day: "2-digit",
    }).format(now);
  } catch {
    return localYmd(now);
  }
}

export function trTodayDate(now = new Date()) {
  return new Date(`${trTodayYmd(now)}T12:00:00`);
}

export const clampReportDate = (value) => {
  const date = String(value || "").slice(0, 10);
  return date && date < REPORT_MIN_DATE ? REPORT_MIN_DATE : date;
};

// Rapor sayfalarının AÇILIŞ aralığı: İÇİNDE BULUNULAN AY (1'i → bugün).
// Kadir: "tüm rapor sayfalarında tarih aralığı otomatik olarak ilgili ayı açarak gelsin".
// Eskiden REPORT_MIN_DATE'ten bugüne (TÜM GEÇMİŞ) açılıyordu; hem istenen bu değildi
// hem de her açılışta aylarca veriyi tarattığı için sayfa çok yavaş geliyordu.
// clampReportDate, verinin başlangıcından öncesine taşmayı zaten engeller.
export function defaultReportRange(today = trTodayDate()) {
  const first = localYmd(new Date(today.getFullYear(), today.getMonth(), 1));
  return { from: clampReportDate(first), to: localYmd(today) };
}

export function reportCoverageDays(today = trTodayDate()) {
  const start = new Date(`${REPORT_MIN_DATE}T12:00:00`);
  const end = new Date(`${localYmd(today)}T12:00:00`);
  return Math.max(1, Math.round((end - start) / 86400000) + 1);
}

export function splitReportRange(from, to, maxDays = 31) {
  const start = new Date(`${clampReportDate(from)}T12:00:00`);
  const finish = new Date(`${clampReportDate(to)}T12:00:00`);
  if (!Number.isFinite(start.getTime()) || !Number.isFinite(finish.getTime()) || start > finish) return [];
  const size = Math.max(1, Number(maxDays) || 31);
  const chunks = [];
  for (let cursor = start; cursor <= finish;) {
    const end = new Date(cursor);
    end.setDate(end.getDate() + size - 1);
    if (end > finish) end.setTime(finish.getTime());
    chunks.push({ from: localYmd(cursor), to: localYmd(end) });
    cursor = new Date(end);
    cursor.setDate(cursor.getDate() + 1);
  }
  return chunks;
}

export function reportPresetRange(key, today = trTodayDate()) {
  const end = localYmd(today);
  let from = end;
  if (key === "yesterday") {
    const yesterday = localYmd(new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1));
    return { from: clampReportDate(yesterday), to: clampReportDate(yesterday) };
  }
  if (key === "lastmonth") {
    // Geçen ayın 1'i → son günü (ayın uzunluğu ne olursa olsun: gün 0 = önceki ayın son günü)
    const first = new Date(today.getFullYear(), today.getMonth() - 1, 1);
    const last = new Date(today.getFullYear(), today.getMonth(), 0);
    return { from: clampReportDate(localYmd(first)), to: clampReportDate(localYmd(last)) };
  }
  if (key === "thismonth" || key === "month") {
    from = localYmd(new Date(today.getFullYear(), today.getMonth(), 1));
  } else {
    const days = Math.max(1, Number(key) || 1);
    from = localYmd(new Date(today.getFullYear(), today.getMonth(), today.getDate() - (days - 1)));
  }
  return { from: clampReportDate(from), to: end };
}

// Aralığın gün sayısı (iki uç dahil). Geçersiz tarihte 0.
export function reportRangeDays(from, to) {
  const a = new Date(`${String(from || "").slice(0, 10)}T12:00:00`);
  const b = new Date(`${String(to || "").slice(0, 10)}T12:00:00`);
  if (!Number.isFinite(a.getTime()) || !Number.isFinite(b.getTime())) return 0;
  return Math.round((b - a) / 86400000) + 1;
}

// Satış grafiğinin kırılımı aralığın uzunluğundan: ≤31 gün günlük, ≤120 haftalık, üstü aylık.
export function reportGroupBy(from, to) {
  const days = Math.max(1, reportRangeDays(from, to));
  return days <= 31 ? "day" : days <= 120 ? "week" : "month";
}

// Tarih aralığı geçerli mi? Geçersizse kullanıcıya gösterilecek mesaj, geçerliyse "".
export function reportRangeError(from, to) {
  if (!from || !to) return "Başlangıç ve bitiş tarihi seçin.";
  if (String(from).slice(0, 10) > String(to).slice(0, 10)) return "Başlangıç tarihi bitiş tarihinden sonra olamaz.";
  return "";
}

export function filterReportChannels(items, source) {
  if (!source || source === "all") return items || [];
  const wanted = source === "site" ? "site" : source.toLocaleLowerCase("tr");
  return (items || []).filter((row) => {
    const value = String(row?.source || row?.channel || "").trim().toLocaleLowerCase("tr");
    return wanted === "site" ? ["site", "meluni", "site (kendi)"].includes(value) : value === wanted;
  });
}

const _number = (value) => Number(value || 0);
const _platformKey = (value) => String(value || "").trim().toLocaleLowerCase("tr");

/**
 * Ürün raporundaki görünür Net/İptal/İade/Toplam ve iade yüzdesini tek kapsamdan üretir.
 * Yeni API `platform_metrics` gönderiyorsa onu esas alır; eski cevaplarda mevcut
 * platform_breakdown + cancel_return_by_platform alanlarına geriye uyumludur.
 */
export function productReportScope(product = {}, platform = "") {
  const wanted = _platformKey(platform);
  const rawMetrics = Array.isArray(product.platform_metrics)
    ? product.platform_metrics
    : Object.entries(product.platform_metrics || {}).map(([key, value]) => ({ platform: key, ...(value || {}) }));
  const metrics = rawMetrics.map((row) => ({
    platform: _platformKey(row.platform || row.source || row.channel),
    netQty: _number(row.net_qty ?? row.net_units ?? row.qty),
    cancelQty: _number(row.cancel_qty ?? row.cancel_units ?? row.cancel),
    returnQty: _number(row.return_qty ?? row.return_units ?? row.returned ?? row.return),
    revenue: _number(row.net_revenue ?? row.revenue),
  })).filter((row) => row.platform);

  let selected = wanted ? metrics.filter((row) => row.platform === wanted) : metrics;
  let hasPlatformData = selected.length > 0;

  if (!hasPlatformData && wanted) {
    const sales = (product.platform_breakdown || []).find((row) => _platformKey(row.platform) === wanted);
    const cr = (product.cancel_return_by_platform || []).find((row) => _platformKey(row.platform) === wanted);
    hasPlatformData = Boolean(sales || cr);
    selected = hasPlatformData ? [{
      platform: wanted,
      netQty: _number(sales?.qty),
      cancelQty: _number(cr?.cancel),
      returnQty: _number(cr?.return),
      revenue: _number(sales?.revenue),
    }] : [];
  }

  const totals = selected.reduce((sum, row) => ({
    netQty: sum.netQty + row.netQty,
    cancelQty: sum.cancelQty + row.cancelQty,
    returnQty: sum.returnQty + row.returnQty,
    revenue: sum.revenue + row.revenue,
  }), { netQty: 0, cancelQty: 0, returnQty: 0, revenue: 0 });

  if (!wanted && metrics.length === 0) {
    totals.netQty = _number(product.qty);
    totals.cancelQty = _number(product.cancel_qty);
    totals.returnQty = _number(product.return_qty);
    totals.revenue = _number(product.revenue);
    hasPlatformData = true;
  }

  const grossQty = totals.netQty + totals.cancelQty + totals.returnQty;
  return {
    ...totals,
    grossQty,
    returnRatePct: grossQty > 0 ? (100 * totals.returnQty) / grossQty : 0,
    hasPlatformData,
  };
}

export function productExportParams({ from, to, platform, size, season, velocity, query, sortKey, sortDir, withImages, baseFrom }) {
  const params = new URLSearchParams({
    start_date: clampReportDate(from),
    // Backend expands a plain YMD to the complete Turkish local day, including
    // the final microsecond. Sending T23:59:59 would truncate that last second.
    end_date: clampReportDate(to),
  });
  if (platform) {
    params.set("source", platform);
    params.set("platform", platform);
  }
  if (size) params.set("size", size);
  if (season) params.set("season", season);
  if (velocity) params.set("velocity", velocity);
  if (query?.trim()) params.set("q", query.trim());
  if (sortKey) params.set("sort_by", sortKey);
  if (sortDir) params.set("sort_dir", sortDir);
  // İvme sıralaması için ekranın kullandığı taban pencere (≈90 gün) başlangıcı
  if (baseFrom && sortKey === "_mom") params.set("base_start_date", clampReportDate(baseFrom));
  // Görselli rapor: her satırın başına ürün fotoğrafı gömülür (kullanıcı isteği).
  if (withImages) { params.set("with_images", "1"); params.set("img_size", "medium"); }
  return params;
}
