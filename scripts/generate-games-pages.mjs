#!/usr/bin/env node

import { mkdir, rm, writeFile } from 'node:fs/promises';

const SOURCE_API = 'https://pfs-library.xetdy-am.workers.dev/api/packages';
const OUTPUT_DIR = 'games';
const PAGE_SIZE = 24;
const MAX_NATIVE_JSON_BYTES = 64000;
const USER_AGENT = 'Z3Shop-Games-Catalog/1.0';
const RAW_BASE = 'https://raw.githubusercontent.com/z3r3lkio/z3shop-catalog/main/games';

async function fetchJson(url) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 25000);
  try {
    const response = await fetch(url, {
      headers: { Accept: 'application/json', 'User-Agent': USER_AGENT },
      redirect: 'follow',
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`HTTP ${response.status} ${response.statusText}`);
    return await response.json();
  } finally {
    clearTimeout(timeout);
  }
}

function clean(value, max = 240) {
  return String(value ?? '').replace(/\s+/g, ' ').trim().slice(0, max);
}

function firmwareList(value) {
  const list = Array.isArray(value) ? value : value == null || value === '' ? [] : [value];
  return list.map((item) => clean(item, 48)).filter(Boolean);
}

function normalizeFirmware(value) {
  const raw = clean(value, 48).toLowerCase();
  const m = raw.match(/(\d{1,2})\s*\.?(?:xx|x)?/i);
  if (!m) return '';
  const major = Number(m[1]);
  if (!Number.isFinite(major) || major < 1 || major > 99) return '';
  const backport = /back\s*p(?:ort|ork)/i.test(raw);
  return `${major}.xx${backport ? '-backport' : '+'}`;
}

function normalizedFirmwareList(value) {
  const out = [];
  for (const item of firmwareList(value)) {
    const normalized = normalizeFirmware(item);
    if (normalized && !out.includes(normalized)) out.push(normalized);
  }
  return out;
}

function normalizePack(value) {
  const pack = clean(value, 16).toLowerCase();
  if (pack === 'pfs' || pack === 'lz4' || pack === 'fpkg') return pack;
  return 'unknown';
}

function providerNames(pkg) {
  const names = [];
  for (const link of Array.isArray(pkg?.downloadLinks) ? pkg.downloadLinks : []) {
    const name = clean(link?.name || 'unnamed', 64);
    if (name && !names.includes(name)) names.push(name);
    if (names.length >= 8) break;
  }
  return names;
}

function normalizeGame(pkg) {
  const rawFirmware = firmwareList(pkg?.firmware);
  const providers = providerNames(pkg);
  return {
    id: clean(pkg?.id, 48),
    title_id: clean(pkg?.titleId, 24),
    title: clean(pkg?.title, 120),
    version: clean(pkg?.version, 32),
    pack: normalizePack(pkg?.pack),
    region: clean(pkg?.region, 48),
    firmware: normalizedFirmwareList(pkg?.firmware),
    firmware_raw: rawFirmware.slice(0, 6),
    apr: pkg?.apr === true,
    apr_version: clean(pkg?.aprVersion, 32),
    dlcs_merged: pkg?.dlcsMerged === true,
    size_bytes: Number.isFinite(Number(pkg?.sizeBytes)) ? Number(pkg.sizeBytes) : 0,
    poster_url: clean(pkg?.posterUrl, 420),
    banner_url: clean(pkg?.bannerUrl, 420),
    updated_at: clean(pkg?.updatedAt || pkg?.createdAt, 40),
    link_count: Array.isArray(pkg?.downloadLinks) ? pkg.downloadLinks.length : 0,
    providers,
    source: 'gfs-pfs-library',
  };
}

function counts(values) {
  const out = {};
  for (const value of values) out[value] = (out[value] || 0) + 1;
  return Object.fromEntries(Object.entries(out).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])));
}

function dedupeById(items) {
  const seen = new Set();
  const unique = [];
  let duplicates = 0;
  for (const item of items) {
    if (seen.has(item.id)) {
      duplicates += 1;
      continue;
    }
    seen.add(item.id);
    unique.push(item);
  }
  return { unique, duplicates };
}

async function main() {
  const source = await fetchJson(SOURCE_API);
  if (!source || !Array.isArray(source.packages)) throw new Error('packages array missing');

  const normalized = source.packages
    .map(normalizeGame)
    .filter((g) => g.id && g.title)
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at) || a.title.localeCompare(b.title, 'en', { sensitivity: 'base' }));

  const { unique: games, duplicates: duplicatesDropped } = dedupeById(normalized);
  if (!games.length) throw new Error('no valid games after normalization');

  await rm(OUTPUT_DIR, { recursive: true, force: true });
  await mkdir(OUTPUT_DIR, { recursive: true });

  const pageCount = Math.ceil(games.length / PAGE_SIZE);
  const generated = new Date().toISOString();
  const pages = [];

  for (let i = 0; i < pageCount; i++) {
    const pageNumber = i + 1;
    const filename = `page-${String(pageNumber).padStart(3, '0')}.json`;
    const chunk = games.slice(i * PAGE_SIZE, (i + 1) * PAGE_SIZE);
    const body = {
      name: 'Z3Shop Games',
      version: 1,
      source: 'gfs-pfs-library',
      page: pageNumber,
      page_size: PAGE_SIZE,
      total: games.length,
      pages: pageCount,
      generated,
      games: chunk,
    };
    const pageBody = `${JSON.stringify(body, null, 2)}\n`;
    if (Buffer.byteLength(pageBody, 'utf8') >= MAX_NATIVE_JSON_BYTES) {
      throw new Error(`${filename} exceeds native JSON budget`);
    }
    await writeFile(`${OUTPUT_DIR}/${filename}`, pageBody, 'utf8');
    pages.push({
      page: pageNumber,
      count: chunk.length,
      url: `${RAW_BASE}/${filename}`,
    });
  }

  const search = games.map((g, index) => ({
    id: g.id,
    title_id: g.title_id,
    title: g.title,
    pack: g.pack,
    region: g.region,
    firmware: g.firmware,
    page: Math.floor(index / PAGE_SIZE) + 1,
  }));
  const searchBody = `${JSON.stringify({ version: 1, generated, total: games.length, games: search })}\n`;
  const searchBytes = Buffer.byteLength(searchBody, 'utf8');
  if (searchBytes >= MAX_NATIVE_JSON_BYTES) {
    throw new Error(`games/search.json is ${searchBytes} bytes; native budget is ${MAX_NATIVE_JSON_BYTES - 1}`);
  }
  await writeFile(`${OUTPUT_DIR}/search.json`, searchBody, 'utf8');

  const index = {
    name: 'Z3Shop Games',
    version: 1,
    source: {
      id: 'gfs-pfs-library',
      api: SOURCE_API,
      mode: 'metadata-only',
    },
    generated,
    total: games.length,
    page_size: PAGE_SIZE,
    pages: pageCount,
    duplicates_dropped: duplicatesDropped,
    search_bytes: searchBytes,
    pack_counts: counts(games.map((g) => g.pack)),
    region_counts: counts(games.map((g) => g.region || 'unspecified')),
    apr_enabled: games.filter((g) => g.apr).length,
    dlcs_merged: games.filter((g) => g.dlcs_merged).length,
    search_url: `${RAW_BASE}/search.json`,
    pages_index: pages,
    note: 'Metadata feed only. Provider download URLs are intentionally not mirrored in this catalog.',
  };
  await writeFile(`${OUTPUT_DIR}/index.json`, `${JSON.stringify(index, null, 2)}\n`, 'utf8');

  console.log(`Generated ${games.length} unique games across ${pageCount} pages (${PAGE_SIZE}/page); dropped ${duplicatesDropped} duplicate IDs; search index ${searchBytes} bytes.`);
}

main().catch((error) => {
  console.error(error?.stack || error);
  process.exitCode = 1;
});
