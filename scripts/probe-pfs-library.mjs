#!/usr/bin/env node

const ROOT = 'https://pfs-library.xetdy-am.workers.dev/';
const UA = 'Z3Shop-Catalog-Probe/1.1';

async function get(url) {
  const c = new AbortController();
  const t = setTimeout(() => c.abort(), 25000);
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
function maybeB64(value) {
  try {
    const s = Buffer.from(String(value || ''), 'base64').toString('utf8');
    return /^https?:\/\//i.test(s) ? s : '';
  } catch { return ''; }
}
function hostOnly(value) {
  try { return new URL(value).host; } catch { return ''; }
}

const main = await get(ROOT);
console.log(`[probe] ${main.status} ${main.type} ${main.url} bytes=${Buffer.byteLength(main.text)}`);
const scripts = matches(main.text, /<script[^>]+src=["']([^"']+)["']/gi).map(x => abs(main.url,x)).filter(Boolean);
console.log(`[probe] script-srcs=${scripts.length}: ${scripts.join(', ')}`);

for (const scriptUrl of scripts.slice(0,8)) {
  const s = await get(scriptUrl);
  console.log(`[probe:js] ${s.status} ${s.type} ${scriptUrl} bytes=${Buffer.byteLength(s.text)}`);
  for (const m of s.text.matchAll(/fetch\(\s*["'`]([^"'`]+)["'`]/g)) console.log(`[probe:fetch] ${m[1]}`);
}

for (const suffix of ['api/packages', 'api/packages?pack=lz4', 'api/packages?pack=fpkg']) {
  const r = await get(new URL(suffix, ROOT).href);
  console.log(`[probe:api] ${r.status} ${r.type} ${r.url} bytes=${Buffer.byteLength(r.text)}`);
  if (!r.ok && r.status >= 400) continue;
  let data;
  try { data = JSON.parse(r.text); } catch (e) { console.log(`[probe:json-warn] ${e.message}`); continue; }
  const packages = Array.isArray(data.packages) ? data.packages : [];
  const allKeys = uniq(packages.flatMap(p => Object.keys(p || {}))).sort();
  console.log(`[probe:summary] endpoint=${suffix} packages=${packages.length} keys=${allKeys.join(',')}`);
  const fieldCounts = {};
  for (const p of packages) for (const k of ['pack','format','type','titleId','version','region','firmware','apr','aprVersion','dlcsMerged','downloadLinks','posterUrl','bannerUrl','sizeBytes']) {
    if (p?.[k] !== undefined && p?.[k] !== null && p?.[k] !== '') fieldCounts[k] = (fieldCounts[k] || 0) + 1;
  }
  console.log(`[probe:fields] ${JSON.stringify(fieldCounts)}`);
  const variants = {};
  for (const k of ['pack','format','type','region']) variants[k] = uniq(packages.map(p => String(p?.[k] ?? '')).filter(Boolean)).sort().slice(0,100);
  console.log(`[probe:variants] ${JSON.stringify(variants)}`);
  let links = 0, decoded = 0;
  const hosts = new Map();
  for (const p of packages) for (const l of (Array.isArray(p.downloadLinks) ? p.downloadLinks : [])) {
    links++;
    const u = /^https?:\/\//i.test(String(l?.url || '')) ? String(l.url) : maybeB64(l?.url);
    if (u) {
      decoded++;
      const h = hostOnly(u) || '(unknown)';
      hosts.set(h, (hosts.get(h) || 0) + 1);
    }
  }
  console.log(`[probe:links] links=${links} decodable=${decoded} hosts=${JSON.stringify([...hosts.entries()].sort((a,b)=>b[1]-a[1]).slice(0,30))}`);
  for (const p of packages.slice(0,12)) {
    console.log('[probe:item] ' + JSON.stringify({
      id:p.id,titleId:p.titleId,title:p.title,version:p.version,region:p.region,firmware:p.firmware,
      pack:p.pack,format:p.format,type:p.type,apr:p.apr,aprVersion:p.aprVersion,dlcsMerged:p.dlcsMerged,
      sizeBytes:p.sizeBytes,links:Array.isArray(p.downloadLinks)?p.downloadLinks.map(l=>l?.name):[],poster:!!p.posterUrl,banner:!!p.bannerUrl,
    }));
  }
}
