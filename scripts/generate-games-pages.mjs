#!/usr/bin/env node

import { mkdir, rm, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';

const SOURCE_API = 'https://pfs-library.xetdy-am.workers.dev/api/packages';
const OUTPUT_DIR = 'games';
const SOURCES_DIR = `${OUTPUT_DIR}/sources`;
const PAGE_SIZE = 24;
const MAX_NATIVE_JSON_BYTES = 64000;
const MAX_SOURCE_MANIFEST_BYTES = 256000;
const USER_AGENT = 'Z3Shop-Games-Catalog/2.0';
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
  if (['pfs', 'lz4', 'fpkg', 'ffpfsc', 'ffspsc', 'exfat'].includes(pack)) return pack;
  return 'unknown';
}

function sizeLabel(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes <= 0) return 'Unknown size';
  const gib = bytes / 1073741824;
  return `${gib >= 100 ? gib.toFixed(0) : gib.toFixed(1)} GiB`;
}

function decodeSourceUrl(value) {
  const raw = clean(value, 4096);
  if (!raw) return '';
  if (/^https?:\/\//i.test(raw)) return raw;
  try {
    const decoded = Buffer.from(raw, 'base64').toString('utf8').trim();
    return /^https?:\/\//i.test(decoded) ? decoded : '';
  } catch {
    return '';
  }
}

function urlExtension(url) {
  try {
    const u = new URL(url);
    const name = u.pathname.split('/').pop() || '';
    const dot = name.lastIndexOf('.');
    return dot >= 0 ? name.slice(dot).toLowerCase() : '';
  } catch {
    return '';
  }
}

function classifySourceUrl(url) {
  if (!url) return { mode: 'unresolved', direct: false, archive: false, extension: '' };
  const extension = urlExtension(url);
  if (['.pkg', '.ffpkg', '.ffpfsc', '.exfat', '.lz4'].includes(extension)) {
    return { mode: 'direct-file', direct: true, archive: false, extension };
  }
  if (['.rar', '.7z'].includes(extension)) {
    return { mode: 'archive', direct: false, archive: true, extension };
  }
  return { mode: 'provider-page', direct: false, archive: false, extension };
}

function stableSourceId(gameId, link, index) {
  const lid = clean(link?.lid, 80);
  if (lid) return lid;
  return createHash('sha256')
    .update(`${gameId}|${index}|${link?.name || ''}|${link?.url || ''}`)
    .digest('hex')
    .slice(0, 24);
}

function normalizeSources(pkg, gameId) {
  const links = Array.isArray(pkg?.downloadLinks) ? pkg.downloadLinks : [];
  return links.map((link, index) => {
    const url = decodeSourceUrl(link?.url);
    const classification = classifySourceUrl(url);
    const part = Number(link?.part);
    return {
      id: stableSourceId(gameId, link, index),
      provider: clean(link?.name || 'Unnamed', 80),
      url,
      mode: classification.mode,
      direct: classification.direct,
      archive: classification.archive,
      extension: classification.extension,
      group: clean(link?.group, 80),
      part: Number.isInteger(part) && part > 0 ? part : 0,
      dlc: link?.dlc === true,
      credit: clean(link?.credit, 120),
      firmware: clean(link?.firmware, 64),
      created_by: clean(link?.createdBy, 80),
    };
  });
}

function providerNames(sources) {
  const names = [];
  for (const source of sources) {
    const name = clean(source?.provider || 'unnamed', 64);
    if (name && !names.includes(name)) names.push(name);
    if (names.length >= 8) break;
  }
  return names;
}

function normalizeGame(pkg) {
  const id = clean(pkg?.id, 48);
  const sources = normalizeSources(pkg, id);
  const rawFirmware = firmwareList(pkg?.firmware);
  const firmware = normalizedFirmwareList(pkg?.firmware);
  const providers = providerNames(sources);
  const bytes = Number.isFinite(Number(pkg?.sizeBytes)) ? Number(pkg.sizeBytes) : 0;
  const usable = sources.filter((s) => s.url && !s.dlc);
  const direct = usable.filter((s) => s.direct);
  const archives = usable.filter((s) => s.archive);
  const providerPages = usable.filter((s) => s.mode === 'provider-page');
  return {
    game: {
      id,
      title_id: clean(pkg?.titleId, 24),
      title: clean(pkg?.title, 120),
      version: clean(pkg?.version, 32),
      pack: normalizePack(pkg?.pack),
      region: clean(pkg?.region, 48),
      firmware,
      firmware_label: firmware.length ? firmware.join(', ') : 'Unknown',
      firmware_raw: rawFirmware.slice(0, 6),
      apr: pkg?.apr === true,
      apr_version: clean(pkg?.aprVersion, 32),
      dlcs_merged: pkg?.dlcsMerged === true,
      size_bytes: bytes,
      size_label: sizeLabel(bytes),
      poster_url: clean(pkg?.posterUrl, 420),
      banner_url: clean(pkg?.bannerUrl, 420),
      updated_at: clean(pkg?.updatedAt || pkg?.createdAt, 40),
      source_count: sources.length,
      usable_source_count: usable.length,
      direct_source_count: direct.length,
      archive_source_count: archives.length,
      provider_page_count: providerPages.length,
      providers,
      sources_url: `${RAW_BASE}/sources/${encodeURIComponent(id)}.json`,
      source: 'gfs-pfs-library',
    },
    sources,
    metadata: {
      description: clean(pkg?.description, 4000),
      notes: clean(pkg?.notes, 1000),
      credits: clean(pkg?.credits, 1000),
      publisher: clean(pkg?.publisher, 160),
      developer: clean(pkg?.developer, 160),
      players: clean(pkg?.players, 64),
      genres: Array.isArray(pkg?.genres) ? pkg.genres.map((x) => clean(x, 80)).filter(Boolean) : [],
      release_date: clean(pkg?.releaseDate, 32),
      store_url: clean(pkg?.storeUrl, 500),
      trailer: clean(pkg?.trailer, 500),
      available_at: clean(pkg?.availableAt, 80),
      uploading: pkg?.uploading === true,
    },
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
    if (seen.has(item.game.id)) {
      duplicates += 1;
      continue;
    }
    seen.add(item.game.id);
    unique.push(item);
  }
  return { unique, duplicates };
}

async function main() {
  const source = await fetchJson(SOURCE_API);
  if (!source || !Array.isArray(source.packages)) throw new Error('packages array missing');

  const normalized = source.packages
    .map(normalizeGame)
    .filter((entry) => entry.game.id && entry.game.title)
    .sort((a, b) => b.game.updated_at.localeCompare(a.game.updated_at) || a.game.title.localeCompare(b.game.title, 'en', { sensitivity: 'base' }));

  const { unique: entries, duplicates: duplicatesDropped } = dedupeById(normalized);
  if (!entries.length) throw new Error('no valid games after normalization');
  const games = entries.map((entry) => entry.game);

  await rm(OUTPUT_DIR, { recursive: true, force: true });
  await mkdir(SOURCES_DIR, { recursive: true });

  const generated = new Date().toISOString();
  let sourceLinks = 0;
  let decodedLinks = 0;
  let unresolvedLinks = 0;
  let directLinks = 0;
  let archiveLinks = 0;
  let providerPageLinks = 0;

  for (const entry of entries) {
    sourceLinks += entry.sources.length;
    decodedLinks += entry.sources.filter((s) => s.url).length;
    unresolvedLinks += entry.sources.filter((s) => !s.url).length;
    directLinks += entry.sources.filter((s) => s.direct).length;
    archiveLinks += entry.sources.filter((s) => s.archive).length;
    providerPageLinks += entry.sources.filter((s) => s.mode === 'provider-page').length;

    const manifest = {
      name: 'Z3Shop Game Sources',
      version: 1,
      generated,
      source: {
        id: 'gfs-pfs-library',
        api: SOURCE_API,
      },
      game: {
        id: entry.game.id,
        title_id: entry.game.title_id,
        title: entry.game.title,
        version: entry.game.version,
        pack: entry.game.pack,
        region: entry.game.region,
        size_bytes: entry.game.size_bytes,
      },
      metadata: entry.metadata,
      sources: entry.sources,
    };
    const body = `${JSON.stringify(manifest, null, 2)}\n`;
    const bytes = Buffer.byteLength(body, 'utf8');
    if (bytes >= MAX_SOURCE_MANIFEST_BYTES) {
      throw new Error(`sources/${entry.game.id}.json is ${bytes} bytes; manifest budget exceeded`);
    }
    await writeFile(`${SOURCES_DIR}/${entry.game.id}.json`, body, 'utf8');
  }

  const pageCount = Math.ceil(games.length / PAGE_SIZE);
  const pages = [];
  for (let i = 0; i < pageCount; i++) {
    const pageNumber = i + 1;
    const filename = `page-${String(pageNumber).padStart(3, '0')}.json`;
    const chunk = games.slice(i * PAGE_SIZE, (i + 1) * PAGE_SIZE);
    const body = {
      name: 'Z3Shop Games',
      version: 3,
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
    pages.push({ page: pageNumber, count: chunk.length, url: `${RAW_BASE}/${filename}` });
  }

  const search = games.map((g, index) => ({
    id: g.id,
    title_id: g.title_id,
    title: g.title,
    pack: g.pack,
    region: g.region,
    firmware: g.firmware,
    source_count: g.source_count,
    page: Math.floor(index / PAGE_SIZE) + 1,
  }));
  const searchBody = `${JSON.stringify({ version: 3, generated, total: games.length, games: search })}\n`;
  const searchBytes = Buffer.byteLength(searchBody, 'utf8');
  if (searchBytes >= MAX_NATIVE_JSON_BYTES) {
    throw new Error(`games/search.json is ${searchBytes} bytes; native budget is ${MAX_NATIVE_JSON_BYTES - 1}`);
  }
  await writeFile(`${OUTPUT_DIR}/search.json`, searchBody, 'utf8');

  const index = {
    name: 'Z3Shop Games',
    version: 3,
    source: {
      id: 'gfs-pfs-library',
      api: SOURCE_API,
      mode: 'full-source-manifests',
    },
    generated,
    total: games.length,
    page_size: PAGE_SIZE,
    pages: pageCount,
    duplicates_dropped: duplicatesDropped,
    search_bytes: searchBytes,
    source_links: sourceLinks,
    decoded_source_links: decodedLinks,
    unresolved_source_links: unresolvedLinks,
    direct_source_links: directLinks,
    archive_source_links: archiveLinks,
    provider_page_links: providerPageLinks,
    pack_counts: counts(games.map((g) => g.pack)),
    region_counts: counts(games.map((g) => g.region || 'unspecified')),
    apr_enabled: games.filter((g) => g.apr).length,
    dlcs_merged: games.filter((g) => g.dlcs_merged).length,
    search_url: `${RAW_BASE}/search.json`,
    sources_base_url: `${RAW_BASE}/sources`,
    pages_index: pages,
    artwork: {
      mode: 'optimized-local-thumbnails',
      size: 256,
      directory: 'game-art',
    },
    note: 'Game pages contain compact metadata plus a per-game sources_url. Source manifests preserve and decode the upstream provider links instead of discarding them.',
  };
  await writeFile(`${OUTPUT_DIR}/index.json`, `${JSON.stringify(index, null, 2)}\n`, 'utf8');

  console.log(`Generated ${games.length} games, ${sourceLinks} source links (${decodedLinks} decoded, ${unresolvedLinks} unresolved), ${pageCount} pages; dropped ${duplicatesDropped} duplicate IDs.`);
}

main().catch((error) => {
  console.error(error?.stack || error);
  process.exitCode = 1;
});
