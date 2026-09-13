#!/usr/bin/env node

const ROOT = 'https://pfs-library.xetdy-am.workers.dev/';
const UA = 'Z3Shop-Catalog-Probe/1.0';

async function get(url) {
  const c = new AbortController();
  const t = setTimeout(() => c.abort(), 20000);
  try {
    const r = await fetch(url, {
      headers: { 'User-Agent': UA, Accept: 'text/html,application/json,text/javascript,*/*;q=0.8' },
      redirect: 'follow', signal: c.signal,
    });
    const text = await r.text();
    return { url: r.url, status: r.status, type: r.headers.get('content-type') || '', text };
  } finally { clearTimeout(t); }
}

function uniq(xs) { return [...new Set(xs)]; }
function abs(base, value) { try { return new URL(value, base).href; } catch { return ''; } }
function matches(text, re) { const out=[]; for (const m of text.matchAll(re)) out.push(m[1]); return uniq(out); }

const main = await get(ROOT);
console.log(`[probe] ${main.status} ${main.type} ${main.url} bytes=${Buffer.byteLength(main.text)}`);
console.log('[probe:first] ' + main.text.slice(0, 12000).replace(/\s+/g, ' '));

const scripts = matches(main.text, /<script[^>]+src=["']([^"']+)["']/gi).map(x => abs(main.url,x)).filter(Boolean);
const hrefs = matches(main.text, /(?:href|src|data-url|data-href)=["']([^"']+)["']/gi).map(x => abs(main.url,x)).filter(Boolean);
const direct = uniq([...hrefs, ...matches(main.text, /["'](https?:\/\/[^"'<>\s]+)["']/gi)])
  .filter(u => /\.(?:pkg|lz4|ffpfs|ffpfsc|exfat|json)(?:[?#]|$)/i.test(u));
console.log(`[probe] script-srcs=${scripts.length}`);
for (const u of scripts.slice(0,20)) console.log(`[probe:script] ${u}`);
console.log(`[probe] direct-candidates=${direct.length}`);
for (const u of direct.slice(0,100)) console.log(`[probe:direct] ${u}`);

const endpointCandidates = new Set();
for (const scriptUrl of scripts.slice(0,12)) {
  try {
    const s = await get(scriptUrl);
    console.log(`[probe:js] ${s.status} ${s.type} ${scriptUrl} bytes=${Buffer.byteLength(s.text)}`);
    const pats = [
      /fetch\(\s*["'`]([^"'`]+)["'`]/g,
      /["'`]((?:\/api\/|api\/)[^"'`\s]+)["'`]/g,
      /["'`]([^"'`\s]+\.json(?:\?[^"'`]*)?)["'`]/g,
    ];
    for (const re of pats) for (const m of s.text.matchAll(re)) {
      const u = abs(scriptUrl, m[1]);
      if (u && new URL(u).origin === new URL(ROOT).origin) endpointCandidates.add(u);
    }
    const assetUrls = matches(s.text, /["'`](https?:\/\/[^"'`\s]+\.(?:pkg|lz4|ffpfs|ffpfsc|exfat)(?:\?[^"'`]*)?)["'`]/gi);
    for (const u of assetUrls.slice(0,100)) console.log(`[probe:asset-js] ${u}`);
  } catch (e) { console.log(`[probe:js-warn] ${scriptUrl}: ${e.message}`); }
}

console.log(`[probe] endpoints=${endpointCandidates.size}`);
for (const u of [...endpointCandidates].slice(0,30)) {
  try {
    const r = await get(u);
    console.log(`[probe:endpoint] ${r.status} ${r.type} ${u} bytes=${Buffer.byteLength(r.text)} preview=${r.text.slice(0,3000).replace(/\s+/g,' ')}`);
  } catch (e) { console.log(`[probe:endpoint-warn] ${u}: ${e.message}`); }
}
