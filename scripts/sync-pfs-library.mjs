#!/usr/bin/env node

import { readFile, writeFile } from 'node:fs/promises';

const CATALOG_PATH = process.env.CATALOG_OUTPUT || 'packages.json';
const SOURCE_ID = 'gfs-pfs-library';
const SOURCE_HOME = 'https://pfs-library.xetdy-am.workers.dev/';
const SOURCE_API = 'https://pfs-library.xetdy-am.workers.dev/api/packages';
const USER_AGENT = 'Z3Shop-Catalog-PFS-Library/1.0';
const FETCH_TIMEOUT_MS = 25000;
const LATEST_SAMPLE = 12;

async function fetchSource() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const response = await fetch(SOURCE_API, {
      headers: {
        Accept: 'application/json',
        'User-Agent': USER_AGENT,
      },
      redirect: 'follow',
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`HTTP ${response.status} ${response.statusText}`);
    const data = await response.json();
    if (!data || !Array.isArray(data.packages)) throw new Error('packages array missing');
    return data;
  } finally {
    clearTimeout(timeout);
  }
}

function inc(map, key) {
  const normalized = String(key ?? '').trim() || 'unspecified';
  map[normalized] = (map[normalized] || 0) + 1;
}

function compactCounts(map) {
  return Object.fromEntries(
    Object.entries(map).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
  );
}

function firmwareValues(value) {
  if (Array.isArray(value)) return value.map(String).map((x) => x.trim()).filter(Boolean);
  if (value === null || value === undefined || value === '') return [];
  return [String(value).trim()].filter(Boolean);
}

function analyze(data) {
  const packages = data.packages;
  const packs = {};
  const regions = {};
  const firmware = {};
  const linkProviders = {};
  const uniqueTitleIds = new Set();
  let explicitPackEntries = 0;
  let aprEnabled = 0;
  let dlcsMerged = 0;
  let totalLinks = 0;
  let withPoster = 0;
  let withBanner = 0;

  for (const pkg of packages) {
    const pack = String(pkg?.pack ?? '').trim().toLowerCase();
    if (pack) explicitPackEntries++;
    inc(packs, pack || 'unspecified');
    inc(regions, pkg?.region);
    for (const fw of firmwareValues(pkg?.firmware)) inc(firmware, fw);
    if (pkg?.apr === true) aprEnabled++;
    if (pkg?.dlcsMerged === true) dlcsMerged++;
    if (pkg?.posterUrl) withPoster++;
    if (pkg?.bannerUrl) withBanner++;
    if (pkg?.titleId) uniqueTitleIds.add(String(pkg.titleId));
    const links = Array.isArray(pkg?.downloadLinks) ? pkg.downloadLinks : [];
    totalLinks += links.length;
    for (const link of links) inc(linkProviders, link?.name || 'unnamed');
  }

  const latest = packages
    .filter((pkg) => pkg && pkg.title)
    .slice()
    .sort((a, b) => String(b.updatedAt || b.createdAt || '').localeCompare(String(a.updatedAt || a.createdAt || '')))
    .slice(0, LATEST_SAMPLE)
    .map((pkg) => ({
      id: String(pkg.id || ''),
      title_id: String(pkg.titleId || ''),
      title: String(pkg.title || '').slice(0, 100),
      version: String(pkg.version || '').slice(0, 32),
      pack: String(pkg.pack || 'unspecified').toLowerCase(),
      region: String(pkg.region || '').slice(0, 40),
      firmware: firmwareValues(pkg.firmware).slice(0, 6),
      apr: pkg.apr === true,
      apr_version: String(pkg.aprVersion || '').slice(0, 32),
      dlcs_merged: pkg.dlcsMerged === true,
      size_bytes: Number.isFinite(Number(pkg.sizeBytes)) ? Number(pkg.sizeBytes) : 0,
      poster_url: String(pkg.posterUrl || '').slice(0, 400),
      updated_at: String(pkg.updatedAt || pkg.createdAt || '').slice(0, 40),
    }));

  return {
    source_name: String(data.name || 'GFS Catalog').slice(0, 80),
    homepage: SOURCE_HOME,
    api: SOURCE_API,
    mode: 'discovery-metadata-only',
    packages: packages.length,
    unique_title_ids: uniqueTitleIds.size,
    explicit_pack_entries: explicitPackEntries,
    unspecified_pack_entries: packages.length - explicitPackEntries,
    pack_counts: compactCounts(packs),
    region_counts: compactCounts(regions),
    firmware_counts: compactCounts(firmware),
    apr_enabled: aprEnabled,
    latest_apr_version: String(data.latestAprVersion || '').slice(0, 40),
    apr_versions: Array.isArray(data.aprVersions) ? data.aprVersions.map(String).slice(0, 40) : [],
    dlcs_merged: dlcsMerged,
    download_links: totalLinks,
    link_provider_counts: compactCounts(linkProviders),
    posters: withPoster,
    banners: withBanner,
    latest,
  };
}

function comparable(value) {
  const clone = structuredClone(value);
  delete clone._generated;
  return JSON.stringify(clone);
}

async function main() {
  let data;
  try {
    data = await fetchSource();
  } catch (error) {
    console.warn(`[source:warn] ${SOURCE_ID}: ${error?.message || error}; preserving existing discovery metadata`);
    return;
  }

  const catalog = JSON.parse(await readFile(CATALOG_PATH, 'utf8'));
  const before = comparable(catalog);
  const summary = analyze(data);

  const sources = Array.isArray(catalog._sources) ? catalog._sources.filter((s) => s?.id !== SOURCE_ID) : [];
  sources.push({
    id: SOURCE_ID,
    url: SOURCE_API,
    homepage: SOURCE_HOME,
    mode: 'discovery',
    entries: summary.packages,
  });
  catalog._sources = sources.sort((a, b) => String(a.id || '').localeCompare(String(b.id || '')));
  catalog._discovery = { ...(catalog._discovery || {}), pfs_library: summary };
  catalog._stats = { ...(catalog._stats || {}), discovered_games: summary.packages };

  console.log(
    `[source:ok] ${SOURCE_ID}: ${summary.packages} records; ` +
    `packs=${JSON.stringify(summary.pack_counts)}; links=${summary.download_links}; ` +
    `APR=${summary.apr_enabled}; uniqueTitleIds=${summary.unique_title_ids}`
  );

  if (before === comparable(catalog)) {
    console.log(`[source:unchanged] ${SOURCE_ID}`);
    return;
  }

  catalog._generated = new Date().toISOString();
  await writeFile(CATALOG_PATH, `${JSON.stringify(catalog, null, 2)}\n`, 'utf8');
  console.log(`[source:updated] ${SOURCE_ID}: discovery metadata written to ${CATALOG_PATH}`);
}

main().catch((error) => {
  console.error(error?.stack || error);
  process.exitCode = 1;
});
