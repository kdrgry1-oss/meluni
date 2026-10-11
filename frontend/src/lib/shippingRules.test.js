import { shippingQuote } from './shippingRules';

test.each([[3999.99, false], [4000, true], [4000.01, true]])('boundary %s', (subtotal, free) => {
  expect(shippingQuote({ subtotal, threshold: 4000, fee: 99, freeShippingPromotion: true }))
    .toEqual({ basis: subtotal, free, cost: free ? 0 : 99 });
});
test('campaign, payment and points reductions count; charges never count', () => {
  const base = { subtotal: 6000, threshold: 4000, fee: 99, freeShippingPromotion: true };
  expect(shippingQuote({ ...base, discounts: [600, 1080, 216, 105] })).toEqual({ basis: 3999, free: false, cost: 99 });
  expect(shippingQuote({ ...base, discounts: [600, 1080, 216, 104] }).free).toBe(true);
});
test('bank payment moves otherwise qualifying cart below the threshold', () => {
  expect(shippingQuote({ subtotal: 5700, discounts: [570, 1026], threshold: 4000, fee: 99 }).free).toBe(true);
  expect(shippingQuote({ subtotal: 5700, discounts: [570, 1026, 205.2], threshold: 4000, fee: 99 }).free).toBe(false);
});
test('no threshold and tenant-configured amount remain supported', () => {
  expect(shippingQuote({ subtotal: 100, threshold: 100, fee: 15 }).free).toBe(true);
  expect(shippingQuote({ subtotal: 5000, threshold: null, fee: 15 }).free).toBe(false);
});
