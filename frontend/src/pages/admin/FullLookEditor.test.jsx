import { act } from 'react';
import { createRoot } from 'react-dom/client';
import axios from 'axios';
import FullLookEditor, { lookPayload } from './FullLookEditor';
jest.mock('axios', () => ({ get: jest.fn(), put: jest.fn() }));
const jacket = { id: 'jacket', name: 'Ceket', price: 2000, campaign_discount_percent: 20, images: ['/front.jpg', '/look.jpg'] };
const pants = { id: 'pants', name: 'Pantolon', price: 1000, images: ['/pants.jpg'] };
const initial = () => ({ title: 'Full Look', description: '', revision: 0, looks: [{ id: 'one', title: '', source_product_id: '', image: '', product_ids: [], products: [], is_active: false }] });
let container, root;
const click = async button => act(async () => button.dispatchEvent(new MouseEvent('click', { bubbles: true })));
const button = text => [...container.querySelectorAll('button')].find(b => b.textContent.includes(text));
const input = async (el, value) => act(async () => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
});
beforeAll(() => { global.IS_REACT_ACT_ENVIRONMENT = true; });
beforeEach(() => {
  jest.useFakeTimers(); container = document.createElement('div'); document.body.append(container); root = createRoot(container);
  axios.get.mockImplementation(url => Promise.resolve({ data: url.endsWith('/admin') ? initial() : { products: [jacket, pants] } }));
  axios.put.mockImplementation((url, payload) => Promise.resolve({ data: { ...payload, revision: 1, looks: payload.looks.map(r => ({ ...r, source_product: jacket, products: r.product_ids.map(id => id === 'jacket' ? jacket : pants) })) } }));
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); jest.useRealTimers(); jest.clearAllMocks(); });
afterAll(() => { delete global.IS_REACT_ACT_ENVIRONMENT; });
test('source search, explicit gallery choice, product search, preview and single save', async () => {
  await act(async () => root.render(<FullLookEditor />));
  await input(container.querySelector('input[type=search]'), 'ceket');
  await act(async () => jest.advanceTimersByTime(301));
  await click(container.querySelector('.look-search-results button'));
  expect(container.querySelectorAll('.look-gallery button')).toHaveLength(2);
  expect(container.querySelector('.look-gallery [aria-pressed=true]')).toBeNull();
  await click(container.querySelector('[aria-label="Fotoğraf 2"]'));
  await input(container.querySelectorAll('input[type=search]')[1], 'pant');
  await act(async () => jest.advanceTimersByTime(301));
  await click([...container.querySelectorAll('.look-search-results button')].find(b => b.textContent.includes('Pantolon')));
  expect(container.querySelector('.full-look-hero img').getAttribute('src')).toBe('/look.jpg');
  expect(container.querySelector('.full-look-price').textContent).toContain('1000,00 TL');
  await click(button('Mobil önizleme'));
  expect(container.querySelector('.full-look-editor-preview.is-mobile')).not.toBeNull();
  expect(axios.put).not.toHaveBeenCalled();
  await click(button('Değişiklikleri Kaydet'));
  const payload = axios.put.mock.calls[0][1];
  expect(payload.looks[0]).toMatchObject({ image: '/look.jpg', source_product_id: 'jacket', product_ids: ['pants'], is_active: false });
  expect(payload.looks[0].products).toBeUndefined();
  expect(container.textContent).toContain('Kaydedildi.');
});
test('conflict keeps unsaved draft and displays actionable error', async () => {
  axios.put.mockRejectedValue({ response: { status: 409, data: { detail: 'Sayfa başka bir kullanıcı tarafından değiştirildi.' } } });
  await act(async () => root.render(<FullLookEditor />));
  await input(container.querySelector('.look-page-fields input'), 'Yeni başlık');
  await click(button('Değişiklikleri Kaydet'));
  expect(container.querySelector('[role=alert]').textContent).toContain('Taslağınız korunuyor');
  expect(container.querySelector('.look-page-fields input').value).toBe('Yeni başlık');
});
test('payload excludes prices and hydrated snapshots', () => {
  const data = initial(); data.looks[0].products = [jacket];
  expect(lookPayload(data).looks[0]).not.toHaveProperty('products');
});
