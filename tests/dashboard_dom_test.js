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
    localStorage: options.localStorage,
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

test('detail prose preserves recorded newlines and supplies breaks only for lone CR', async () => {
  const text = '\rfirst\r\nsecond\nthird\rfourth\r\rfifth\r\r\nsixth\r';
  const h = dashboard('en', { clipboard: 'success', mutate(data) {
    data.findings[0].fields = [{ label: 'Mixed newline evidence', text }];
    data.findings[0].location = 'src/first\rsecond\r\nthird';
  } });
  h.id('finding-1').querySelector('.finding-toggle').click();
  const fields = h.id('finding-1-detail').querySelectorAll('dd');
  const evidence = fields.find(node => node.textContent === text);
  assert.ok(evidence, 'body text remains exactly as recorded');
  assert.equal(evidence.querySelectorAll('br').length, 6);
  const retained = evidence.querySelectorAll('span[hidden]');
  assert.equal(retained.length, 8);
  assert.ok(retained.every(node => node.textContent === '\r'));
  assert.equal(fields[0].textContent, 'src/first\rsecond\r\nthird');
  assert.equal(fields[0].querySelectorAll('br').length, 1);
  h.id('finding-1-detail').querySelector('.copy-finding').click();
  await settle();
  assert.ok(h.copied[0].includes('```text\n' + text + '\n```'), 'copy uses the original evidence');
});

test('overview prose keeps recorded line breaks, as in the printable report', () => {
  const text = 'first\r\nsecond\rthird\nfourth';
  const h = dashboard('en', { mutate(data) {
    data.summary = text;
    data.findings[0].action.text = text;
    data.perspectives = [{ name: 'Perspective', result: text, note: text }, { name: 'Blank', result: ' \n', note: '\r' }];
    for (const key of ['limitations', 'decisions', 'next_steps', 'checked_ok']) data[key] = [text];
  } });
  h.id('finding-1').querySelector('.finding-toggle').click();
  const prose = [
    h.id('summary').querySelector('p'), h.id('priority').querySelector('.action').querySelector('span.text-lines'),
    h.id('finding-1-detail').querySelector('.action-guidance').querySelector('p'),
    ...h.id('perspectives').querySelectorAll('p.text-lines'),
    h.id('limitations').querySelector('li'), ...h.id('lists').querySelectorAll('li')
  ];
  assert.equal(prose.length, 9);
  for (const node of prose) {
    assert.equal(node.textContent, text, 'recorded text is unchanged');
    assert.ok((node.getAttribute('class') || '').split(' ').includes('text-lines'), 'line breaks are displayed');
    assert.equal(node.querySelectorAll('br').length, 1, 'only the lone CR adds a break');
    assert.equal(node.querySelectorAll('span[hidden]').length, 2);
  }
  assert.equal(h.id('priority').querySelector('.action').textContent, 'Action label marker ' + text);
  const blank = h.id('perspectives').children[1];
  assert.deepEqual(blank.querySelectorAll('p').map(node => node.textContent), [h.data.labels.not_recorded]);
});

test('blank summary and action text are absent from the overview and details', () => {
  const h = dashboard('en', { mutate(data) {
    data.summary = ' \r\n\x1c\x85 '; data.findings[0].action.text = '\n';
    data.perspectives = [{ name: 'Blank to Python', result: '\x1f\x85', note: '\u3000' }];
  } });
  h.id('finding-1').querySelector('.finding-toggle').click();
  assert.equal(h.id('summary').querySelector('p'), null);
  assert.equal(h.id('priority').querySelector('.action').textContent, 'Action label marker');
  assert.equal(h.id('finding-1-detail').querySelector('.action-guidance').querySelector('p'), null);
  assert.deepEqual(h.id('perspectives').querySelectorAll('p').map(node => node.textContent), [h.data.labels.not_recorded]);
  // U+FEFF is text to Python's str.strip(), so the printable report keeps it too.
  const bom = dashboard('en', { mutate(data) { data.summary = '\ufeff'; } });
  assert.equal(bom.id('summary').querySelector('p').textContent, '\ufeff');
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

test('severity facets count included records, filter the register, and clear on a second press', () => {
  const h = dashboard();
  const facets = h.id('facets').querySelectorAll('button.facet');
  assert.deepEqual(facets.map(b => b.textContent), ['High3', 'Medium1', 'Low1', 'Info0']);
  h.change('f-scope', 'excluded');
  facets[0].click();
  assert.equal(h.id('f-scope').value, 'included');
  assert.deepEqual(h.ids(), ['F-10', 'F-3', 'F-1']);
  assert.equal(facets[0].getAttribute('aria-pressed'), 'true');
  assert.equal(h.id('findings').scrolled, true);
  facets[0].click();
  assert.equal(h.id('f-sev').value, '');
  assert.equal(facets[0].getAttribute('aria-pressed'), 'false');
  h.change('f-sev', 'Low');
  assert.equal(facets[2].getAttribute('aria-pressed'), 'true');
});

test('keyboard shortcuts search, step through visible findings, and ignore typing', () => {
  const h = dashboard();
  const key = (k, target = h.document.body, extra = {}) => {
    const event = { key: k, target, preventDefault() { this.prevented = true; }, ...extra };
    for (const listener of h.document.listeners.keydown) listener(event);
    return event;
  };
  assert.equal(key('/').prevented, true);
  assert.equal(h.document.activeElement, h.id('f-q'));
  const toggle = anchor => h.id(anchor).querySelector('.finding-toggle');
  key('j');
  assert.equal(h.document.activeElement, toggle('finding-1'));
  key('j', h.document.activeElement);
  assert.equal(h.document.activeElement, toggle('finding-6'));
  key('k', h.document.activeElement);
  assert.equal(h.document.activeElement, toggle('finding-1'));
  key('k', h.document.activeElement);
  assert.equal(h.document.activeElement, toggle('finding-1'));
  // Focus inside an expanded detail row steps from that finding. Compare row
  // ids: a failed assertion on DOM nodes formats the whole cyclic tree.
  const focusedRow = () => h.document.activeElement.closest('tr.row').id;
  const copy = () => h.id('finding-6-detail').querySelector('button.copy-finding');
  toggle('finding-6').click();
  copy().focus();
  key('j', h.document.activeElement);
  assert.equal(focusedRow(), 'finding-3');
  copy().focus();
  key('k', h.document.activeElement);
  assert.equal(focusedRow(), 'finding-1');
  assert.equal(key('j', h.document.body, { metaKey: true }).prevented, undefined);
  h.search('first');
  assert.equal(key('j', h.id('f-q')).prevented, undefined);
  key('Escape', h.id('f-q'));
  assert.equal(h.id('f-q').value, '');
  assert.equal(h.rows().length, 5);
});

test('the theme choice is remembered and storage failures fall back to the system theme', () => {
  const store = new Map();
  const localStorage = { getItem: k => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) };
  const first = dashboard('en', { localStorage });
  first.id('theme').click();
  assert.equal(store.get('scan-report-kit.theme'), 'dark');
  const second = dashboard('en', { localStorage });
  assert.equal(second.document.documentElement.dataset.theme, 'dark');
  const blocked = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } };
  const third = dashboard('en', { localStorage: blocked });
  assert.equal(third.document.documentElement.dataset.theme, undefined);
  third.id('theme').click();
  assert.equal(third.document.documentElement.dataset.theme, 'dark');
});

test('grouped fields become tabs with tables, and copy and search cover every group', async () => {
  const h = dashboard('en', { clipboard: 'success', mutate(data) {
    data.findings[0].fields = [
      { label: 'Ungrouped marker', text: 'shown above the tabs' },
      { label: 'Opportunity', text: 'overview_unique_token', group: 'Overview' },
      { label: 'Facts', group: 'Overview', rows: [['Effort', 'S'], ['Surface', 'Product UI']] },
      { label: 'Rules', text: 'Lead <b>', group: 'Brand', columns: ['ID', 'Rule'], rows: [['VO-02', 'calm_rule_token\nsecond']] }
    ];
  } });
  h.id('finding-1').querySelector('.finding-toggle').click();
  const detail = h.id('finding-1-detail');
  const tabs = detail.querySelectorAll('button[role="tab"]');
  assert.deepEqual(tabs.map(tab => tab.textContent), ['Overview', 'Brand']);
  const panels = detail.querySelectorAll('div[role="tabpanel"]');
  assert.deepEqual(panels.map(panel => panel.hidden), [false, true]);
  assert.equal(tabs[0].getAttribute('aria-selected'), 'true');
  assert.equal(tabs[1].getAttribute('tabindex'), '-1');
  assert.equal(tabs[1].getAttribute('aria-controls'), panels[1].id);
  assert.equal(panels[1].getAttribute('aria-labelledby'), tabs[1].id);
  // Ungrouped fields stay in the list above the tabs.
  assert.ok(detail.querySelector('dl').textContent.includes('shown above the tabs'));
  assert.ok(!detail.querySelector('dl').textContent.includes('overview_unique_token'));
  const facts = panels[0].querySelector('table.field-table');
  assert.deepEqual(facts.querySelectorAll('th').map(th => [th.getAttribute('scope'), th.textContent]), [['row', 'Effort'], ['row', 'Surface']]);
  tabs[1].click();
  assert.deepEqual(panels.map(panel => panel.hidden), [true, false]);
  const rules = panels[1].querySelector('table.field-table');
  assert.deepEqual(rules.querySelector('thead').querySelectorAll('th').map(th => th.textContent), ['ID', 'Rule']);
  assert.equal(rules.querySelector('td').textContent, 'calm_rule_token\nsecond');
  assert.equal(panels[1].querySelector('p').textContent, 'Lead <b>');
  // Arrow keys wrap between tabs and move focus; Home and End reach the ends.
  const list = detail.querySelector('div[role="tablist"]');
  const key = name => { const e = { key: name, preventDefault() { e.prevented = true; }, stopPropagation() {} }; list.onkeydown(e); return e; };
  tabs[1].focus();
  assert.ok(key('ArrowRight').prevented);
  assert.equal(h.document.activeElement, tabs[0]);
  assert.deepEqual(panels.map(panel => panel.hidden), [false, true]);
  key('End');
  assert.equal(h.document.activeElement, tabs[1]);
  key('ArrowLeft');
  assert.equal(h.document.activeElement, tabs[0]);
  assert.equal(key('a').prevented, undefined);
  // Modified arrows stay with the browser (Alt+Left is Back).
  const back = { key: 'ArrowLeft', altKey: true, preventDefault() { back.prevented = true; }, stopPropagation() {} };
  list.onkeydown(back);
  assert.equal(back.prevented, undefined);
  assert.equal(h.document.activeElement, tabs[0]);
  // Redraws (sorting, filtering, searching) rebuild the detail but keep the chosen tab.
  tabs[1].click();
  h.change('f-sort', 'id');
  const kept = h.id('finding-1-detail').querySelectorAll('div[role="tabpanel"]');
  assert.deepEqual(kept.map(panel => panel.hidden), [true, false]);
  assert.equal(h.id('finding-1-detail').querySelectorAll('button[role="tab"]')[1].getAttribute('aria-selected'), 'true');
  detail.querySelector('.copy-finding').click();
  await settle();
  const text = h.copied[0];
  assert.ok(text.indexOf('### Ungrouped marker') < text.indexOf('### Overview'));
  assert.ok(text.includes('### Overview\n\n#### Opportunity\n```text\noverview_unique_token\n```'));
  assert.ok(text.includes('#### Facts\n```text\nEffort | S\nSurface | Product UI\n```'));
  assert.ok(text.includes('### Brand\n\n#### Rules\n```text\nLead <b>\nID | Rule\nVO-02 | calm_rule_token second\n```'));
  for (const term of ['calm_rule_token', 'Product UI', 'Brand']) {
    h.search(term);
    assert.deepEqual(h.ids(), [h.data.findings[0].id], term);
  }
});

test('blank table leads and empty groups are absent from the detail and the copy', async () => {
  const h = dashboard('en', { clipboard: 'success', mutate(data) {
    data.findings[0].fields = [
      { label: 'Req', group: 'Request', text: '   ', rows: [['Method', 'GET']] },
      { label: 'Empty', group: 'Unrecorded', text: '' }
    ];
  } });
  h.id('finding-1').querySelector('.finding-toggle').click();
  const detail = h.id('finding-1-detail');
  assert.equal(detail.querySelectorAll('p.field-lead').length, 0);
  detail.querySelector('.copy-finding').click();
  await settle();
  const text = h.copied[0];
  assert.ok(text.includes('### Request\n\n#### Req\n```text\nMethod | GET\n```'), text);
  assert.ok(!text.includes('Unrecorded'), text);
});

test('blank field text reads as not recorded and is left out of the copy', async () => {
  const h = dashboard('en', { clipboard: 'success', mutate(data) {
    data.findings[0].fields = [
      { label: 'Blank_field', text: ' \r\n\x85 ' }, { label: 'Kept_field', text: 'kept_token' }
    ];
  } });
  h.id('finding-1').querySelector('.finding-toggle').click();
  const detail = h.id('finding-1-detail');
  const dd = detail.querySelector('dl').children;
  const at = dd.findIndex(node => node.textContent === 'Blank_field');
  assert.equal(dd[at + 1].textContent, h.data.labels.not_recorded);
  detail.querySelector('.copy-finding').click();
  await settle();
  assert.ok(!h.copied[0].includes('Blank_field'), h.copied[0]);
  assert.ok(h.copied[0].includes('### Kept\\_field\n```text\nkept_token\n```'), h.copied[0]);
});

test('an action label without text still reaches the copy', async () => {
  for (const text of [undefined, '', ' \n ']) {
    const h = dashboard('en', { clipboard: 'success', mutate(data) {
      if (text === undefined) delete data.findings[0].action.text; else data.findings[0].action.text = text;
    } });
    copyButton(h).click();
    await settle();
    const copied = h.copied[0];
    assert.ok(copied.includes('\n### Action label marker\n'), JSON.stringify(text) + '\n' + copied);
    assert.ok(!/### Action label marker\n`+text/.test(copied), copied);
  }
});

test('a single group reads as part of the detail list, without tabs', () => {
  const h = dashboard('en', { mutate(data) {
    data.findings[0].fields = [{ label: 'Only', text: 'single_group_token', group: 'Overview' }];
  } });
  h.id('finding-1').querySelector('.finding-toggle').click();
  const detail = h.id('finding-1-detail');
  assert.equal(detail.querySelectorAll('button[role="tab"]').length, 0);
  assert.ok(detail.querySelector('dl').textContent.includes('single_group_token'));
});

(async () => {
  for (const { name, run } of tests) {
    await run();
    process.stdout.write('ok - ' + name + '\n');
  }
  process.stdout.write(tests.length + ' dashboard behaviour tests passed\n');
})().catch(error => { console.error(error); process.exitCode = 1; });
