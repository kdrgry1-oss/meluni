// Match backend/shipping_rules.py. Compare integer kuruş, including the boundary.
export function shippingQuote({ subtotal, discounts = [], threshold, fee, freeShippingPromotion = false }) {
  const cents = (value) => Math.round((Number(value || 0) + Number.EPSILON) * 100);
  const basis = Math.max(0, cents(subtotal) - discounts.reduce((sum, d) => sum + cents(d), 0));
  const free = threshold != null ? basis >= cents(threshold) : Boolean(freeShippingPromotion);
  return { basis: basis / 100, free, cost: free ? 0 : cents(fee) / 100 };
}
