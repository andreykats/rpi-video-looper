// USB drive content — folder tree of video files.
// Each video has a `path` (folder) used to build the tree.
const VIDEO_POOL = [
  // /CARTOONS
  { id: 'v01', name: 'CARTOON_HOUR_OPENER.mov', duration: 180, kind: 'cartoon', path: '/CARTOONS' },
  { id: 'v02', name: 'BUGS_RABBIT_S03E12.mov',  duration: 1320, kind: 'cartoon', path: '/CARTOONS' },
  { id: 'v03', name: 'SATURDAY_TOONS_BLOCK.mov', duration: 2700, kind: 'cartoon', path: '/CARTOONS' },
  { id: 'v25', name: 'KIDS_PUPPET_HOUR.mov',     duration: 1500, kind: 'cartoon', path: '/CARTOONS/KIDS' },
  { id: 'v31', name: 'SATURDAY_AM_BLOCK_2.mov',  duration: 1800, kind: 'cartoon', path: '/CARTOONS/KIDS' },

  // /NEWS
  { id: 'v04', name: 'EVENING_NEWS_0600.mov',  duration: 1800, kind: 'news', path: '/NEWS' },
  { id: 'v05', name: 'WEATHER_REPORT_PM.mov',  duration: 240,  kind: 'news', path: '/NEWS' },
  { id: 'v21', name: 'SPORTS_HIGHLIGHTS.mov',  duration: 900,  kind: 'news', path: '/NEWS/SPORTS' },
  { id: 'v32', name: 'NEWS_BREAK_NOON.mov',    duration: 300,  kind: 'news', path: '/NEWS' },

  // /BUMPERS (station IDs + promos)
  { id: 'v06', name: 'NETWORK_PROMO_FALL.mov', duration: 30, kind: 'promo', path: '/BUMPERS' },
  { id: 'v07', name: 'STATION_ID_LONG.mov',    duration: 15, kind: 'promo', path: '/BUMPERS/IDS' },
  { id: 'v08', name: 'STATION_ID_SHORT.mov',   duration: 8,  kind: 'promo', path: '/BUMPERS/IDS' },
  { id: 'v29', name: 'TRAILER_BLOCKBUSTER.mov', duration: 150, kind: 'promo', path: '/BUMPERS' },

  // /COMMERCIALS
  { id: 'v09', name: 'COMMERCIAL_BREAK_A.mov', duration: 120, kind: 'ad', path: '/COMMERCIALS' },
  { id: 'v10', name: 'COMMERCIAL_BREAK_B.mov', duration: 90,  kind: 'ad', path: '/COMMERCIALS' },
  { id: 'v11', name: 'AD_DETERGENT_30S.mov',   duration: 30,  kind: 'ad', path: '/COMMERCIALS/30S' },
  { id: 'v12', name: 'AD_AUTOMOBILE_60S.mov',  duration: 60,  kind: 'ad', path: '/COMMERCIALS/60S' },
  { id: 'v20', name: 'INFOMERCIAL_KNIVES.mov', duration: 1800, kind: 'ad', path: '/COMMERCIALS/INFOMERCIAL' },

  // /SHOWS
  { id: 'v13', name: 'SITCOM_PILOT_EPISODE.mov', duration: 1500, kind: 'sitcom', path: '/SHOWS/SITCOMS' },
  { id: 'v14', name: 'GAME_SHOW_MIDDAY.mov',     duration: 1800, kind: 'show',   path: '/SHOWS/GAMESHOWS' },
  { id: 'v15', name: 'SOAP_OPERA_EP_417.mov',    duration: 2640, kind: 'show',   path: '/SHOWS/DRAMAS' },
  { id: 'v16', name: 'TALK_SHOW_LATE_NITE.mov',  duration: 3300, kind: 'show',   path: '/SHOWS/TALK' },
  { id: 'v26', name: 'COOKING_SHOW_S02.mov',     duration: 1500, kind: 'show',   path: '/SHOWS/LIFESTYLE' },
  { id: 'v27', name: 'EXERCISE_AEROBICS.mov',    duration: 1200, kind: 'show',   path: '/SHOWS/LIFESTYLE' },
  { id: 'v28', name: 'MUSIC_VIDEO_BLOCK.mov',    duration: 1800, kind: 'show',   path: '/SHOWS/MUSIC' },

  // /MOVIES
  { id: 'v17', name: 'MOVIE_OF_THE_WEEK.mov', duration: 5400, kind: 'movie', path: '/MOVIES' },
  { id: 'v18', name: 'B_MOVIE_HORROR.mov',    duration: 4800, kind: 'movie', path: '/MOVIES/HORROR' },

  // /DOCS
  { id: 'v19', name: 'NATURE_DOC_OCEANS.mov',   duration: 2700, kind: 'doc', path: '/DOCS/NATURE' },
  { id: 'v30', name: 'DOCUMENTARY_SPACE.mov',   duration: 3000, kind: 'doc', path: '/DOCS/SCIENCE' },

  // /SYSTEM
  { id: 'v22', name: 'TEST_PATTERN_BARS.mov',  duration: 60, kind: 'tech', path: '/SYSTEM' },
  { id: 'v23', name: 'COLOR_CALIBRATION.mov',  duration: 30, kind: 'tech', path: '/SYSTEM' },
  { id: 'v24', name: 'EMERGENCY_BCAST_TEST.mov', duration: 45, kind: 'tech', path: '/SYSTEM' },

  // /SYSTEM/GAMES — interactive ROMs, occupy a whole channel
  { id: 'g01', name: 'SUPER_QUEST.nes',        kind: 'game', fileType: 'nes', path: '/SYSTEM/GAMES' },
  { id: 'g02', name: 'PIXEL_RACER_3.nes',      kind: 'game', fileType: 'nes', path: '/SYSTEM/GAMES' },
  { id: 'g03', name: 'DUNGEON_CRAWLER_2.nes',  kind: 'game', fileType: 'nes', path: '/SYSTEM/GAMES' },
  { id: 'g04', name: 'SPACE_INVADERS_NX.nes',  kind: 'game', fileType: 'nes', path: '/SYSTEM/GAMES' },
];

// Channel labels — Dymo-style tape on each row
const CHANNELS = [
  { num: 2,  call: 'KCRT-2',  name: 'TOONS' },
  { num: 3,  call: 'WBRD-3',  name: 'NEWS' },
  { num: 4,  call: 'KMOV-4',  name: 'CINEMA' },
  { num: 5,  call: 'WSPT-5',  name: 'SPORTS' },
  { num: 6,  call: 'KSHO-6',  name: 'PRIMETIME' },
  { num: 7,  call: 'WLAT-7',  name: 'LATE NITE' },
  { num: 8,  call: 'KIDS-8',  name: 'CHILDREN' },
  { num: 9,  call: 'WDOC-9',  name: 'NATURE' },
  { num: 10, call: 'KMUS-10', name: 'MUSIC' },
  { num: 11, call: 'WCOM-11', name: 'AD-CHAN' },
  { num: 12, call: 'KOLD-12', name: 'CLASSICS' },
  { num: 13, call: 'WTST-13', name: 'TEST' },
];

// Default playlist seed — give each channel some content
const SEED_PLAYLISTS = {
  2:  ['v01','v02','v07','v09','v03','v06','v25'],
  3:  ['v04','v11','v05','v10','v21','v08'],
  4:  ['v17','v06','v18'],
  5:  ['v21','v11','v07','v21','v09'],
  6:  ['v13','v09','v14','v11','v15'],
  7:  ['v16','v10','v28','v06'],
  8:  ['v25','v07','v01','v03'],
  9:  ['v19','v08','v30','v06'],
  10: ['v28','v07','v29','v11','v28'],
  11: ['v09','v11','v12','v20','v10'],
  12: ['v18','v06','v17'],
  13: ['g01'],
};

// Build a folder tree from the flat pool. Each node is either:
//   { type: 'folder', name, path, children: [], files: [] }
//   { type: 'file',   ...video }
function buildPoolTree(pool) {
  const root = { type: 'folder', name: 'USB-DRIVE', path: '/', children: {}, files: [] };
  for (const v of pool) {
    const segs = (v.path || '/').split('/').filter(Boolean);
    let node = root;
    let curPath = '';
    for (const seg of segs) {
      curPath += '/' + seg;
      if (!node.children[seg]) {
        node.children[seg] = { type: 'folder', name: seg, path: curPath, children: {}, files: [] };
      }
      node = node.children[seg];
    }
    node.files.push(v);
  }
  // Convert children maps to sorted arrays recursively
  const finalize = (n) => {
    n.childList = Object.values(n.children).sort((a,b) => a.name.localeCompare(b.name));
    n.files.sort((a,b) => a.name.localeCompare(b.name));
    n.childList.forEach(finalize);
    return n;
  };
  return finalize(root);
}

window.VIDEO_POOL = VIDEO_POOL;
window.CHANNELS = CHANNELS;
window.SEED_PLAYLISTS = SEED_PLAYLISTS;
window.buildPoolTree = buildPoolTree;
