// Channel list — the looper supports 1-13 but channel 1 is the
// "unmapped/dead-zone" channel (no relay), so we don't surface it.
const CHANNELS = [
  { num: 2  },
  { num: 3  },
  { num: 4  },
  { num: 5  },
  { num: 6  },
  { num: 7  },
  { num: 8  },
  { num: 9  },
  { num: 10 },
  { num: 11 },
  { num: 12 },
  { num: 13 },
];

// Per-channel phosphor palette — stripe is the bright accent (border, 4px
// stripe element, duration text), bg is the darker tint behind clip-body.
// NES clips bypass this map; they use the existing purple theme.
const CHANNEL_COLORS = {
  1:  { stripe: '#5a5e62', bg: '#1a1c20' }, // unmapped — off-air gray
  2:  { stripe: '#ff4030', bg: '#2a1010' },
  3:  { stripe: '#ff8a30', bg: '#2a1808' },
  4:  { stripe: '#ffc040', bg: '#2a200c' },
  5:  { stripe: '#b0ff40', bg: '#1f2a0c' },
  6:  { stripe: '#40ff60', bg: '#0c2a14' },
  7:  { stripe: '#40ffc0', bg: '#0c2a24' },
  8:  { stripe: '#40d0ff', bg: '#0c1f2a' },
  9:  { stripe: '#5080ff', bg: '#0c142a' },
  10: { stripe: '#8060ff', bg: '#14102a' },
  11: { stripe: '#ff60d0', bg: '#2a0c20' },
  12: { stripe: '#ff8090', bg: '#2a1014' },
  13: { stripe: '#ff7050', bg: '#2a140c' },
};

// Flatten the server's pool tree into a list suitable for the sidebar.
// `id` is the absolute path (used by API calls — rename/delete/move).
// `path` is a display path with the mount-root prefix stripped so the
// sidebar tree starts at channel folders, not /mnt/usbdriveN.
// `relPath` is the file's USB-root-relative path (no leading slash) —
// what the server stores in playlist.json.
function flattenPool(serverPool) {
  // Sort longest-first so a file under /mnt/usbdrive10 isn't matched
  // against /mnt/usbdrive1's prefix.
  const mountRoots = (serverPool.mountRoots || [])
    .slice().sort((a, b) => b.length - a.length);
  const stripMount = (absPath) => {
    for (const mr of mountRoots) {
      if (absPath === mr) return '/';
      if (absPath.startsWith(mr + '/')) return absPath.slice(mr.length);
    }
    return absPath;
  };
  const out = [];
  const walk = (node) => {
    for (const f of (node.files || [])) {
      out.push({
        id: f.path,
        name: f.name,
        path: stripMount(node.path),
        relPath: stripMount(f.path).replace(/^\//, ''),
        absParent: node.path,
        fileType: f.fileType,
        sizeBytes: f.sizeBytes,
      });
    }
    for (const c of (node.children || [])) walk(c);
  };
  for (const root of (serverPool.children || [])) walk(root);
  return out;
}

// Build a folder tree-of-arrays from the server's pool tree. The shape
// matches what VideoPoolSidebar expects: each node has `childList`
// (sorted children), `files` (sorted files), and a `path` string.
function buildPoolTree(serverPool) {
  const finalize = (n) => {
    const node = {
      type: 'folder',
      name: n.name,
      path: n.path,
      childList: (n.children || []).map(finalize),
      files: [...(n.files || [])].sort((a, b) => a.name.localeCompare(b.name)),
    };
    node.childList.sort((a, b) => a.name.localeCompare(b.name));
    return node;
  };
  // Server returns {mountRoots, children: [<root nodes>]}. Wrap the roots
  // under a single virtual USB-DRIVE node when there's exactly one mount,
  // or expose them as siblings under a synthetic root.
  const roots = (serverPool.children || []).map(finalize);
  if (roots.length === 1) return roots[0];
  return {
    type: 'folder',
    name: 'USB-DRIVE',
    path: '/',
    childList: roots,
    files: [],
  };
}

// Map a flat-pool item back to a duration when the server hasn't given us
// one (size-only listing). Used for clip-block width fallbacks.
function poolItemDuration(item) {
  return typeof item.durationSec === 'number' ? item.durationSec : 0;
}

window.CHANNELS = CHANNELS;
window.CHANNEL_COLORS = CHANNEL_COLORS;
window.flattenPool = flattenPool;
window.buildPoolTree = buildPoolTree;
window.poolItemDuration = poolItemDuration;
