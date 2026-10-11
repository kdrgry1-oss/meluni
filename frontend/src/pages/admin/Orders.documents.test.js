import fs from 'fs';
import path from 'path';
import { parse } from '@babel/parser';

// Exercise the actual handler without mounting unrelated order mutations/providers.
const source = fs.readFileSync(path.join(__dirname, 'Orders.jsx'), 'utf8');
const ast = parse(source, { sourceType: 'module', plugins: ['jsx'] });
const component = ast.program.body.find(n => n.type === 'ExportDefaultDeclaration').declaration;
const node = component.body.body.flatMap(n => n.declarations || [])
  .find(n => n.id?.name === 'handleBulkPrintCargoLabels').init;

function setup(ids = ['a']) {
  const toast = { error: jest.fn(), loading: jest.fn(), dismiss: jest.fn() };
  const fetchAdminDocumentTexts = jest.fn().mockResolvedValue({htmls: [], failures: []});
  const fetchOrders = jest.fn();
  const popup = { opener: {}, close: jest.fn(), document: {write: jest.fn(), close: jest.fn()} };
  const open = jest.spyOn(window, 'open').mockReturnValue(popup);
  const handler = new Function('selectedOrders', 'toast', 'fetchAdminDocumentTexts', 'sanitizeHtml', 'fetchOrders',
    `return (${source.slice(node.start, node.end)});`)(ids, toast, fetchAdminDocumentTexts, html => html, fetchOrders);
  return {handler, toast, fetchAdminDocumentTexts, fetchOrders, popup, open};
}
afterEach(() => jest.restoreAllMocks());

test('blocked popup makes no label request or print-counter change', async () => {
  const t = setup();
  t.open.mockReturnValue(null);
  await t.handler();
  expect(t.fetchAdminDocumentTexts).not.toHaveBeenCalled();
  expect(t.toast.error).toHaveBeenCalledWith(expect.stringContaining('engellendi'));
});

test('all failures show actual reason and close blank popup', async () => {
  const t = setup();
  t.fetchAdminDocumentTexts.mockResolvedValue({htmls: [], failures: [{id:'a',error:'Yetkiniz yok (orders.cargo)'}]});
  await t.handler();
  expect(t.toast.error).toHaveBeenCalledWith(expect.stringContaining('orders.cargo'), expect.any(Object));
  expect(t.popup.close).toHaveBeenCalled();
  expect(t.popup.document.write).not.toHaveBeenCalled();
});

test('partial success warns about missing labels and prints only successful HTML', async () => {
  const t = setup(['a','b']);
  t.fetchAdminDocumentTexts.mockResolvedValue({htmls: ['<html><head><style>.label{color:black}</style></head><body><div class="label">TEST-001</div></body></html>'], failures: [{id:'b',error:'Sipariş bulunamadı'}]});
  await t.handler();
  expect(t.toast.error).toHaveBeenCalledWith(expect.stringContaining('1/2'), expect.any(Object));
  expect(t.popup.document.write).toHaveBeenCalledWith(expect.stringContaining('1 Kargo Etiketi'));
  expect(t.popup.opener).toBeNull();
  expect(t.fetchOrders).toHaveBeenCalledTimes(1);
});

test('empty selection never opens a window or fetches a label', async () => {
  const t = setup([]);
  await t.handler();
  expect(t.open).not.toHaveBeenCalled();
  expect(t.fetchAdminDocumentTexts).not.toHaveBeenCalled();
});
