// Behaviour tests for the actual generated runtime. Only Node built-ins are used.
// This adapter supplies the DOM operations exercised here, not browser layout.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const templates = JSON.parse(fs.readFileSync(0, 'utf8'));

class DOMNode {
  constructor(tag, document, text = '') {
    this.tagName = tag.toUpperCase();
    this.nodeType = tag === '#text' ? 3 : 1;
    this.ownerDocument = document;
    this.childNodes = [];
    this.attributes = Object.create(null);
    this.dataset = Object.create(null);
    this.style = Object.create(null);
    this.listeners = Object.create(null);
    this.hidden = false;
    this.text = text;
  }
  get children() { return this.childNodes.filter(node => node.nodeType === 1); }
  get firstChild() { return this.childNodes[0] || null; }
  get id() { return this.getAttribute('id') || ''; }
  get lang() { return this.getAttribute('lang') || ''; }
  get textContent() { return this.nodeType === 3 ? this.text : this.childNodes.map(node => node.textContent).join(''); }
  set textContent(value) {
    for (const node of [...this.childNodes]) this.removeChild(node);
    if (String(value)) this.appendChild(this.ownerDocument.createTextNode(String(value)));
  }
  get value() {
    if (this._value !== undefined) return this._value;
    if (this.tagName === 'SELECT') return this.children.length ? this.children[0].value : '';
    return this.getAttribute('value') || '';
  }
  set value(value) { this._value = String(value); }
  setAttribute(key, value) {
    this.attributes[key] = value == null ? '' : String(value);
    if (key.startsWith('data-')) this.dataset[key.slice(5)] = this.attributes[key];
  }
  getAttribute(key) { return Object.hasOwn(this.attributes, key) ? this.attributes[key] : null; }
  appendChild(node) {
    if (node.parentNode) node.parentNode.removeChild(node);
    this.childNodes.push(node);
    node.parentNode = this;
    return node;
  }
  removeChild(node) {
    const index = this.childNodes.indexOf(node);
    assert.ok(index >= 0, 'removeChild must receive a child');
    if (node.contains(this.ownerDocument.activeElement)) this.ownerDocument.activeElement = this.ownerDocument.body;
    this.childNodes.splice(index, 1);
    node.parentNode = null;
    return node;
  }
  contains(node) { return node === this || this.childNodes.some(child => child.contains(node)); }
  matches(selector) {
    return selector.split(',').some(part => {
      const match = part.trim().match(/^([\w-]+)?(?:#([\w-]+))?(?:\.([\w-]+))?(?:\[([\w-]+)(?:(\^?=)"([^"]*)")?\])?$/);
      assert.ok(match, 'unsupported test selector: ' + part);
      const [, tag, id, cls, attr, op, value] = match;
      return (!tag || this.tagName.toLowerCase() === tag) && (!id || this.id === id)
        && (!cls || (this.getAttribute('class') || '').split(/\s+/).includes(cls))
        && (!attr || (this.getAttribute(attr) !== null && (!op || (op === '^='
          ? this.getAttribute(attr).startsWith(value) : this.getAttribute(attr) === value))));
    });
  }
  querySelectorAll(selector) {
    return this.children.flatMap(child => [
      ...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)
    ]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) { return this.matches(selector) ? this : this.parentNode && this.parentNode.closest(selector); }
  addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }
  focus() { this.ownerDocument.activeElement = this; }
  select() { this.focus(); this.ownerDocument.selected = this; }
  scrollIntoView() { this.scrolled = true; }
  click() {
    const event = { target: this, stopPropagation() { this.stopped = true; }, preventDefault() { this.prevented = true; } };
    for (let node = this; node; node = node.parentNode) {
      if (node.onclick) node.onclick(event);
      for (const listener of node.listeners.click || []) listener(event);
      if (event.stopped) break;
    }
    if (!event.prevented && this.tagName === 'A' && this.getAttribute('href').startsWith('#')) {
      this.ownerDocument.window.location.hash = this.getAttribute('href');
    }
  }
}

function dashboard(lang = 'en', options = {}) {
  const document = new DOMNode('#document');
  document.ownerDocument = document;
  document.createElement = tag => new DOMNode(tag, document);
  document.createTextNode = text => new DOMNode('#text', document, String(text));
  document.getElementById = id => document.querySelectorAll('[id]').find(node => node.id === id) || null;
  function build(tree, parent) {
    for (const child of tree.children || []) {
      const node = Object.hasOwn(child, 'text') ? document.createTextNode(child.text) : document.createElement(child.tag);
      for (const [key, value] of Object.entries(child.attrs || {})) node.setAttribute(key, value);
      parent.appendChild(node);
      build(child, node);
    }
  }
  build(templates[lang], document);
  document.documentElement = document.querySelector('html');
  document.body = document.querySelector('body');
  document.activeElement = document.body;
  const listeners = Object.create(null);
  const window = {
    isSecureContext: options.clipboard !== undefined,
    matchMedia: () => ({ matches: Boolean(options.dark) }),
    addEventListener(type, listener) { (listeners[type] ||= []).push(listener); },
    location: {}
  };
  let hash = options.hash || '';
  Object.defineProperty(window.location, 'hash', {
    get() { return hash; },
    set(value) {
      if (value === hash) return;
      hash = value;
      for (const listener of listeners.hashchange || []) listener();
    }
  });
  document.window = window;
  const copied = [], fallbacks = [];
  document.execCommand = command => {
    assert.equal(command, 'copy');
    fallbacks.push(document.selected.value);
    return options.fallbackSuccess !== false;
  };
  const navigator = {};
  if (options.clipboard) navigator.clipboard = { writeText(text) {
    if (options.clipboard === 'reject') return Promise.reject(new Error('permission denied'));
    copied.push(text);
    return Promise.resolve();
  } };
  let timerId = 0;
  const timers = new Map();
  const dataElement = document.getElementById('data');
  const data = JSON.parse(dataElement.textContent);
  if (options.mutate) { options.mutate(data); dataElement.textContent = JSON.stringify(data); }
  const source = document.querySelectorAll('script').find(script => !script.id).textContent;
  vm.runInNewContext(source, {
    document, window, navigator, Node: DOMNode,
    setTimeout(callback) { timers.set(++timerId, callback); return timerId; },
    clearTimeout(id) { timers.delete(id); }
  }, { filename: 'dashboard.js', timeout: 10000 });
  return {
    document, window, data, copied, fallbacks, timers,
    id: id => document.getElementById(id),
    rows() { return document.getElementById('rows').children.filter(node => node.matches('.row')); },
    ids() { return this.rows().map(row => row.children[0].textContent); },
    change(id, value) { const control = this.id(id); control.value = value; control.onchange(); },
    search(value) { const control = this.id('f-q'); control.value = value; control.oninput(); },
    runTimers() { const callbacks = [...timers.values()]; timers.clear(); callbacks.forEach(callback => callback()); }
  };
}

const tests = [];
function test(name, run) { tests.push({ name, run }); }
const settle = () => new Promise(resolve => setImmediate(resolve));
function copyButton(harness, anchor = 'finding-1') {
  harness.id(anchor).querySelector('.finding-toggle').click();
  return harness.id(anchor + '-detail').querySelector('.copy-finding');
}

test('English and Japanese render literal data, correct counts, and stable priority', () => {
  for (const lang of ['en', 'ja']) {
    const h = dashboard(lang);
    assert.equal(h.id('h').textContent, 'Project <&> 日本語');
    assert.deepEqual(h.ids(), ['F-10', 'F-3', 'F-02', 'F-2', 'F-1']);
    assert.deepEqual(h.id('cards').querySelectorAll('.n').map(node => node.textContent), ['3', '4', '1', '1']);
    assert.equal(h.id('priority').querySelectorAll('article').length, 4);
    assert.equal(h.document.querySelectorAll('img').length, 0);
    assert.ok(h.id('finding-1').textContent.includes('<img src=x onerror=attack()>'));
    assert.equal(h.id('f-q').placeholder, h.data.labels.search);
    assert.equal(h.document.documentElement.lang, lang);
    assert.equal(h.id('rows').closest('table').getAttribute('role'), 'table');
    assert.equal(h.id('finding-1').getAttribute('role'), 'row');
    assert.ok(h.id('finding-1').children.every(cell => cell.getAttribute('role') === 'cell'));
  }
});

test('ID sort compares numbers and keeps equal IDs in recorded order', () => {
  const h = dashboard();
  h.change('f-sort', 'id');
  assert.deepEqual(h.ids(), ['F-1', 'F-2', 'F-02', 'F-3', 'F-10']);
  h.change('f-sort', 'priority');
  assert.deepEqual(h.ids(), ['F-10', 'F-3', 'F-02', 'F-2', 'F-1']);
});

test('filters combine with excluded scope and preserve the underlying recorded status', () => {
  const h = dashboard();
  h.change('f-sev', 'High');
  h.change('f-status', 'Open');
  assert.deepEqual(h.ids(), ['F-10', 'F-3']);
  h.change('f-cat', 'constructor');
  assert.deepEqual(h.ids(), ['F-10']);
  h.id('reset').click();
  h.change('f-scope', 'excluded');
  h.change('f-status', 'Accepted');
  h.change('f-cat', '__proto__');
  assert.deepEqual(h.ids(), ['__proto__ < & " / 日本語']);
  assert.equal(h.rows()[0].children[3].textContent, 'Accepted · Excluded');
  h.id('reset').click();
  assert.equal(h.rows().length, 5);
  h.change('f-scope', 'all');
  assert.equal(h.rows().length, 6);
});

test('search includes all recorded detail text, labels, snippets, and references', () => {
  const h = dashboard();
  for (const term of ['f-10', ' FIRST ', 'src/<entry>', 'ACTION LABEL MARKER', 'Action text marker',
    'Evidence label marker', 'body marker', '日本語', 'snippet_unique_token',
    'Reference type marker', 'Reference title marker', 'reference_unique_token', '<img src=x']) {
    h.search(term);
    assert.deepEqual(h.ids(), ['F-10'], term);
  }
  h.search('does not appear');
  assert.equal(h.id('rows').textContent, h.data.labels.no_matches);
});

test('empty English and Japanese reports keep controls and empty states usable', () => {
  for (const lang of ['empty_en', 'empty_ja']) {
    const h = dashboard(lang);
    assert.equal(h.rows().length, 0);
    assert.equal(h.id('rows').textContent, h.data.labels.no_records);
    assert.equal(h.id('priority').textContent, h.data.labels.no_open);
    assert.deepEqual(h.id('cards').querySelectorAll('.n').map(node => node.textContent), ['0', '0', '0', '0']);
    h.search('anything');
    h.change('f-scope', 'excluded');
    h.id('reset').click();
    assert.equal(h.id('rows').textContent, h.data.labels.no_records);
  }
});

test('details build lazily, toggle once, and retain expansion after sorting', () => {
  const h = dashboard();
  const detail = h.id('finding-1-detail');
  assert.equal(detail.hidden, true);
  assert.equal(detail.firstChild, null);
  const button = h.id('finding-1').querySelector('.finding-toggle');
  button.click();
  assert.equal(detail.hidden, false);
  assert.equal(button.getAttribute('aria-expanded'), 'true');
  assert.equal(button.getAttribute('aria-controls'), detail.id);
  assert.equal(detail.querySelectorAll('img').length, 0);
  assert.equal(detail.querySelector('.refs').querySelector('a').getAttribute('href'), 'https://example.test/reference_unique_token?q=1&x=2');
  h.change('f-sort', 'id');
  assert.equal(h.id('finding-1-detail').hidden, false);
  h.id('finding-1').children[0].click();
  assert.equal(h.id('finding-1-detail').hidden, true);
  h.id('finding-2').querySelector('.finding-toggle').click();
  assert.ok(h.id('finding-2-detail').textContent.includes(h.data.labels.not_recorded));
});

test('copy exports safe fences and complete details with the Clipboard API', async () => {
  const h = dashboard('en', { clipboard: 'success' });
  const button = copyButton(h);
  button.focus();
  button.click();
  await settle();
  assert.equal(h.copied.length, 1);
  assert.equal(h.fallbacks.length, 0);
  const text = h.copied[0];
  assert.ok(text.includes('`````text\nAction text marker ````\n`````'));
  assert.ok(text.includes('Body marker\nSecond line <&> 日本語'));
  assert.ok(text.includes('>    5 snippet_unique_token'));
  assert.ok(text.includes('<https://example.test/reference_unique_token?q=1&amp;x=2>'));
  assert.equal(button.textContent, h.data.labels.copied);
  h.runTimers();
  assert.equal(button.textContent, h.data.labels.copy_llm);
});

test('copy keeps Markdown syntax and HTML-like inline evidence literal', async () => {
  const h = dashboard('en', { clipboard: 'success', mutate(data) {
    const finding = data.findings[0];
    finding.id = 'F_*[1]';
    finding.title = '<img src=x> &copy; [link](https://example.test/) #\r\n next';
    finding.location = 'src/`entry`_file.py\r\n  second\n\tthird';
    finding.fields = [{ label: '<b>Evidence</b> &copy; #', text: '<b>literal evidence</b>\n[link](target)' }];
    finding.references = [{ type: '[spec]', title: '<title> &copy;',
      url: 'https://example.test/a<b>?one=1&copy;&two=2' }];
  } });
  copyButton(h).click();
  await settle();
  const text = h.copied[0];
  assert.ok(text.startsWith('## F\\_\\*\\[1\\] · \\<img src=x\\> \\&copy; \\[link\\](https://example.test/) \\# next\n'));
  assert.ok(text.includes('- Location: src/\\`entry\\`\\_file.py second third\n'));
  assert.ok(text.includes('### \\<b\\>Evidence\\</b\\> \\&copy; \\#\n```text\n<b>literal evidence</b>\n[link](target)\n```'));
  assert.ok(text.includes('- [\\[spec\\]] \\<title\\> \\&copy; <https://example.test/a%3Cb%3E?one=1&amp;copy;&amp;two=2>'));
});

test('copy keeps custom metadata labels from becoming nested Markdown lists', async () => {
  for (const [label, escaped] of [['- Severity', '\\- Severity'], ['+ Severity', '\\+ Severity'],
    ['1. Severity', '1\\. Severity'], ['1) Severity', '1\\) Severity'],
    [' - Severity', '&#32;- Severity'], ['  + Severity', '&#32;&#32;+ Severity'],
    ['   1. Severity', '&#32;&#32;&#32;1. Severity'], ['\t- Severity', '&#9;- Severity'],
    ['    Severity', '&#32;&#32;&#32;&#32;Severity']]) {
    const h = dashboard('en', { clipboard: 'success', mutate(data) { data.labels.severity = label; } });
    copyButton(h).click();
    await settle();
    assert.ok(h.copied[0].includes('- ' + escaped + ': High\n'), label);
  }
});

test('denied clipboard uses selection copy, restores focus, and cleans up', async () => {
  const h = dashboard('ja', { clipboard: 'reject' });
  const button = copyButton(h);
  button.focus();
  button.click();
  await settle();
  assert.equal(h.fallbacks.length, 1);
  assert.equal(h.document.activeElement, button);
  assert.equal(h.document.querySelectorAll('textarea').length, 0);
  assert.equal(button.textContent, h.data.labels.copied);
});

test('copy failure is reported without leaving a temporary control', async () => {
  const h = dashboard('en', { fallbackSuccess: false });
  const button = copyButton(h, 'finding-2');
  button.click();
  await settle();
  assert.equal(button.textContent, h.data.labels.copy_failed);
  assert.equal(h.document.querySelectorAll('textarea').length, 0);
  h.runTimers();
  assert.equal(button.textContent, h.data.labels.copy_llm);
});

test('excluded records retain status in copied text and suppress action guidance', async () => {
  const h = dashboard('en', { clipboard: 'success' });
  h.change('f-scope', 'excluded');
  const button = copyButton(h, 'finding-5');
  assert.equal(h.id('finding-5-detail').querySelector('.action-guidance'), null);
  button.click();
  await settle();
  assert.ok(h.copied[0].includes('- Status: Accepted · Excluded'));
  assert.ok(!h.copied[0].includes('Not actionable'));
});

test('a large backtick-rich field copies without exceeding the argument stack', async () => {
  const text = '`x'.repeat(150000) + '````';
  const h = dashboard('en', { clipboard: 'success', mutate(data) {
    data.findings[0].fields = [{ label: 'Large evidence', text }];
  } });
  copyButton(h).click();
  await settle();
  assert.equal(h.copied.length, 1);
  assert.ok(h.copied[0].includes('`````text\n' + text + '\n`````'));
});

test('copy handles long whitespace runs without quadratic newline matching', async () => {
  const title = 'Long trailing whitespace' + ' '.repeat(350000);
  const h = dashboard('en', { clipboard: 'success', mutate(data) { data.findings[0].title = title; } });
  copyButton(h).click();
  await settle();
  assert.ok(h.copied[0].startsWith('## F-10 · ' + title + '\n'));
});

test('deep links decode the fragment, reveal excluded rows, and focus their control', () => {
  const h = dashboard('en', { hash: '#finding%2D2' });
  assert.equal(h.id('finding-2-detail').hidden, false);
  assert.equal(h.document.activeElement, h.id('finding-2').querySelector('.finding-toggle'));
  assert.equal(h.id('finding-2').scrolled, true);
  h.search('does not appear');
  h.change('f-sev', 'Low');
  h.window.location.hash = '#finding-5';
  assert.equal(h.id('f-scope').value, 'excluded');
  assert.equal(h.id('f-q').value, '');
  assert.equal(h.id('f-sev').value, '');
  assert.equal(h.id('finding-5-detail').hidden, false);
  assert.equal(h.document.activeElement, h.id('finding-5').querySelector('.finding-toggle'));
  const before = h.ids();
  h.window.location.hash = '#finding-%ZZ';
  assert.deepEqual(h.ids(), before);
  h.window.location.hash = '#missing';
  assert.deepEqual(h.ids(), before);
  const same = dashboard('en', { hash: '#finding-1' });
  same.id('finding-1').querySelector('.finding-toggle').click();
  same.id('priority').querySelector('a').click();
  assert.equal(same.id('finding-1-detail').hidden, false);
});

test('large-register search debounces and reset cancels pending work', () => {
  const h = dashboard('en', { mutate(data) {
    data.findings = Array.from({ length: 301 }, (_, i) => ({
      id: 'F-' + i, anchor: 'finding-' + (i + 1), severity: 'Low', status: 'Open', title: i === 300 ? 'needle' : 'ordinary'
    }));
  } });
  h.search('nee');
  h.search('needle');
  assert.equal(h.rows().length, 301);
  assert.equal(h.timers.size, 1);
  h.runTimers();
  assert.deepEqual(h.ids(), ['F-300']);
  h.search('ordinary');
  h.id('reset').click();
  assert.equal(h.timers.size, 0);
  assert.equal(h.rows().length, 301);
});

test('unknown and inherited template tokens are preserved literally', () => {
  const h = dashboard('en', { mutate(data) {
    data.labels.showing = '{shown}/{total} {unknown} {constructor} {__proto__} {';
  } });
  assert.equal(h.id('result-count').textContent, '5/5 {unknown} {constructor} {__proto__} {');
});

test('an empty All label keeps the unfiltered option and default results', () => {
  const h = dashboard('en', { mutate(data) { data.labels.all = ''; } });
  assert.equal(h.rows().length, 5);
  for (const id of ['f-sev', 'f-status', 'f-cat']) {
    assert.equal(h.id(id).value, '');
    assert.equal(h.id(id).children[0].getAttribute('value'), '');
  }
  h.change('f-sev', 'Low');
  h.id('reset').click();
  assert.equal(h.rows().length, 5);
});

test('theme switches from the operating-system preference and then the explicit choice', () => {
  for (const dark of [false, true]) {
    const h = dashboard('en', { dark });
    h.id('theme').click();
    assert.equal(h.document.documentElement.dataset.theme, dark ? 'light' : 'dark');
    h.id('theme').click();
    assert.equal(h.document.documentElement.dataset.theme, dark ? 'dark' : 'light');
  }
});

(async () => {
  for (const { name, run } of tests) {
    await run();
    process.stdout.write('ok - ' + name + '\n');
  }
  process.stdout.write(tests.length + ' dashboard behaviour tests passed\n');
})().catch(error => { console.error(error); process.exitCode = 1; });
