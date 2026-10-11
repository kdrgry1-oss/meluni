import {
  REPORT_MIN_DATE, clampReportDate, defaultReportRange, filterReportChannels,
  productExportParams, reportCoverageDays, reportPresetRange, splitReportRange,
  productReportScope,
} from "./reportFilters";

test("report range starts at the historical reporting boundary", () => {
  // Açılış aralığı artık İÇİNDE BULUNULAN AY (ayın 1'i → bugün).
  expect(defaultReportRange(new Date(2026, 8, 7))).toEqual({ from: "2026-09-01", to: "2026-09-07" });
  // Verinin başladığı aydan öncesine taşmaz (clamp).
  expect(defaultReportRange(new Date(2026, 5, 20))).toEqual({ from: REPORT_MIN_DATE, to: "2026-06-20" });
  expect(clampReportDate("2025-01-01")).toBe(REPORT_MIN_DATE);
  expect(reportCoverageDays(new Date(2026, 8, 7))).toBe(95);
});

test("long Trendyol reconciliation periods are split without gaps", () => {
  expect(splitReportRange("2026-06-05", "2026-09-07", 31)).toEqual([
    { from: "2026-06-05", to: "2026-07-05" },
    { from: "2026-07-06", to: "2026-08-05" },
    { from: "2026-08-06", to: "2026-09-05" },
    { from: "2026-09-06", to: "2026-09-07" },
  ]);
});

test("presets are inclusive and never cross the reporting boundary", () => {
  expect(reportPresetRange("7", new Date(2026, 8, 7))).toEqual({ from: "2026-09-01", to: "2026-09-07" });
  expect(reportPresetRange("365", new Date(2026, 8, 7))).toEqual({ from: REPORT_MIN_DATE, to: "2026-09-07" });
});

test("gecen ay onayari: ayin tamami, yil ve subat gecisleri dahil", () => {
  // Eylul'deyken -> Agustos 1..31
  expect(reportPresetRange("lastmonth", new Date(2026, 8, 22))).toEqual({ from: "2026-08-01", to: "2026-08-31" });
  // Ocak'tayken -> onceki YILIN Aralik ayi
  expect(reportPresetRange("lastmonth", new Date(2027, 0, 15))).toEqual({ from: "2026-12-01", to: "2026-12-31" });
  // Mart'tayken -> Subat 1..28/29 (ay sonu tasmasi yok). 2028 artik yil: 29 cekiyor.
  expect(reportPresetRange("lastmonth", new Date(2028, 2, 31))).toEqual({ from: "2028-02-01", to: "2028-02-29" });
  // Ayin 1'indeyken de onceki ayin TAMAMI
  expect(reportPresetRange("lastmonth", new Date(2026, 9, 1))).toEqual({ from: "2026-09-01", to: "2026-09-30" });
  // Rapor alt siniri: tamamen sinirin gerisinde kalan ay kirpilir (o tarihte veri yok)
  expect(reportPresetRange("lastmonth", new Date(2026, 2, 15)))
    .toEqual({ from: REPORT_MIN_DATE, to: REPORT_MIN_DATE });
});

test("channel table follows the selected report source", () => {
  const rows = [{ source: "Site" }, { source: "Trendyol" }];
  expect(filterReportChannels(rows, "site")).toEqual([{ source: "Site" }]);
  expect(filterReportChannels(rows, "trendyol")).toEqual([{ source: "Trendyol" }]);
  expect(filterReportChannels(rows, "all")).toEqual(rows);
});

test("product export carries every visible table filter", () => {
  const params = productExportParams({
    from: "2026-06-05", to: "2026-09-07", platform: "trendyol", size: "M",
    season: "Yaz", velocity: "green", query: "bermuda", sortKey: "revenue", sortDir: "desc",
  });
  expect(Object.fromEntries(params)).toEqual({
    start_date: "2026-06-05", end_date: "2026-09-07", source: "trendyol",
    platform: "trendyol", size: "M", season: "Yaz", velocity: "green", q: "bermuda",
    sort_by: "revenue", sort_dir: "desc",
  });
});

test("product return rate uses the same visible scope for all and selected platforms", () => {
  const product = {
    qty: 9, cancel_qty: 3, return_qty: 4, revenue: 900,
    platform_breakdown: [
      { platform: "trendyol", qty: 2, revenue: 200 },
      { platform: "site", qty: 7, revenue: 700 },
    ],
    cancel_return_by_platform: [
      { platform: "trendyol", cancel: 2, return: 4 },
      { platform: "site", cancel: 1, return: 0 },
    ],
  };
  expect(productReportScope(product)).toMatchObject({ netQty: 9, cancelQty: 3, returnQty: 4, grossQty: 16, returnRatePct: 25 });
  expect(productReportScope(product, "trendyol")).toMatchObject({ netQty: 2, cancelQty: 2, returnQty: 4, grossQty: 8, returnRatePct: 50 });
  expect(productReportScope(product, "site")).toMatchObject({ netQty: 7, cancelQty: 1, returnQty: 0, grossQty: 8, returnRatePct: 0 });
});

test("product platform_metrics is authoritative when backend supplies it", () => {
  const product = {
    qty: 99, cancel_qty: 99, return_qty: 99,
    platform_metrics: {
      trendyol: { net_qty: 5, cancel_qty: 1, return_qty: 2, net_revenue: 500 },
      site: { net_qty: 3, cancel_qty: 0, return_qty: 0, net_revenue: 300 },
    },
  };
  expect(productReportScope(product)).toMatchObject({ netQty: 8, cancelQty: 1, returnQty: 2, grossQty: 11, revenue: 800 });
  expect(productReportScope(product, "trendyol")).toMatchObject({ netQty: 5, cancelQty: 1, returnQty: 2, grossQty: 8, returnRatePct: 25 });
});
