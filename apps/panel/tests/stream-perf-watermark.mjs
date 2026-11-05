/** Index outputs from an isolated real component render, never from Markdown-length guesses. */
export function buildDomVersionIndex(states) {
  const firstVersions = new Map();
  const ambiguousVersions = [];
  let lastVersion = 0;
  for (const { version, text } of states) {
    if (!Number.isInteger(version) || version <= lastVersion || typeof text !== 'string') {
      throw new TypeError('Invalid ordered DOM reference state');
    }
    lastVersion = version;
    if (firstVersions.has(text)) ambiguousVersions.push(version);
    else firstVersions.set(text, version);
  }
  return { entries: [...firstVersions], ambiguousVersions };
}

/** Receive-to-observed-DOM-version lag; this is not a painted-pixel measurement. */
export function calculateLagMetrics(receiveTimeline, domCommitTimeline) {
  const lags = [];
  let unmatchedCount = 0;
  let ambiguousCount = 0;
  for (const item of receiveTimeline) {
    if (!Number.isInteger(item.version) || item.version < 1 || !Number.isFinite(item.timestamp)) {
      throw new TypeError('Invalid receive sample');
    }
    if (item.ambiguous) { ambiguousCount++; continue; }
    const committed = domCommitTimeline.find(sample => Number.isInteger(sample.version)
      && sample.version >= item.version && Number.isFinite(sample.timestamp) && sample.timestamp >= item.timestamp);
    if (committed) lags.push(committed.timestamp - item.timestamp);
    else unmatchedCount++;
  }
  lags.sort((a, b) => a - b);
  const rounded = value => value === null ? null : Math.round(value * 100) / 100;
  const n = lags.length;
  return {
    lagsMs: lags,
    p50LagMs: rounded(n ? (n % 2 ? lags[Math.floor(n / 2)] : (lags[n / 2 - 1] + lags[n / 2]) / 2) : null),
    p95LagMs: rounded(n ? lags[Math.min(n - 1, Math.floor(n * 0.95))] : null),
    maxLagMs: rounded(n ? lags[n - 1] : null),
    matchedCount: n,
    unmatchedCount,
    ambiguousCount,
    sampledCount: receiveTimeline.length,
  };
}

export function isSafeFixtureUrl(url, baseOrigin, allowedOrigin) {
  try {
    const parsed = new URL(url, baseOrigin);
    return parsed.origin === allowedOrigin && ['/src/', '/node_modules/', '/@', '/tests/'].some(prefix => parsed.pathname.startsWith(prefix));
  } catch { return false; }
}

const SHA256_HEX_REGEX = /^[0-9a-f]{64}$/;

/**
 * Builds a mapping from DOM text SHA256 -> minimum source length (lower bound).
 * Given a cumulative source text and an array of chunk boundary source lengths,
 * maps each DOM text state to:
 * 1. earliest/minimum sourceLength that produced this exact DOM text (conservative lower bound)
 * 2. the highest chunk version whose cumulative text length <= minSourceLength.
 */
export function buildDomPrefixProgressIndex(prefixRecords, chunkBoundaries) {
  if (!Array.isArray(prefixRecords)) throw new TypeError('prefixRecords must be an array');
  if (!Array.isArray(chunkBoundaries)) throw new TypeError('chunkBoundaries must be an array');

  // Validate chunk boundaries: positive integers, monotonically non-decreasing
  let prevBoundary = 0;
  for (const b of chunkBoundaries) {
    if (!Number.isInteger(b) || b <= 0 || b < prevBoundary) {
      throw new TypeError('Chunk boundaries must be monotonically non-decreasing positive integers');
    }
    prevBoundary = b;
  }

  const hashToMinSourceLength = new Map();
  let prevSourceLen = -1;
  for (const { sourceLength, hash } of prefixRecords) {
    if (!Number.isInteger(sourceLength) || sourceLength < 0) {
      throw new TypeError('Invalid source length');
    }
    if (sourceLength < prevSourceLen) {
      throw new TypeError('Source length must be monotonically non-decreasing');
    }
    prevSourceLen = sourceLength;

    if (typeof hash !== 'string' || !SHA256_HEX_REGEX.test(hash)) {
      throw new TypeError('Invalid SHA-256 hash');
    }

    if (!hashToMinSourceLength.has(hash)) {
      hashToMinSourceLength.set(hash, sourceLength);
    } else {
      // Conservative lower bound: keep minimum source length
      const cur = hashToMinSourceLength.get(hash);
      if (sourceLength < cur) {
        hashToMinSourceLength.set(hash, sourceLength);
      }
    }
  }

  // Pre-calculate chunk version for any source length via binary search
  function getCoveredChunkVersion(sourceLen) {
    if (sourceLen <= 0 || !chunkBoundaries || chunkBoundaries.length === 0) return 0;
    // chunkBoundaries is monotonically non-decreasing
    let low = 0;
    let high = chunkBoundaries.length - 1;
    let version = 0;
    while (low <= high) {
      const mid = (low + high) >> 1;
      if (chunkBoundaries[mid] <= sourceLen) {
        version = mid + 1; // 1-indexed version
        low = mid + 1;
      } else {
        high = mid - 1;
      }
    }
    return version;
  }

  return {
    algorithmVersion: 'sha256-prefix-lowerbound-v1',
    hashToMinSourceLength,
    chunkBoundaries,
    resolveProgress(hash) {
      const minSourceLength = hashToMinSourceLength.get(hash);
      if (minSourceLength === undefined) return { matched: false, minSourceLength: null, coveredVersion: 0 };
      const coveredVersion = getCoveredChunkVersion(minSourceLength);
      return { matched: true, minSourceLength, coveredVersion };
    }
  };
}

export function serializeDomPrefixProgressIndex(index) {
  return {
    algorithmVersion: index.algorithmVersion,
    chunkBoundaries: [...index.chunkBoundaries],
    entries: Array.from(index.hashToMinSourceLength.entries())
  };
}

export function deserializeDomPrefixProgressIndex(serialized) {
  if (!serialized || serialized.algorithmVersion !== 'sha256-prefix-lowerbound-v1') {
    throw new TypeError('Invalid serialized DOM prefix progress index');
  }
  const hashToMinSourceLength = new Map(serialized.entries);
  const chunkBoundaries = serialized.chunkBoundaries;

  function getCoveredChunkVersion(sourceLen) {
    if (sourceLen <= 0 || !chunkBoundaries || chunkBoundaries.length === 0) return 0;
    let low = 0;
    let high = chunkBoundaries.length - 1;
    let version = 0;
    while (low <= high) {
      const mid = (low + high) >> 1;
      if (chunkBoundaries[mid] <= sourceLen) {
        version = mid + 1;
        low = mid + 1;
      } else {
        high = mid - 1;
      }
    }
    return version;
  }

  return {
    algorithmVersion: serialized.algorithmVersion,
    hashToMinSourceLength,
    chunkBoundaries,
    resolveProgress(hash) {
      const minSourceLength = hashToMinSourceLength.get(hash);
      if (minSourceLength === undefined) return { matched: false, minSourceLength: null, coveredVersion: 0 };
      const coveredVersion = getCoveredChunkVersion(minSourceLength);
      return { matched: true, minSourceLength, coveredVersion };
    }
  };
}
