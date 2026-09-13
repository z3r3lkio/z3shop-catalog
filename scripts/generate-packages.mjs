#!/usr/bin/env node

import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';

const OUTPUT = process.env.CATALOG_OUTPUT || 'packages.json';
const USER_AGENT = 'Z3Shop-Catalog/1.0';
const MAX_PACKAGES = 32; // must stay aligned with the native client for now
const GITHUB_TOKEN = process.env.GITHUB_TOKEN || '';

const SOURCES = Object.freeze({
  nexgen: {
    id: 'nexgen-aio-pkg',
    url: 'https://raw.githubusercontent.com/nexgen999/PS5-Super-PLDMGR-Auto-Updater/main/PKGjson/pkg.json',
    homepage: 'https://github.com/nexgen999/PS5-Super-PLDMGR-Auto-Updater',
    priority: 30,
  },
  websrv: {
    id: 'ps5-payload-dev-websrv',
    release: 'https://api.github.com/repos/ps5-payload-dev/websrv/releases/latest',
    homepage: 'https://github.com/ps5-payload-dev/websrv',
    priority: 100,
  },
  ps5shop: {
    id: 'ps5xploit-ps5shopappkg',
    // Deliberately pinned to the homebrew-store release. The repository also has
    // unrelated game releases, so /releases/latest must never be used here.
    release: 'https://api.github.com/repos/ps5xploit/ps5shopappkg/releases/tags/ps5shopappkg',
    homepage: 'https://github.com/ps5xploit/ps5shopappkg',
    priority: 95,
  },
  prospero: [
    {
      id: 'prospero-light',
      repo: 'blackbearreloaded/ProsperoLight',
      name: 'ProsperoLight',
      description: 'PS5 Moonlight client for game streaming.',
      icon: 'https://raw.githubusercontent.com/blackbearreloaded/ProsperoLight/main/sce_sys/icon0.png',
      priority: 110,
      featured: true,
    },
    {
      id: 'prospero-radio',
      repo: 'blackbearreloaded/ProsperoRadio',
      name: 'ProsperoRadio',
      description: 'Native PS5 internet radio player.',
      icon: 'https://raw.githubusercontent.com/blackbearreloaded/ProsperoRadio/main/sce_sys/icon0.png',
      priority: 110,
      featured: true,
    },
    {
      id: 'prospero-tv',
      repo: 'blackbearreloaded/ProsperoTV',
      name: 'ProsperoTV',
      description: 'Native PS5 IPTV player.',
      icon: 'https://raw.githubusercontent.com/blackbearreloaded/ProsperoTV/main/sce_sys/icon0.png',
      priority: 110,
      featured: true,
    },
  ],
});

const FEATURED_IDS = new Set([
  'ps5-homebrew-launcher',
  'prospero-light',
  'prospero-radio',
  'prospero-tv',
  'itemzflow-game-manager',
  'ps5-xplorer',
]);

const ICON_OVERRIDES = new Map([
  ['ps5-homebrew-launcher', 'https://raw.githubusercontent.com/ps5-payload-dev/websrv/master/icon0.png'],
  ['prospero-light', 'https://raw.githubusercontent.com/blackbearreloaded/ProsperoLight/main/sce_sys/icon0.png'],
  ['prospero-radio', 'https://raw.githubusercontent.com/blackbearreloaded/ProsperoRadio/main/sce_sys/icon0.png'],
  ['prospero-tv', 'https://raw.githubusercontent.com/blackbearreloaded/ProsperoTV/main/sce_sys/icon0.png'],
]);

const HOMEPAGE_OVERRIDES = new Map([
  ['ps5-homebrew-launcher', 'https://github.com/ps5-payload-dev/websrv'],
  ['prospero-light', 'https://github.com/blackbearreloaded/ProsperoLight'],
  ['prospero-radio', 'https://github.com/blackbearreloaded/ProsperoRadio'],
  ['prospero-tv', 'https://github.com/blackbearreloaded/ProsperoTV'],
  ['ps5-shop-appkg', 'https://github.com/ps5xploit/ps5shopappkg'],
  ['itemzflow-game-manager', 'https://github.com/LightningMods/Itemzflow'],
]);

function clean(value, max = 220) {
  return String(value ?? '').replace(/\s+/g, ' ').trim().slice(0, max);
}

function slug(value) {
  return clean(value, 120)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 47) || 'package';
}

function canonicalId(name) {
  const id = slug(name);
  if (['homebrewloader', 'homebrew-loader', 'ps5-homebrew-launcher'].includes(id)) return 'ps5-homebrew-launcher';
  if (id === 'prosperolight') return 'prospero-light';
  if (id === 'prosperoradio') return 'prospero-radio';
  if (id === 'prosperotv') return 'prospero-tv';
  if (id === 'ps5-shop-appkg') return 'ps5-shop-appkg';
  if (id === 'itemzflow-game-manager') return 'itemzflow-game-manager';
  return id;
}

function headersFor(url) {
  const headers = {
    Accept: 'application/vnd.github+json, application/json;q=0.9, */*;q=0.8',
    'User-Agent': USER_AGENT,
    'X-GitHub-Api-Version': '2022-11-28',
  };
  if (GITHUB_TOKEN && url.startsWith('https://api.github.com/')) {
    headers.Authorization = `Bearer ${GITHUB_TOKEN}`;
  }
  return headers;
}

async function fetchJson(url) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch(url, {
      headers: headersFor(url),
      signal: controller.signal,
      redirect: 'follow',
    });
    if (!response.ok) throw new Error(`HTTP ${response.status} ${response.statusText}`);
    return await response.json();
  } finally {
    clearTimeout(timeout);
  }
}

function packageTypeFromUrl(url) {
  const lower = String(url || '').toLowerCase();
  if (lower.endsWith('.ffpfsc')) return 'ffspsc'; // native UI currently names the FFPFSC section FFSPSC
  if (lower.endsWith('.lz4')) return 'lz4';
  return 'app';
}

function normalizePackage(input, priority, sourceId) {
  const id = canonicalId(input.id || input.name);
  const url = clean(input.url, 320);
  const type = clean(input.type || packageTypeFromUrl(url), 20).toLowerCase();
  const pkg = {
    id,
    name: clean(input.name, 72) || id,
    description: clean(input.description, 220),
    type,
    category: clean(input.category || 'apps', 24).toLowerCase(),
    featured: Boolean(input.featured || FEATURED_IDS.has(id)),
    redistributable: input.redistributable !== false,
    installable: input.installable !== false && Boolean(url),
    url,
    icon_url: clean(input.icon_url || ICON_OVERRIDES.get(id) || '', 320),
    version: clean(input.version, 48),
    author: clean(input.author, 72),
    homepage: clean(input.homepage || HOMEPAGE_OVERRIDES.get(id) || '', 220),
    release_notes: clean(input.release_notes || '', 220),
  };
  return { pkg, priority, sourceId };
}

function mergeEntries(entries) {
  const byId = new Map();
  for (const entry of entries) {
    if (!entry?.pkg?.id) continue;
    const current = byId.get(entry.pkg.id);
    if (!current) {
      byId.set(entry.pkg.id, entry);
      continue;
    }

    const winner = entry.priority >= current.priority ? entry : current;
    const fallback = winner === entry ? current : entry;
    const merged = { ...winner.pkg };
    for (const key of ['description', 'url', 'icon_url', 'version', 'author', 'homepage', 'release_notes']) {
      if (!merged[key] && fallback.pkg[key]) merged[key] = fallback.pkg[key];
    }
    merged.featured = Boolean(winner.pkg.featured || fallback.pkg.featured);
    merged.redistributable = Boolean(winner.pkg.redistributable && fallback.pkg.redistributable);
    merged.installable = Boolean(merged.url && (winner.pkg.installable || fallback.pkg.installable));
    byId.set(entry.pkg.id, { ...winner, pkg: merged });
  }
  return [...byId.values()].map((entry) => entry.pkg);
}

async function ingestNexgen() {
  const data = await fetchJson(SOURCES.nexgen.url);
  if (!data || !Array.isArray(data.packages)) throw new Error('packages array missing');
  return data.packages.map((item) => {
    const url = clean(item.url, 320);
    const type = packageTypeFromUrl(url);
    const id = canonicalId(item.name);
    return normalizePackage({
      id,
      name: item.name,
      description: item.description,
      type,
      category: 'apps',
      featured: FEATURED_IDS.has(id),
      redistributable: true,
      installable: Boolean(url),
      url,
      version: item.version,
      author: item.author,
      homepage: HOMEPAGE_OVERRIDES.get(id) || SOURCES.nexgen.homepage,
      release_notes: `Discovered via ${SOURCES.nexgen.id}`,
    }, SOURCES.nexgen.priority, SOURCES.nexgen.id);
  });
}

async function ingestProspero(def) {
  const api = `https://api.github.com/repos/${def.repo}/releases/latest`;
  const release = await fetchJson(api);
  const asset = Array.isArray(release.assets)
    ? release.assets.find((a) => String(a.name || '').toLowerCase().endsWith('.ffpfsc'))
    : null;
  if (!asset?.browser_download_url) throw new Error('latest release has no .ffpfsc asset');
  return [normalizePackage({
    id: def.id,
    name: def.name,
    description: def.description,
    type: 'ffspsc',
    category: 'apps',
    featured: def.featured,
    redistributable: true,
    installable: true,
    url: asset.browser_download_url,
    icon_url: def.icon,
    version: release.tag_name || release.name,
    author: def.repo.split('/')[0],
    homepage: `https://github.com/${def.repo}`,
    release_notes: release.name || release.tag_name || 'Latest release',
  }, def.priority, def.id)];
}

async function ingestWebsrvLauncher() {
  const release = await fetchJson(SOURCES.websrv.release);
  return [normalizePackage({
    id: 'ps5-homebrew-launcher',
    name: 'PS5 Homebrew Launcher',
    description: 'Launch and manage PS5 homebrew content from the ps5-payload-dev websrv launcher.',
    type: 'app',
    category: 'apps',
    featured: true,
    redistributable: true,
    installable: true,
    url: 'https://github.com/ps5-payload-dev/websrv/raw/refs/heads/master/homebrew/IV9999-FAKE00000_00-HOMEBREWLOADER01.pkg',
    icon_url: 'https://raw.githubusercontent.com/ps5-payload-dev/websrv/master/icon0.png',
    version: release.tag_name || release.name || 'current',
    author: 'ps5-payload-dev',
    homepage: SOURCES.websrv.homepage,
    release_notes: release.name || release.tag_name || 'Latest websrv release',
  }, SOURCES.websrv.priority, SOURCES.websrv.id)];
}

async function ingestPs5Shop() {
  const release = await fetchJson(SOURCES.ps5shop.release);
  const asset = Array.isArray(release.assets)
    ? release.assets.find((a) => String(a.name || '').toLowerCase() === 'ps5-shop-appkg.pkg')
    : null;
  if (!asset?.browser_download_url) throw new Error('pinned homebrew-store release has no PS5-SHOP-APPKG.pkg');
  return [normalizePackage({
    id: 'ps5-shop-appkg',
    name: 'PS5-SHOP-APPKG',
    description: 'PS5 browser-based homebrew apps store package.',
    type: 'app',
    category: 'apps',
    featured: false,
    redistributable: true,
    installable: true,
    url: asset.browser_download_url,
    version: '1.0.0',
    author: 'ps5xploit',
    homepage: SOURCES.ps5shop.homepage,
    release_notes: 'Pinned to the ps5shopappkg homebrew-store release tag.',
  }, SOURCES.ps5shop.priority, SOURCES.ps5shop.id)];
}

function validate(packages) {
  if (!Array.isArray(packages) || packages.length === 0) throw new Error('catalog would be empty');
  if (packages.length > MAX_PACKAGES) throw new Error(`catalog has ${packages.length} packages; native limit is ${MAX_PACKAGES}`);

  const ids = new Set();
  const allowedTypes = new Set(['app', 'fpkg', 'lz4', 'exfat', 'ffspsc']);
  for (const pkg of packages) {
    if (!pkg.id || ids.has(pkg.id)) throw new Error(`duplicate/empty id: ${pkg.id || '(empty)'}`);
    ids.add(pkg.id);
    if (!allowedTypes.has(pkg.type)) throw new Error(`${pkg.id}: unsupported type ${pkg.type}`);
    if (pkg.installable && !/^https:\/\//i.test(pkg.url)) throw new Error(`${pkg.id}: installable URL must be HTTPS`);
    if (pkg.icon_url && !/^https:\/\//i.test(pkg.icon_url)) throw new Error(`${pkg.id}: icon URL must be HTTPS`);
    if (pkg.icon_url && !/\.png(?:$|\?)/i.test(pkg.icon_url)) throw new Error(`${pkg.id}: native icon decoder currently accepts PNG only`);
    if (!pkg.redistributable) throw new Error(`${pkg.id}: generator only emits redistributable/public homebrew entries`);
  }
}

function stableComparable(catalog) {
  const copy = structuredClone(catalog);
  delete copy._generated;
  return JSON.stringify(copy);
}

async function readExisting() {
  try {
    return JSON.parse(await readFile(OUTPUT, 'utf8'));
  } catch {
    return null;
  }
}

async function main() {
  const entries = [];
  const sourceMeta = [];

  const jobs = [
    { id: SOURCES.nexgen.id, url: SOURCES.nexgen.url, fn: ingestNexgen },
    { id: SOURCES.websrv.id, url: SOURCES.websrv.release, fn: ingestWebsrvLauncher },
    { id: SOURCES.ps5shop.id, url: SOURCES.ps5shop.release, fn: ingestPs5Shop },
    ...SOURCES.prospero.map((def) => ({
      id: def.id,
      url: `https://api.github.com/repos/${def.repo}/releases/latest`,
      fn: () => ingestProspero(def),
    })),
  ];

  const results = await Promise.allSettled(jobs.map((job) => job.fn()));
  results.forEach((result, index) => {
    const job = jobs[index];
    if (result.status === 'fulfilled') {
      entries.push(...result.value);
      sourceMeta.push({ id: job.id, url: job.url });
      console.log(`[source:ok] ${job.id}: ${result.value.length} entries`);
    } else {
      console.warn(`[source:warn] ${job.id}: ${result.reason?.message || result.reason}`);
    }
  });

  if (!entries.length) throw new Error('all catalog sources failed');

  const packages = mergeEntries(entries)
    .filter((pkg) => pkg.redistributable)
    .sort((a, b) => {
      if (a.featured !== b.featured) return a.featured ? -1 : 1;
      if (a.type !== b.type) return a.type.localeCompare(b.type);
      return a.name.localeCompare(b.name, 'en', { sensitivity: 'base' });
    })
    .slice(0, MAX_PACKAGES);

  validate(packages);

  const existing = await readExisting();
  const catalog = {
    name: 'Z3Shop',
    version: 6,
    packages,
    _sources: sourceMeta.sort((a, b) => a.id.localeCompare(b.id)),
    _stats: {
      packages: packages.length,
      featured: packages.filter((p) => p.featured).length,
      apps: packages.filter((p) => p.type === 'app').length,
      ffspsc: packages.filter((p) => p.type === 'ffspsc').length,
    },
  };

  if (existing && stableComparable(existing) === stableComparable(catalog)) {
    catalog._generated = existing._generated || new Date().toISOString();
    console.log(`No catalog change (${packages.length} packages).`);
    return;
  }

  catalog._generated = new Date().toISOString();
  const body = `${JSON.stringify(catalog, null, 2)}\n`;
  await writeFile(OUTPUT, body, 'utf8');
  const digest = createHash('sha256').update(body, 'utf8').digest('hex');
  console.log(`Wrote ${OUTPUT}: ${packages.length} packages, sha256=${digest}`);
}

main().catch((error) => {
  console.error(error?.stack || error);
  process.exitCode = 1;
});
