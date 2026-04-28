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

// Flatten the server's pool tree into a list suitable for the sidebar.
// id = absolute path (stable per file).
function flattenPool(serverPool) {
  const out = [];
  const walk = (node) => {
    for (const f of (node.files || [])) {
      out.push({
        id: f.path,
        name: f.name,
        path: node.path,
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
window.flattenPool = flattenPool;
window.buildPoolTree = buildPoolTree;
window.poolItemDuration = poolItemDuration;
