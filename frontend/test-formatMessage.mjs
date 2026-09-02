import { test } from 'node:test';
import assert from 'node:assert/strict';
import { formatMessage } from './src/formatMessage.mjs';

test('formats reference headings, emphasis and sources', () => {
  const html = formatMessage('## Título\n\nAbra **Eventos**.\nFonte: [Ajuda](https://portal.inchurch.com.br/pt-br)');
  assert.ok(html.includes('<strong>Título</strong>'));
  assert.ok(html.includes('<strong>Eventos</strong>'));
  assert.ok(html.includes('rel="noopener noreferrer"'));
  assert.ok(!html.includes('##'));
});
test('customer and model HTML cannot execute', () => {
  const html = formatMessage('<img src=x onerror=alert(1)> <script>alert(1)</script>');
  assert.ok(!html.includes('<img'));
  assert.ok(!html.includes('<script'));
  assert.ok(html.includes('&lt;img'));
});
test('links cannot break out of escaped attributes', () => {
  const html = formatMessage('[x](https://example.com/"onclick="evil) [bad](javascript:evil)');
  assert.ok(!html.includes('"onclick='));
  assert.ok(!html.includes('href="javascript:'));
});
test('handles WhatsApp bold, empty values and internal tokens', () => {
  assert.equal(formatMessage(null), '');
  assert.equal(formatMessage('Abra *Eventos*.'), 'Abra <strong>Eventos</strong>.');
  assert.equal(formatMessage('TRANSFERIR_SUPORTE'), '');
});
