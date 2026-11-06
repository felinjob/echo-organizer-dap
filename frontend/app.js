// Application State
let filesState = []; // Array of { source, albumGroupId, original: {}, proposed: {}, already_organized: bool }
let selectedSources = new Set();
let collapsedAlbumGroups = new Set();
let destinationCollisionMap = new Map(); // Map<lowerRelPath, number>
let collidingTrackIndices = new Set();
let workspaceFilterQuery = '';

let isScanning = false;
let isFetchingBulk = false;
let manualSearchIndex = null;

// Active Background Job & SSE State
let activeJobId = null;
let activeEventSource = null;
let isJobPaused = false;

// Web Explorer Modal State
let currentExplorerTarget = null; // 'source' | 'dest'
let currentExplorerPath = '';
let currentExplorerParent = null;

// Reserved DOS / Windows / FAT32 names
const RESERVED_FAT_NAMES = new Set([
    'CON', 'PRN', 'AUX', 'NUL',
    'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
    'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'
]);

// DOM Elements
const sysClockEl = document.getElementById('sys-clock');
const vfdModeLabel = document.getElementById('vfd-mode-label');

const sourceDirInput = document.getElementById('source-dir');
const destDirInput = document.getElementById('dest-dir');
const browseSourceBtn = document.getElementById('browse-source-btn');
const browseDestBtn = document.getElementById('browse-dest-btn');
const scanBtn = document.getElementById('scan-btn');
const emptyState = document.getElementById('empty-state');
const statsPanel = document.getElementById('stats-panel');
const workspacePanel = document.getElementById('workspace-panel');
const albumsContainer = document.getElementById('albums-container');
const workspaceFilterInput = document.getElementById('workspace-filter-input');

const totalCountEl = document.getElementById('total-count');
const albumsCountEl = document.getElementById('albums-count');
const mbMatchesCountEl = document.getElementById('mb-matches-count');
const coverCountEl = document.getElementById('cover-count');
const selectedCountLabel = document.getElementById('selected-count-label');
const collisionCountBadge = document.getElementById('collision-count-badge');
const collisionBanner = document.getElementById('collision-banner');
const collisionBannerDetail = document.getElementById('collision-banner-detail');
const allowOverwriteCheckbox = document.getElementById('allow-overwrite-checkbox');

const expandAllBtn = document.getElementById('expand-all-btn');
const collapseAllBtn = document.getElementById('collapse-all-btn');
const selectAllBtn = document.getElementById('select-all-btn');
const deselectAllBtn = document.getElementById('deselect-all-btn');
const headerSelectAll = document.getElementById('header-select-all');
const mbBulkBtn = document.getElementById('mb-bulk-btn');
const processBtn = document.getElementById('process-btn');
const processBtnLabel = document.getElementById('process-btn-label');
const clearQueueBtn = document.getElementById('clear-queue-btn');

// Web Directory Explorer Elements
const fsExplorerModal = document.getElementById('fs-explorer-modal');
const fsModalTitle = document.getElementById('fs-modal-title');
const fsCloseBtn = document.getElementById('fs-close-btn');
const fsUpBtn = document.getElementById('fs-up-btn');
const fsCurrentPathInput = document.getElementById('fs-current-path-input');
const fsGoBtn = document.getElementById('fs-go-btn');
const fsRootsList = document.getElementById('fs-roots-list');
const fsDirsList = document.getElementById('fs-dirs-list');
const fsDirMeta = document.getElementById('fs-dir-meta');
const fsSpaceIndicator = document.getElementById('fs-space-indicator');
const fsCancelBtn = document.getElementById('fs-cancel-btn');
const fsSelectBtn = document.getElementById('fs-select-btn');

// Processing Modal Elements (SSE)
const processModal = document.getElementById('process-modal');
const processStageBadge = document.getElementById('process-stage-badge');
const progressCurrentAlbum = document.getElementById('progress-current-album');
const progressCurrentFile = document.getElementById('progress-current-file');
const progressFill = document.getElementById('progress-fill');
const progressPercent = document.getElementById('progress-percent');
const progressFraction = document.getElementById('progress-fraction');
const logPanel = document.getElementById('log-panel');
const jobControlBtns = document.getElementById('job-control-btns');
const jobPauseBtn = document.getElementById('job-pause-btn');
const jobCancelBtn = document.getElementById('job-cancel-btn');
const modalSuccessActions = document.getElementById('modal-success-actions');
const modalCloseBtn = document.getElementById('modal-close-btn');

// Manual Search Modal Elements
const searchModal = document.getElementById('search-modal');
const searchArtistInput = document.getElementById('search-artist');
const searchTitleInput = document.getElementById('search-title');
const searchCancelBtn = document.getElementById('search-cancel-btn');
const searchSubmitBtn = document.getElementById('search-submit-btn');

function setVfdMode(text) {
    if (vfdModeLabel) {
        vfdModeLabel.textContent = text;
    }
}

function updateSysClock() {
    if (!sysClockEl) return;
    const now = new Date();
    const hh = String(now.getHours()).padStart(2, '0');
    const mm = String(now.getMinutes()).padStart(2, '0');
    const ss = String(now.getSeconds()).padStart(2, '0');
    sysClockEl.textContent = `${hh}:${mm}:${ss}`;
}

// =========================================================================
// 1. Safe HTML Escaping & Client-Side Sprint 1 Path Normalizer
// =========================================================================

function escapeHtml(val) {
    if (val === null || val === undefined) return '';
    return String(val)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function normalizeUnicodeNFC(text) {
    if (text === null || text === undefined) return '';
    return String(text)
        .normalize('NFC')
        .replace(/[\x00-\x1f\x7f]/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();
}

function formatDuration(seconds) {
    const sec = parseInt(seconds, 10) || 0;
    if (sec <= 0) return '00:00';
    const mins = Math.floor(sec / 60);
    const rem = sec % 60;
    return `${String(mins).padStart(2, '0')}:${String(rem).padStart(2, '0')}`;
}

function stripLeadingTrackNumber(title, knownTrack = null) {
    let cleaned = normalizeUnicodeNFC(title);
    if (!cleaned) return '';

    let knownTrackInt = null;
    if (knownTrack !== null && knownTrack !== undefined) {
        const m = String(knownTrack).trim().match(/^(\d+)/);
        if (m) knownTrackInt = parseInt(m[1], 10);
    }

    const patterns = [
        /^(?:[\[\(]?\d{1,2}[-.]\d{1,3}[\]\)]?)\s*(?:[-–—._:]+\s*|\s+)(.+)$/,
        /^[\[\(]\s*(?:track\s*|faixa\s*|#)?\d{1,3}\s*[\]\)]\s*[-–—._:]*\s*(.+)$/i,
        /^(?:track|faixa|#)\s*\d{1,3}\s*(?:[-–—._:]+\s*|\s+)(.+)$/i,
        /^\d{1,3}\s*[-–—._:]+\s*(.+)$/,
        /^0+\d{1,2}\s+(.+)$/
    ];

    let changed = true;
    while (changed && cleaned) {
        changed = false;
        for (const pat of patterns) {
            const m = cleaned.match(pat);
            if (m && m[1]) {
                const candidate = normalizeUnicodeNFC(m[1]);
                if (candidate) {
                    cleaned = candidate;
                    changed = true;
                    break;
                }
            }
        }
        if (!changed && knownTrackInt !== null) {
            const mKnown = cleaned.match(/^(\d{1,3})\s+(.+)$/);
            if (mKnown) {
                const prefixNum = parseInt(mKnown[1], 10);
                const candidate = normalizeUnicodeNFC(mKnown[2]);
                if (prefixNum === knownTrackInt && candidate) {
                    cleaned = candidate;
                    changed = true;
                }
            }
        }
    }
    return cleaned;
}

function sanitizeFat32Component(name, defaultVal = 'Unknown') {
    const norm = normalizeUnicodeNFC(name);
    if (!norm) return defaultVal;

    let sanitized = norm
        .replace(/[<>:"/\\|?*\x00-\x1f\x7f]/g, '')
        .replace(/\s+/g, ' ')
        .replace(/^[.\s]+|[.\s]+$/g, '');

    if (!sanitized) return defaultVal;

    const stem = sanitized.split('.')[0].trim().toUpperCase();
    if (RESERVED_FAT_NAMES.has(stem)) {
        sanitized = `_${sanitized}`;
    }
    return sanitized || defaultVal;
}

function truncateClean(text, maxChars) {
    if (maxChars <= 0) return '';
    if (text.length <= maxChars) return text;
    const truncated = text.slice(0, maxChars).replace(/[.\s\-_–—]+$/g, '');
    return truncated || text.slice(0, maxChars).replace(/[.\s]+$/g, '');
}

function buildSafeRelativePathClient(tags, ext = '.mp3', maxLength = 200) {
    let extClean = normalizeUnicodeNFC(ext || '.mp3').toLowerCase();
    if (!extClean.startsWith('.')) extClean = `.${extClean}`;

    let albumArtist = normalizeUnicodeNFC(tags.album_artist || tags.artist || 'Artista Desconhecido');
    const vaSet = new Set(['various artists', 'various', 'varios artistas', 'varios', 'vários artistas', 'va', 'v.a.']);
    if (vaSet.has(albumArtist.toLowerCase())) {
        albumArtist = 'Various Artists';
    }

    let artistFolder = sanitizeFat32Component(albumArtist, 'Artista Desconhecido');
    let albumFolder = sanitizeFat32Component(tags.album || 'Álbum Desconhecido', 'Álbum Desconhecido');

    const trackRaw = String(tags.track || '01').trim();
    const mTrack = trackRaw.match(/^(\d+)/);
    const trackNum = mTrack ? String(parseInt(mTrack[1], 10)).padStart(2, '0') : sanitizeFat32Component(trackRaw, '01');

    const discRaw = String(tags.disc || '1').trim();
    const mDisc = discRaw.match(/^(\d+)/);
    const discInt = mDisc ? parseInt(mDisc[1], 10) : 1;
    const trackPrefix = discInt > 1 ? `${discInt}-${trackNum}` : trackNum;

    const rawTitle = stripLeadingTrackNumber(tags.title || 'Faixa', trackNum);
    const titleClean = sanitizeFat32Component(rawTitle, 'Faixa');

    const trackArtist = normalizeUnicodeNFC(tags.artist || '');
    let isDiffArtist = false;
    if (trackArtist && albumArtist) {
        const tArt = trackArtist.toLowerCase();
        const aArt = albumArtist.toLowerCase();
        if (tArt !== aArt && aArt !== 'artista desconhecido' && aArt !== 'unknown') {
            isDiffArtist = true;
        }
    }

    let trackArtistClean = isDiffArtist ? sanitizeFat32Component(trackArtist, '') : '';

    const assemble = (artF, albF, trkArt, ttl) => {
        const fname = (isDiffArtist && trkArt)
            ? `${trackPrefix} - ${trkArt} - ${ttl}${extClean}`
            : `${trackPrefix} - ${ttl}${extClean}`;
        return `${artF}/${albF}/${fname}`;
    };

    const fullRel = assemble(artistFolder, albumFolder, trackArtistClean, titleClean);
    if (fullRel.length <= maxLength) {
        return fullRel;
    }

    let prefixBeforeTitle = (isDiffArtist && trackArtistClean)
        ? `${trackPrefix} - ${trackArtistClean} - `
        : `${trackPrefix} - `;

    let fixedOverhead = artistFolder.length + 1 + albumFolder.length + 1 + prefixBeforeTitle.length + extClean.length;
    let availableForTitle = maxLength - fixedOverhead;

    if (availableForTitle < 15) {
        artistFolder = sanitizeFat32Component(truncateClean(artistFolder, 50), 'Artista');
        albumFolder = sanitizeFat32Component(truncateClean(albumFolder, 50), 'Album');
        if (isDiffArtist && trackArtistClean) {
            trackArtistClean = sanitizeFat32Component(truncateClean(trackArtistClean, 35), 'Artista');
            prefixBeforeTitle = `${trackPrefix} - ${trackArtistClean} - `;
        }
        fixedOverhead = artistFolder.length + 1 + albumFolder.length + 1 + prefixBeforeTitle.length + extClean.length;
        availableForTitle = Math.max(1, maxLength - fixedOverhead);
    }

    let titleTruncated = truncateClean(titleClean, availableForTitle) || 'Faixa'.slice(0, availableForTitle);
    titleTruncated = sanitizeFat32Component(titleTruncated, 'Faixa');
    if (titleTruncated.length > availableForTitle) {
        titleTruncated = titleTruncated.slice(0, availableForTitle).replace(/[.\s]+$/g, '');
    }

    return assemble(artistFolder, albumFolder, trackArtistClean, titleTruncated);
}

// =========================================================================
// 2. Initialization & Event Listeners
// =========================================================================

document.addEventListener('DOMContentLoaded', () => {
    updateSysClock();
    setInterval(updateSysClock, 1000);

    const savedSource = localStorage.getItem('music_source_dir');
    const savedDest = localStorage.getItem('music_dest_dir');
    if (savedSource) sourceDirInput.value = savedSource;
    if (savedDest) destDirInput.value = savedDest;
});

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        if (!fsExplorerModal.classList.contains('hidden')) {
            closeWebFolderExplorer();
        } else if (!searchModal.classList.contains('hidden')) {
            searchModal.classList.add('hidden');
            manualSearchIndex = null;
        }
    }
});

scanBtn.addEventListener('click', scanDirectory);
browseSourceBtn.addEventListener('click', () => openWebFolderExplorer('source'));
browseDestBtn.addEventListener('click', () => openWebFolderExplorer('dest'));

if (workspaceFilterInput) {
    workspaceFilterInput.addEventListener('input', (e) => {
        workspaceFilterQuery = (e.target.value || '').trim().toLowerCase();
        applyWorkspaceFilter();
    });
}

expandAllBtn.addEventListener('click', () => {
    collapsedAlbumGroups.clear();
    renderAlbumsWorkspace();
});
collapseAllBtn.addEventListener('click', () => {
    filesState.forEach(f => collapsedAlbumGroups.add(f.albumGroupId));
    renderAlbumsWorkspace();
});
selectAllBtn.addEventListener('click', () => toggleAllSelection(true));
deselectAllBtn.addEventListener('click', () => toggleAllSelection(false));
if (headerSelectAll) {
    headerSelectAll.addEventListener('change', (e) => toggleAllSelection(e.target.checked));
}
if (allowOverwriteCheckbox) {
    allowOverwriteCheckbox.addEventListener('change', () => {
        recalculateCollisionsAndPreviews();
    });
}

mbBulkBtn.addEventListener('click', () => fetchMusicBrainzReleaseBatch(null));
processBtn.addEventListener('click', processSelectedFilesSSE);
clearQueueBtn.addEventListener('click', () => {
    filesState = [];
    selectedSources.clear();
    collapsedAlbumGroups.clear();
    destinationCollisionMap.clear();
    collidingTrackIndices.clear();
    workspaceFilterQuery = '';
    if (workspaceFilterInput) workspaceFilterInput.value = '';
    setVfdMode('IDLE // READY');
    updateStats();
    renderAlbumsWorkspace();
    emptyState.classList.remove('hidden');
    statsPanel.classList.add('hidden');
    workspacePanel.classList.add('hidden');
});

modalCloseBtn.addEventListener('click', () => {
    closeActiveSSE();
    processModal.classList.add('hidden');
    scanDirectory();
});

jobPauseBtn.addEventListener('click', togglePauseActiveJob);
jobCancelBtn.addEventListener('click', cancelActiveJob);

searchCancelBtn.addEventListener('click', () => {
    searchModal.classList.add('hidden');
    manualSearchIndex = null;
});
searchSubmitBtn.addEventListener('click', executeManualSearch);

// Explorer Modal Listeners
fsCloseBtn.addEventListener('click', closeWebFolderExplorer);
fsCancelBtn.addEventListener('click', closeWebFolderExplorer);
fsUpBtn.addEventListener('click', () => {
    if (currentExplorerParent) {
        navigateExplorerTo(currentExplorerParent);
    }
});
fsGoBtn.addEventListener('click', () => {
    const val = fsCurrentPathInput.value.trim();
    if (val) navigateExplorerTo(val);
});
fsCurrentPathInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
        const val = fsCurrentPathInput.value.trim();
        if (val) navigateExplorerTo(val);
    }
});
fsSelectBtn.addEventListener('click', confirmWebFolderSelection);

function setBtnLoading(btn, isLoading) {
    if (!btn) return;
    const spinner = btn.querySelector('.spinner');
    if (isLoading) {
        btn.disabled = true;
        if (spinner) spinner.classList.remove('hidden');
    } else {
        btn.disabled = false;
        if (spinner) spinner.classList.add('hidden');
    }
}

function applyWorkspaceFilter() {
    const q = workspaceFilterQuery;
    document.querySelectorAll('.album-card').forEach(card => {
        if (!q) {
            card.classList.remove('hidden');
            card.querySelectorAll('.track-row').forEach(r => r.classList.remove('hidden'));
            return;
        }
        const headerTxt = (card.querySelector('.album-header-title')?.textContent || '').toLowerCase();
        const headerMatch = headerTxt.includes(q);
        let anyRowMatch = false;
        card.querySelectorAll('.track-row').forEach(row => {
            const idx = parseInt(row.dataset.index, 10);
            const item = filesState[idx];
            if (!item) return;
            const hay = `${item.original.filename || ''} ${item.proposed.title || ''} ${item.proposed.artist || ''} ${item.proposed.album || ''} ${item.proposed.relative_path || ''}`.toLowerCase();
            const match = headerMatch || hay.includes(q);
            row.classList.toggle('hidden', !match);
            if (match) anyRowMatch = true;
        });
        card.classList.toggle('hidden', !headerMatch && !anyRowMatch);
    });
}

// =========================================================================
// 3. Web Directory Explorer Modal (/api/fs/roots & /api/fs/ls)
// =========================================================================

async function openWebFolderExplorer(target) {
    currentExplorerTarget = target;
    fsModalTitle.textContent = target === 'source'
        ? 'SELECIONAR BARRAMENTO DE ORIGEM [IN]'
        : 'SELECIONAR BARRAMENTO DE DESTINO [OUT // FAT32]';
    fsExplorerModal.classList.remove('hidden');

    const initialPath = (target === 'source' ? sourceDirInput.value : destDirInput.value).trim();

    try {
        const res = await fetch('/api/fs/roots');
        const data = await res.json();
        const roots = data.roots || [];
        renderExplorerRoots(roots);

        if (initialPath) {
            await navigateExplorerTo(initialPath, roots[0]?.path);
        } else if (roots.length > 0) {
            await navigateExplorerTo(roots[0].path);
        }
    } catch (e) {
        console.error('Error loading filesystem roots:', e);
    }
}

function closeWebFolderExplorer() {
    fsExplorerModal.classList.add('hidden');
    currentExplorerTarget = null;
}

function renderExplorerRoots(roots) {
    fsRootsList.innerHTML = '';
    roots.forEach(root => {
        const item = document.createElement('div');
        item.className = 'fs-item';
        item.dataset.path = root.path;

        const nameSpan = document.createElement('span');
        nameSpan.style.fontWeight = '600';
        nameSpan.textContent = root.name;

        const freeSpan = document.createElement('span');
        freeSpan.style.fontSize = '10px';
        freeSpan.style.color = 'var(--accent-cyan)';
        freeSpan.textContent = root.free_gb > 0 ? `${root.free_gb} GB` : '';

        item.appendChild(nameSpan);
        item.appendChild(freeSpan);

        item.addEventListener('click', () => navigateExplorerTo(root.path));
        fsRootsList.appendChild(item);
    });
}

async function navigateExplorerTo(path, fallbackPath = null) {
    try {
        const response = await fetch(`/api/fs/ls?path=${encodeURIComponent(path)}`);
        if (!response.ok) {
            if (fallbackPath && fallbackPath !== path) {
                return navigateExplorerTo(fallbackPath, null);
            }
            const err = await response.json();
            alert(err.detail || 'Não foi possível abrir este diretório.');
            return;
        }
        const data = await response.json();
        currentExplorerPath = data.path;
        currentExplorerParent = data.parent;
        fsCurrentPathInput.value = data.path;
        fsUpBtn.disabled = !data.parent;

        fsDirMeta.textContent = data.audio_files_count > 0
            ? `[AUDIO_FILES: ${data.audio_files_count}]`
            : `[DIRS: ${data.total_directories}]`;

        if (data.total_gb > 0) {
            fsSpaceIndicator.textContent = `VOLUME CAPACITY // FREE: ${data.free_gb} GB / TOTAL: ${data.total_gb} GB`;
        } else {
            fsSpaceIndicator.textContent = '';
        }

        document.querySelectorAll('#fs-roots-list .fs-item').forEach(el => {
            el.classList.toggle('active', el.dataset.path === data.path);
        });

        fsDirsList.innerHTML = '';
        if (!data.directories || data.directories.length === 0) {
            const emptyMsg = document.createElement('div');
            emptyMsg.style.padding = '20px';
            emptyMsg.style.color = 'var(--text-muted)';
            emptyMsg.style.fontSize = '11.5px';
            emptyMsg.style.textAlign = 'center';
            emptyMsg.textContent = '// NENHUM SUBDIRETÓRIO NESTE CAMINHO //';
            fsDirsList.appendChild(emptyMsg);
            return;
        }

        data.directories.forEach(dir => {
            const row = document.createElement('div');
            row.className = 'fs-item';

            const left = document.createElement('span');
            left.textContent = `[DIR] ${dir.name}`;

            row.appendChild(left);
            row.addEventListener('click', () => navigateExplorerTo(dir.path));
            fsDirsList.appendChild(row);
        });
    } catch (e) {
        console.error('Error navigating directory:', e);
    }
}

function confirmWebFolderSelection() {
    const chosen = fsCurrentPathInput.value.trim() || currentExplorerPath;
    if (!chosen) return;

    const target = currentExplorerTarget;
    closeWebFolderExplorer();

    if (target === 'source') {
        sourceDirInput.value = chosen;
        localStorage.setItem('music_source_dir', chosen);
        scanDirectory();
    } else if (target === 'dest') {
        destDirInput.value = chosen;
        localStorage.setItem('music_dest_dir', chosen);
        if (filesState.length > 0) {
            scanDirectory();
        }
    }
}

// =========================================================================
// 4. Directory Scan & Album Grouping
// =========================================================================

function computeInitialAlbumGroupId(fileItem) {
    const src = fileItem.source || '';
    const lastSep = Math.max(src.lastIndexOf('/'), src.lastIndexOf('\\'));
    const parentDir = lastSep > 0 ? src.slice(0, lastSep).toLowerCase() : 'root';
    const prop = fileItem.proposed || {};
    const alb = (prop.album || '').trim().toLowerCase();
    const albArt = (prop.album_artist || prop.artist || '').trim().toLowerCase();

    if (alb && alb !== 'álbum desconhecido' && alb !== 'unknown album') {
        return `alb:${albArt}::${alb}`;
    }
    return `dir:${parentDir}`;
}

async function scanDirectory() {
    const sourceDir = sourceDirInput.value.trim();
    if (!sourceDir) {
        alert('Por favor, informe ou selecione a pasta de origem.');
        return;
    }

    const destDir = destDirInput.value.trim();
    localStorage.setItem('music_source_dir', sourceDir);
    if (destDir) localStorage.setItem('music_dest_dir', destDir);

    isScanning = true;
    setBtnLoading(scanBtn, true);
    setVfdMode('SCANNING // BUS');

    try {
        const payload = { source_dir: sourceDir };
        if (destDir) payload.dest_dir = destDir;

        const response = await fetch('/api/scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || 'Erro ao varrer diretório.');
        }

        const data = await response.json();
        const scannedFiles = data.files || [];

        filesState = scannedFiles.map((f) => ({
            ...f,
            albumGroupId: computeInitialAlbumGroupId(f)
        }));

        selectedSources.clear();
        collapsedAlbumGroups.clear();

        filesState.forEach(f => {
            if (!f.already_organized) {
                selectedSources.add(f.source);
            }
        });

        if (filesState.length > 0) {
            emptyState.classList.add('hidden');
            statsPanel.classList.remove('hidden');
            workspacePanel.classList.remove('hidden');
            setVfdMode(`READY // ${filesState.length} TRKS`);
        } else {
            emptyState.classList.remove('hidden');
            statsPanel.classList.add('hidden');
            workspacePanel.classList.add('hidden');
            setVfdMode('IDLE // EMPTY');
            alert('Nenhum arquivo de áudio compatível (.mp3, .flac, .m4a) encontrado neste diretório.');
        }

        renderAlbumsWorkspace();
        recalculateCollisionsAndPreviews();
    } catch (error) {
        setVfdMode('ERR // SCAN');
        alert('Erro: ' + error.message);
    } finally {
        isScanning = false;
        setBtnLoading(scanBtn, false);
    }
}

// =========================================================================
// 5. Reactive Live Preview & Destination Collision Detector (Map<path, count>)
// =========================================================================

function recalculateCollisionsAndPreviews() {
    destinationCollisionMap.clear();
    collidingTrackIndices.clear();

    const allowOverwrite = allowOverwriteCheckbox ? allowOverwriteCheckbox.checked : true;
    const pathToIndices = new Map();

    filesState.forEach((item, index) => {
        const ext = (item.original && item.original.extension) ? item.original.extension : '.mp3';
        const relPath = buildSafeRelativePathClient(item.proposed, ext, 200);
        item.proposed.relative_path = relPath;

        if (selectedSources.has(item.source)) {
            const key = relPath.toLowerCase();
            const currentCount = destinationCollisionMap.get(key) || 0;
            destinationCollisionMap.set(key, currentCount + 1);

            if (!pathToIndices.has(key)) {
                pathToIndices.set(key, []);
            }
            pathToIndices.get(key).push(index);

            if (item.already_organized && !allowOverwrite) {
                collidingTrackIndices.add(index);
            }
        }
    });

    for (const [, indices] of pathToIndices.entries()) {
        if (indices.length >= 2) {
            indices.forEach(idx => collidingTrackIndices.add(idx));
        }
    }

    const collidingAlbumGroups = new Set();

    filesState.forEach((item, index) => {
        const relPath = item.proposed.relative_path || '';
        const isCollision = collidingTrackIndices.has(index);
        if (isCollision) {
            collidingAlbumGroups.add(item.albumGroupId);
        }

        const rowEl = document.querySelector(`.track-row[data-index="${index}"]`);
        if (rowEl) {
            rowEl.classList.toggle('collision-row', isCollision);
        }

        const previewBox = document.getElementById(`preview-box-${index}`);
        if (previewBox) {
            previewBox.classList.toggle('collision', isCollision);
        }

        const previewText = document.getElementById(`preview-path-${index}`);
        if (previewText) {
            previewText.textContent = `> ${relPath}`;
        }

        const previewLen = document.getElementById(`preview-len-${index}`);
        if (previewLen) {
            previewLen.textContent = `[${String(relPath.length).padStart(3, '0')}/200B]`;
        }

        updateRowBadges(index);
    });

    document.querySelectorAll('.album-card').forEach(card => {
        const gid = card.dataset.groupId;
        card.classList.toggle('has-collision', collidingAlbumGroups.has(gid));
    });

    const collisionCount = collidingTrackIndices.size;
    if (collisionCount > 0) {
        collisionBanner.classList.remove('hidden');
        collisionCountBadge.classList.remove('hidden');
        collisionCountBadge.textContent = `${collisionCount} COLISÃO(ÕES)`;
        collisionBannerDetail.textContent = `${collisionCount} faixa(s) selecionada(s) geram endereços físicos FAT32 idênticos ou colidem com arquivos protegidos no destino. Ajuste o número/título da faixa ou desmarque a duplicata.`;
        processBtn.disabled = true;
        processBtnLabel.textContent = 'CORRIJA AS COLISÕES PARA GRAVAR';
    } else {
        collisionBanner.classList.add('hidden');
        collisionCountBadge.classList.add('hidden');
        processBtn.disabled = selectedSources.size === 0;
        processBtnLabel.textContent = 'GRAVAR & ORGANIZAR SEQUENCIALMENTE [FAT32]';
    }

    updateStats();
}

function updateStats() {
    totalCountEl.textContent = String(filesState.length);

    const uniqueGroups = new Set(filesState.map(f => f.albumGroupId));
    if (albumsCountEl) {
        albumsCountEl.textContent = String(uniqueGroups.size);
    }

    const mbCount = filesState.filter(f => f.proposed.source === 'MusicBrainz').length;
    mbMatchesCountEl.textContent = String(mbCount);

    const coverCount = filesState.filter(f => f.proposed.has_cover).length;
    coverCountEl.textContent = String(coverCount);

    selectedCountLabel.textContent = `${selectedSources.size} DE ${filesState.length} FAIXAS ARMADAS PARA GRAVAÇÃO`;
}

// =========================================================================
// 6. Safe DOM Rendering of Album-Grouped Cards & Bulk Editing
// =========================================================================

function getGroupedAlbums() {
    const groupsMap = new Map();
    filesState.forEach((item, index) => {
        const gid = item.albumGroupId || 'default';
        if (!groupsMap.has(gid)) {
            groupsMap.set(gid, []);
        }
        groupsMap.get(gid).push({ item, index });
    });
    return groupsMap;
}

function renderAlbumsWorkspace() {
    albumsContainer.innerHTML = '';
    const groupsMap = getGroupedAlbums();

    let unitNum = 0;
    for (const [groupId, entries] of groupsMap.entries()) {
        if (entries.length === 0) continue;
        unitNum += 1;

        const firstProp = entries[0].item.proposed;
        const isCollapsed = collapsedAlbumGroups.has(groupId);
        const allGroupSelected = entries.every(e => selectedSources.has(e.item.source));
        const isVariousArtists = (firstProp.album_artist || '').trim().toLowerCase() === 'various artists';

        const card = document.createElement('div');
        card.className = 'album-card';
        card.dataset.groupId = groupId;

        // --- Album Header ---
        const header = document.createElement('div');
        header.className = 'album-card-header';

        const headerTop = document.createElement('div');
        headerTop.className = 'album-header-top';

        const titleArea = document.createElement('div');
        titleArea.className = 'album-title-area';

        const groupCheckbox = document.createElement('input');
        groupCheckbox.type = 'checkbox';
        groupCheckbox.checked = allGroupSelected;
        groupCheckbox.title = 'Selecionar/Desmarcar todas as faixas deste álbum';
        groupCheckbox.addEventListener('change', (e) => {
            entries.forEach(({ item }) => {
                if (e.target.checked) {
                    selectedSources.add(item.source);
                } else {
                    selectedSources.delete(item.source);
                }
            });
            renderAlbumsWorkspace();
            recalculateCollisionsAndPreviews();
        });

        const collapseBtn = document.createElement('button');
        collapseBtn.className = 'album-collapse-btn';
        collapseBtn.type = 'button';
        collapseBtn.textContent = isCollapsed ? '+' : '−';
        collapseBtn.title = isCollapsed ? 'Expandir faixas' : 'Recolher faixas';
        collapseBtn.addEventListener('click', () => {
            if (collapsedAlbumGroups.has(groupId)) {
                collapsedAlbumGroups.delete(groupId);
            } else {
                collapsedAlbumGroups.add(groupId);
            }
            renderAlbumsWorkspace();
            recalculateCollisionsAndPreviews();
        });

        const headerTitle = document.createElement('span');
        headerTitle.className = 'album-header-title';
        headerTitle.id = `album-title-display-${groupId}`;
        headerTitle.textContent = `[UNIT_${String(unitNum).padStart(2, '0')}] ${firstProp.album_artist || 'Artista Desconhecido'} // ${firstProp.album || 'Álbum Desconhecido'}`;

        const countBadge = document.createElement('span');
        countBadge.className = 'album-track-count';
        countBadge.textContent = `${entries.length} TRK`;

        titleArea.appendChild(groupCheckbox);
        titleArea.appendChild(collapseBtn);
        titleArea.appendChild(headerTitle);
        titleArea.appendChild(countBadge);

        // Header Right Actions (Various Artists toggle + 1-Click Release Search for this Album)
        const headerActions = document.createElement('div');
        headerActions.className = 'album-header-actions';

        const vaLabel = document.createElement('label');
        vaLabel.className = 'va-checkbox-label';
        const vaCheck = document.createElement('input');
        vaCheck.type = 'checkbox';
        vaCheck.checked = isVariousArtists;
        const vaSpan = document.createElement('span');
        vaSpan.textContent = 'V.A. COMPILATION';
        vaLabel.appendChild(vaCheck);
        vaLabel.appendChild(vaSpan);

        const albumMbBtn = document.createElement('button');
        albumMbBtn.className = 'btn secondary-btn';
        albumMbBtn.type = 'button';
        albumMbBtn.style.padding = '5px 12px';
        albumMbBtn.style.fontSize = '11px';
        albumMbBtn.textContent = '[ SYNC RELEASE MBZ ]';
        albumMbBtn.addEventListener('click', async () => {
            albumMbBtn.disabled = true;
            albumMbBtn.textContent = '[ SYNCING... ]';
            await fetchMusicBrainzReleaseBatch(entries.map(e => e.index));
            albumMbBtn.disabled = false;
            albumMbBtn.textContent = '[ SYNC RELEASE MBZ ]';
        });

        headerActions.appendChild(vaLabel);
        headerActions.appendChild(albumMbBtn);

        headerTop.appendChild(titleArea);
        headerTop.appendChild(headerActions);

        // --- Master Bulk Edit Fields ---
        const masterGrid = document.createElement('div');
        masterGrid.className = 'album-master-grid';

        const unitPrefix = `[UNIT_${String(unitNum).padStart(2, '0')}]`;

        const createMasterField = (labelText, initialValue, placeholder, onInputCallback) => {
            const wrapper = document.createElement('div');
            wrapper.className = 'master-field';
            const lbl = document.createElement('label');
            lbl.textContent = labelText;
            const inp = document.createElement('input');
            inp.type = 'text';
            inp.placeholder = placeholder;
            inp.spellcheck = false;
            // Safe DOM property assignment (never breaks on quotes!)
            inp.value = initialValue || '';
            inp.addEventListener('input', (e) => onInputCallback(e.target.value, inp));
            wrapper.appendChild(lbl);
            wrapper.appendChild(inp);
            return { wrapper, inp };
        };

        const masterAlbumArtist = createMasterField(
            'MASTER // ALBUM ARTIST',
            firstProp.album_artist,
            'Ex: Daft Punk',
            (val) => {
                entries.forEach(({ item }) => {
                    item.proposed.album_artist = val;
                });
                vaCheck.checked = val.trim().toLowerCase() === 'various artists';
                headerTitle.textContent = `${unitPrefix} ${val || 'Artista Desconhecido'} // ${masterAlbum.inp.value || 'Álbum Desconhecido'}`;
                recalculateCollisionsAndPreviews();
            }
        );

        const masterAlbum = createMasterField(
            'MASTER // ALBUM TITLE',
            firstProp.album,
            'Ex: Discovery',
            (val) => {
                entries.forEach(({ item }) => {
                    item.proposed.album = val;
                });
                headerTitle.textContent = `${unitPrefix} ${masterAlbumArtist.inp.value || 'Artista Desconhecido'} // ${val || 'Álbum Desconhecido'}`;
                recalculateCollisionsAndPreviews();
            }
        );

        const masterYear = createMasterField(
            'MASTER // YEAR',
            firstProp.year,
            '2001',
            (val) => {
                entries.forEach(({ item }) => {
                    item.proposed.year = val;
                });
            }
        );

        const masterGenre = createMasterField(
            'MASTER // GENRE',
            firstProp.genre,
            'Eletrônica, Rock',
            (val) => {
                entries.forEach(({ item }) => {
                    item.proposed.genre = val;
                });
            }
        );

        const masterCoverUrl = createMasterField(
            'MASTER // COVER ART URL',
            firstProp.cover_url,
            'https://coverartarchive.org/...',
            (val) => {
                const hasUrl = Boolean(val && val.trim());
                entries.forEach(({ item, index }) => {
                    item.proposed.cover_url = val.trim();
                    item.proposed.has_cover = hasUrl || Boolean(item.original.has_cover);
                    updateRowBadges(index);
                });
                updateStats();
            }
        );

        vaCheck.addEventListener('change', (e) => {
            const newAlbumArtist = e.target.checked
                ? 'Various Artists'
                : (entries[0].item.proposed.artist || 'Artista Desconhecido');
            masterAlbumArtist.inp.value = newAlbumArtist;
            entries.forEach(({ item }) => {
                item.proposed.album_artist = newAlbumArtist;
            });
            headerTitle.textContent = `${unitPrefix} ${newAlbumArtist} // ${masterAlbum.inp.value || 'Álbum Desconhecido'}`;
            recalculateCollisionsAndPreviews();
        });

        masterGrid.appendChild(masterAlbumArtist.wrapper);
        masterGrid.appendChild(masterAlbum.wrapper);
        masterGrid.appendChild(masterYear.wrapper);
        masterGrid.appendChild(masterGenre.wrapper);
        masterGrid.appendChild(masterCoverUrl.wrapper);

        header.appendChild(headerTop);
        header.appendChild(masterGrid);
        card.appendChild(header);

        // --- Album Tracks Body ---
        if (!isCollapsed) {
            const body = document.createElement('div');
            body.className = 'album-tracks-body';

            entries.forEach(({ item, index }) => {
                const row = createTrackRowElement(item, index);
                body.appendChild(row);
            });

            card.appendChild(body);
        }

        albumsContainer.appendChild(card);
    }

    if (workspaceFilterQuery) {
        applyWorkspaceFilter();
    }
}

function createTrackRowElement(item, index) {
    const row = document.createElement('div');
    row.className = 'track-row';
    row.dataset.index = String(index);

    // 1. Checkbox Cell
    const checkCol = document.createElement('div');
    const rowCheck = document.createElement('input');
    rowCheck.type = 'checkbox';
    rowCheck.className = 'row-select';
    rowCheck.checked = selectedSources.has(item.source);
    rowCheck.dataset.source = item.source;
    rowCheck.addEventListener('change', (e) => {
        if (e.target.checked) {
            selectedSources.add(item.source);
        } else {
            selectedSources.delete(item.source);
        }
        recalculateCollisionsAndPreviews();
    });
    checkCol.appendChild(rowCheck);

    // 2. Original Metadata & Duration Cell
    const origCol = document.createElement('div');
    origCol.className = 'orig-info';

    const nameLine = document.createElement('div');
    nameLine.className = 'orig-name-line';

    const nameSpan = document.createElement('span');
    nameSpan.className = 'orig-name';
    nameSpan.textContent = item.original.filename || '';

    const durPill = document.createElement('span');
    durPill.className = 'duration-pill';
    durPill.title = `Duração física extraída: ${item.proposed.duration || item.original.duration || 0}s`;
    durPill.textContent = `⏱ ${formatDuration(item.proposed.duration || item.original.duration || 0)}`;

    nameLine.appendChild(nameSpan);
    nameLine.appendChild(durPill);

    const metaSpan = document.createElement('span');
    metaSpan.className = 'orig-meta';
    metaSpan.textContent = `RAW: ${item.original.artist || 'N/A'} // ${item.original.title || 'N/A'} [${item.original.album || 'N/A'}]`;

    const badgesDiv = document.createElement('div');
    badgesDiv.id = `badges-${index}`;
    badgesDiv.style.marginTop = '4px';

    origCol.appendChild(nameLine);
    origCol.appendChild(metaSpan);
    origCol.appendChild(badgesDiv);

    // 3. Track Edit Inputs + Real-time Before -> After Preview
    const editCol = document.createElement('div');
    editCol.className = 'track-edit-area';

    const fieldsGrid = document.createElement('div');
    fieldsGrid.className = 'track-fields-grid';

    const makeTrackInput = (labelTxt, fieldKey, classSuffix) => {
        const wrap = document.createElement('div');
        wrap.className = 'track-field';
        const lbl = document.createElement('label');
        lbl.textContent = labelTxt;
        const inp = document.createElement('input');
        inp.type = 'text';
        inp.spellcheck = false;
        inp.className = `input-prop-${classSuffix}`;
        inp.dataset.index = String(index);
        // Safe programmatic assignment preserves double quotes ("Heroes", 12" Mix)
        inp.value = item.proposed[fieldKey] !== undefined && item.proposed[fieldKey] !== null
            ? String(item.proposed[fieldKey])
            : '';
        inp.addEventListener('input', (e) => {
            filesState[index].proposed[fieldKey] = e.target.value;
            recalculateCollisionsAndPreviews();
        });
        wrap.appendChild(lbl);
        wrap.appendChild(inp);
        return wrap;
    };

    fieldsGrid.appendChild(makeTrackInput('TRK#', 'track', 'track'));
    fieldsGrid.appendChild(makeTrackInput('DSC#', 'disc', 'disc'));
    fieldsGrid.appendChild(makeTrackInput('TRACK TITLE', 'title', 'title'));
    fieldsGrid.appendChild(makeTrackInput('TRACK ARTIST', 'artist', 'artist'));

    // Live Preview Bar ("Antes -> Depois")
    const previewBox = document.createElement('div');
    previewBox.className = 'live-preview-box';
    previewBox.id = `preview-box-${index}`;

    const previewPathSpan = document.createElement('span');
    previewPathSpan.className = 'preview-path-text';
    previewPathSpan.id = `preview-path-${index}`;
    const initialRel = buildSafeRelativePathClient(
        item.proposed,
        item.original.extension || '.mp3',
        200
    );
    previewPathSpan.textContent = `> ${initialRel}`;

    const previewLenSpan = document.createElement('span');
    previewLenSpan.className = 'preview-char-count';
    previewLenSpan.id = `preview-len-${index}`;
    previewLenSpan.textContent = `[${String(initialRel.length).padStart(3, '0')}/200B]`;

    previewBox.appendChild(previewPathSpan);
    previewBox.appendChild(previewLenSpan);

    editCol.appendChild(fieldsGrid);
    editCol.appendChild(previewBox);

    // 4. Row Actions Cell
    const actionsCol = document.createElement('div');
    actionsCol.className = 'cell-actions';

    const srcBadge = document.createElement('span');
    srcBadge.id = `badge-source-${index}`;
    srcBadge.className = item.proposed.source === 'MusicBrainz' ? 'badge badge-mb' : 'badge badge-local';
    srcBadge.textContent = item.proposed.source === 'MusicBrainz' ? 'MBZ//SYNC' : 'HEUR//LOC';

    const mbBtn = document.createElement('button');
    mbBtn.className = 'btn secondary-btn cell-mb-btn';
    mbBtn.type = 'button';
    mbBtn.style.padding = '5px 8px';
    mbBtn.style.fontSize = '10.5px';
    mbBtn.textContent = '[ SYNC TRK ]';
    mbBtn.addEventListener('click', async () => {
        mbBtn.disabled = true;
        mbBtn.textContent = '[ WAIT... ]';
        await fetchMusicBrainzSingle(index);
        mbBtn.disabled = false;
        mbBtn.textContent = '[ SYNC TRK ]';
    });

    const manualBtn = document.createElement('button');
    manualBtn.className = 'btn text-btn cell-manual-btn';
    manualBtn.type = 'button';
    manualBtn.style.padding = '4px 6px';
    manualBtn.style.fontSize = '10px';
    manualBtn.textContent = 'MANUAL // OVR';
    manualBtn.addEventListener('click', () => {
        manualSearchIndex = index;
        searchArtistInput.value = item.proposed.artist || '';
        searchTitleInput.value = item.proposed.title || '';
        searchModal.classList.remove('hidden');
    });

    actionsCol.appendChild(srcBadge);
    actionsCol.appendChild(mbBtn);
    actionsCol.appendChild(manualBtn);

    row.appendChild(checkCol);
    row.appendChild(origCol);
    row.appendChild(editCol);
    row.appendChild(actionsCol);

    setTimeout(() => updateRowBadges(index), 0);
    return row;
}

function updateRowBadges(index) {
    const item = filesState[index];
    if (!item) return;
    const badgesContainer = document.getElementById(`badges-${index}`);
    if (!badgesContainer) return;

    const coverBadge = item.proposed.has_cover
        ? `<span class="badge badge-has-cover">COVER:4:2:0</span>`
        : `<span class="badge badge-no-cover">NO_COVER</span>`;

    let lyricsBadge = '';
    if (item.proposed.lyrics) {
        if (item.proposed.lyrics.synced) {
            lyricsBadge = `<span class="badge" style="background: rgba(0, 240, 255, 0.14); color: var(--accent-cyan); border: 1px solid rgba(0, 240, 255, 0.4); margin-left: 5px;">LRC:SYNC</span>`;
        } else if (item.proposed.lyrics.plain) {
            lyricsBadge = `<span class="badge" style="background: rgba(252, 238, 9, 0.14); color: var(--accent-amber); border: 1px solid rgba(252, 238, 9, 0.35); margin-left: 5px;">LRC:PLAIN</span>`;
        }
    } else {
        lyricsBadge = `<span class="badge" style="background: rgba(255, 255, 255, 0.02); color: var(--text-muted); border: 1px solid var(--border-dim); margin-left: 5px;">NO_LRC</span>`;
    }

    const alreadyOrganizedBadge = item.already_organized
        ? `<span class="badge" style="background: rgba(0, 255, 102, 0.14); color: var(--success); border: 1px solid rgba(0, 255, 102, 0.4); margin-left: 5px;">ON_SD_CARD</span>`
        : '';

    const isCollision = collidingTrackIndices.has(index);
    const collisionBadge = isCollision
        ? `<span class="badge badge-collision" style="margin-left: 5px;">!COLLISION!</span>`
        : '';

    const extUpper = escapeHtml((item.original.extension || '.mp3').toUpperCase().replace('.', ''));
    const extBadge = `<span class="badge" style="background: #0c1017; color: var(--text-secondary); border: 1px solid var(--border-bright); margin-left: 5px;">${extUpper}</span>`;

    badgesContainer.innerHTML = `${coverBadge}${lyricsBadge}${alreadyOrganizedBadge}${extBadge}${collisionBadge}`;
}

function toggleAllSelection(isChecked) {
    selectedSources.clear();
    if (isChecked) {
        filesState.forEach(f => selectedSources.add(f.source));
    }
    renderAlbumsWorkspace();
    recalculateCollisionsAndPreviews();
}

// =========================================================================
// 7. MusicBrainz Single & Release-Centric Batch Lookups
// =========================================================================

async function fetchMusicBrainzSingle(index) {
    const item = filesState[index];
    if (!item) return;

    setVfdMode('QUERY // MBZ');
    const payload = {
        artist: item.proposed.artist,
        title: item.proposed.title,
        album: item.proposed.album,
        filename: item.original.filename,
        duration: item.proposed.duration || item.original.duration || 0
    };

    try {
        const response = await fetch('/api/musicbrainz', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (data.success && data.tags) {
            filesState[index].proposed = {
                ...filesState[index].proposed,
                ...data.tags,
                lyrics: data.lyrics || filesState[index].proposed.lyrics || null,
                has_cover: Boolean(data.tags.cover_url) || Boolean(filesState[index].proposed.has_cover),
                source: 'MusicBrainz'
            };
            renderAlbumsWorkspace();
            recalculateCollisionsAndPreviews();
        }
    } catch (e) {
        console.error('Error fetching single MusicBrainz tags:', e);
    } finally {
        setVfdMode(`READY // ${filesState.length} TRKS`);
    }
}

async function executeManualSearch() {
    const artist = searchArtistInput.value.trim();
    const title = searchTitleInput.value.trim();
    if (!title || manualSearchIndex === null) {
        alert('Por favor, preencha o Título da Música.');
        return;
    }

    const item = filesState[manualSearchIndex];
    setBtnLoading(searchSubmitBtn, true);
    setVfdMode('OVR // MBZ');

    try {
        const response = await fetch('/api/musicbrainz', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                artist,
                title,
                album: item.proposed.album,
                duration: item.proposed.duration || item.original.duration || 0
            })
        });
        const data = await response.json();
        if (data.success && data.tags) {
            filesState[manualSearchIndex].proposed = {
                ...filesState[manualSearchIndex].proposed,
                ...data.tags,
                lyrics: data.lyrics || filesState[manualSearchIndex].proposed.lyrics || null,
                has_cover: Boolean(data.tags.cover_url) || Boolean(filesState[manualSearchIndex].proposed.has_cover),
                source: 'MusicBrainz'
            };
            searchModal.classList.add('hidden');
            manualSearchIndex = null;
            renderAlbumsWorkspace();
            recalculateCollisionsAndPreviews();
        } else {
            alert('Nenhum metadado encontrado para essa busca no MusicBrainz.');
        }
    } catch (err) {
        alert('Erro na busca: ' + err.message);
    } finally {
        setBtnLoading(searchSubmitBtn, false);
        setVfdMode(`READY // ${filesState.length} TRKS`);
    }
}

let cancelBulkSync = false;

async function fetchMusicBrainzReleaseBatch(targetIndices = null) {
    if (isFetchingBulk) {
        cancelBulkSync = true;
        return;
    }

    let indicesToProcess = targetIndices;
    if (!indicesToProcess) {
        indicesToProcess = [];
        filesState.forEach((f, idx) => {
            if (selectedSources.has(f.source)) {
                indicesToProcess.push(idx);
            }
        });
    }

    if (indicesToProcess.length === 0) {
        alert('Selecione pelo menos uma música ou álbum para buscar.');
        return;
    }

    // Group target indices by albumGroupId so each album is synced and rendered live
    const albumBatches = new Map();
    indicesToProcess.forEach(idx => {
        const grpId = filesState[idx]?.albumGroupId || `single-${idx}`;
        if (!albumBatches.has(grpId)) {
            albumBatches.set(grpId, []);
        }
        albumBatches.get(grpId).push(idx);
    });

    const batchList = Array.from(albumBatches.values());
    const totalBatches = batchList.length;

    isFetchingBulk = true;
    cancelBulkSync = false;
    const btnTextSpan = mbBulkBtn ? mbBulkBtn.querySelector('.btn-text') : null;
    const originalBtnText = btnTextSpan ? btnTextSpan.textContent : 'SINCRONIZAR ÁLBUNS [MUSICBRAINZ + LRCLIB]';
    const spinner = mbBulkBtn ? mbBulkBtn.querySelector('.spinner') : null;
    if (spinner) spinner.classList.remove('hidden');

    const cassetteEl = document.getElementById('deck-cassette');
    if (cassetteEl) cassetteEl.classList.add('spinning');

    try {
        for (let bIdx = 0; bIdx < totalBatches; bIdx++) {
            if (cancelBulkSync) break;

            const groupIndices = batchList[bIdx];
            const firstItem = filesState[groupIndices[0]];
            const albumLabel = (firstItem?.proposed?.album || firstItem?.proposed?.artist || 'ÁLBUM').slice(0, 22).toUpperCase();

            setVfdMode(`SYNC [${bIdx + 1}/${totalBatches}] // ${albumLabel}`);
            if (btnTextSpan) {
                btnTextSpan.textContent = `SINCRONIZANDO [${bIdx + 1}/${totalBatches}] (CLIQUE P/ PARAR)`;
            }

            const itemsPayload = groupIndices.map(idx => ({
                source: filesState[idx].source,
                filename: filesState[idx].original.filename,
                proposed: filesState[idx].proposed
            }));

            const response = await fetch('/api/musicbrainz/release-batch', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    items: itemsPayload,
                    fetch_lyrics: true
                })
            });

            if (!response.ok) {
                throw new Error('Falha ao consultar lote Release-Centric no servidor.');
            }

            const data = await response.json();
            if (data.success && Array.isArray(data.items)) {
                data.items.forEach((enrichedItem, pos) => {
                    const stateIdx = groupIndices[pos];
                    if (filesState[stateIdx] && enrichedItem.proposed) {
                        filesState[stateIdx].proposed = {
                            ...filesState[stateIdx].proposed,
                            ...enrichedItem.proposed
                        };
                    }
                });
                renderAlbumsWorkspace();
                recalculateCollisionsAndPreviews();
            }
        }
    } catch (e) {
        alert('Erro na busca por álbum: ' + e.message);
    } finally {
        isFetchingBulk = false;
        cancelBulkSync = false;
        if (btnTextSpan) btnTextSpan.textContent = originalBtnText;
        if (spinner) spinner.classList.add('hidden');
        if (cassetteEl) cassetteEl.classList.remove('spinning');
        setVfdMode(`READY // ${filesState.length} TRKS`);
    }
}

// =========================================================================
// 8. Full-Batch Processing & Real-Time SSE Streaming (/api/jobs/process)
// =========================================================================

function appendModalLog(level, message) {
    const item = document.createElement('div');
    item.className = `log-item ${level || 'info'}`;
    item.textContent = message;
    logPanel.appendChild(item);
    logPanel.scrollTop = logPanel.scrollHeight;
}

function closeActiveSSE() {
    if (activeEventSource) {
        activeEventSource.close();
        activeEventSource = null;
    }
    activeJobId = null;
    isJobPaused = false;
}

async function processSelectedFilesSSE() {
    const destDir = destDirInput.value.trim();
    if (!destDir) {
        alert('Por favor, informe ou selecione o Diretório de Destino.');
        return;
    }

    recalculateCollisionsAndPreviews();
    if (collidingTrackIndices.size > 0) {
        alert('Existem colisões de destino nas faixas selecionadas. Resolva as colisões destacadas antes de iniciar a gravação.');
        return;
    }

    const selectedList = filesState.filter(f => selectedSources.has(f.source));
    if (selectedList.length === 0) {
        alert('Nenhum arquivo selecionado para gravar.');
        return;
    }

    localStorage.setItem('music_dest_dir', destDir);
    setVfdMode('WRITING // FAT32');

    // Reset and open Processing Modal
    processModal.classList.remove('hidden');
    progressFill.style.width = '0%';
    progressPercent.textContent = '0%';
    progressFraction.textContent = `0 / ${selectedList.length}`;
    progressCurrentAlbum.textContent = 'INICIALIZANDO LOTE COMPLETO...';
    progressCurrentFile.textContent = '';
    processStageBadge.textContent = 'PASSADA 1: ÁUDIO';
    logPanel.innerHTML = '';
    jobControlBtns.classList.remove('hidden');
    modalSuccessActions.classList.add('hidden');
    jobPauseBtn.disabled = false;
    jobCancelBtn.disabled = false;
    jobPauseBtn.textContent = '⏸ PAUSAR LOTE';
    isJobPaused = false;

    const mappings = selectedList.map(file => ({
        source: file.source,
        tags: file.proposed
    }));

    try {
        const response = await fetch('/api/jobs/process', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                dest_dir: destDir,
                mappings: mappings,
                generate_index: true
            })
        });

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || 'Falha ao iniciar o job de gravação.');
        }

        const startData = await response.json();
        activeJobId = startData.job_id;

        activeEventSource = new EventSource(`/api/jobs/${activeJobId}/stream`);

        activeEventSource.onmessage = (event) => {
            try {
                const payload = JSON.parse(event.data);
                handleJobSSEEvent(payload);
            } catch (parseErr) {
                console.error('Error parsing SSE event:', parseErr);
            }
        };

        activeEventSource.onerror = () => {
            if (activeEventSource) {
                activeEventSource.close();
                activeEventSource = null;
            }
        };
    } catch (error) {
        appendModalLog('error', `[FATAL] Erro ao iniciar lote: ${error.message}`);
        jobControlBtns.classList.add('hidden');
        modalSuccessActions.classList.remove('hidden');
        setVfdMode('ERR // WRITE');
    }
}

function handleJobSSEEvent(ev) {
    if (ev.stage === 'pass_1_audio') {
        processStageBadge.textContent = 'PASSADA 1: ÁUDIO SEQUENCIAL';
        setVfdMode('PASS_01 // AUDIO');
    } else if (ev.stage === 'pass_2_lrc') {
        processStageBadge.textContent = 'PASSADA 2: SIDECARS .LRC (BOM)';
        setVfdMode('PASS_02 // LRC');
    } else if (ev.stage === 'indexing') {
        processStageBadge.textContent = 'GERANDO LIBRARY_INDEX.TXT';
        setVfdMode('INDEX // TREE');
    } else if (ev.stage === 'done') {
        processStageBadge.textContent = ev.status === 'cancelled' ? 'ABORTADO' : 'CONCLUÍDO';
        setVfdMode(ev.status === 'cancelled' ? 'ABORTED' : 'SYNC // DONE');
    }

    if (ev.album) {
        progressCurrentAlbum.textContent = `ALBUM // ${ev.album}`;
    }
    if (ev.current_file) {
        progressCurrentFile.textContent = ev.current_file;
    }

    if (typeof ev.percent === 'number') {
        progressFill.style.width = `${ev.percent}%`;
        progressPercent.textContent = `${ev.percent}%`;
    }
    if (typeof ev.processed === 'number' && typeof ev.total === 'number') {
        progressFraction.textContent = `${ev.processed} / ${ev.total}`;
    }

    if (ev.log && ev.log.message) {
        appendModalLog(ev.log.level || 'info', ev.log.message);
    }

    if (ev.type === 'completed' || ev.type === 'cancelled' || ev.type === 'error') {
        closeActiveSSE();
        jobControlBtns.classList.add('hidden');
        modalSuccessActions.classList.remove('hidden');
    }
}

async function togglePauseActiveJob() {
    if (!activeJobId) return;
    const action = isJobPaused ? 'resume' : 'pause';
    try {
        const res = await fetch(`/api/jobs/${activeJobId}/${action}`, { method: 'POST' });
        if (res.ok) {
            const data = await res.json();
            isJobPaused = data.status === 'paused';
            jobPauseBtn.textContent = isJobPaused ? '▶ RETOMAR LOTE' : '⏸ PAUSAR LOTE';
            setVfdMode(isJobPaused ? 'PAUSED // HOLD' : 'WRITING // FAT32');
        }
    } catch (e) {
        console.error('Error toggling pause:', e);
    }
}

async function cancelActiveJob() {
    if (!activeJobId) return;
    jobCancelBtn.disabled = true;
    jobPauseBtn.disabled = true;
    try {
        await fetch(`/api/jobs/${activeJobId}/cancel`, { method: 'POST' });
    } catch (e) {
        console.error('Error cancelling job:', e);
    }
}
