import { act } from 'react';
import { createRoot } from 'react-dom/client';
import FullLookSections from './FullLookSections';

test('renders successive looks, four linked cards and authoritative campaign prices', async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const container = document.createElement('div'), root = createRoot(container);
  const products = [1, 2, 3, 4].map(i => ({ id: String(i), slug: `product-${i}`, name: `Ürün ${i}`, images: [`/p${i}.jpg`], price: 3200, campaign_discount_percent: 20 }));
  await act(async () => root.render(<FullLookSections looks={[{ id: 'a', image: '/look.jpg', products }, { id: 'b', image: '/look2.jpg', products: [products[0]] }]} />));
  expect(container.querySelectorAll('.full-look-section')).toHaveLength(2);
  expect(container.querySelector('.full-look-products').children).toHaveLength(4);
  expect(container.querySelector('a.full-look-product').getAttribute('href')).toBe('/urun/product-1');
  expect(container.querySelector('.full-look-number')).toBeNull();
  expect(container.querySelector('.full-look-shop-link')).toBeNull();
  expect(container.querySelector('#look-products-a .full-look-label').textContent).toBe('LOOK 01');
  expect(container.querySelector('#look-products-b .full-look-label').textContent).toBe('LOOK 02');
  expect(container.querySelector('#look-products-a .full-look-caption-meta').textContent).toContain('4 PARÇA');
  expect(container.querySelector('.full-look-price').textContent).toBe('3200,00 TL2560,00 TL');
  expect(container.querySelectorAll('.full-look-hero img')[1].getAttribute('loading')).toBe('lazy');
  await act(async () => root.unmount());
  delete global.IS_REACT_ACT_ENVIRONMENT;
});
