// Application State
let filesState = []; // Array of { source, original: {}, proposed: {} }
let selectedSources = new Set();
let isScanning = false;
let isFetchingBulk = false;
let manualSearchIndex = null; // Track which row is being manually searched

// DOM Elements
const sourceDirInput = document.getElementById('source-dir');
const destDirInput = document.getElementById('dest-dir');
const browseSourceBtn = document.getElementById('browse-source-btn');
const browseDestBtn = document.getElementById('browse-dest-btn');
const scanBtn = document.getElementById('scan-btn');
const emptyState = document.getElementById('empty-state');
const statsPanel = document.getElementById('stats-panel');
const workspacePanel = document.getElementById('workspace-panel');
const musicListTbody = document.getElementById('music-list-tbody');

const totalCountEl = document.getElementById('total-count');
const mbMatchesCountEl = document.getElementById('mb-matches-count');
const coverCountEl = document.getElementById('cover-count');
const selectedCountLabel = document.getElementById('selected-count-label');

const selectAllBtn = document.getElementById('select-all-btn');
const deselectAllBtn = document.getElementById('deselect-all-btn');
const headerSelectAll = document.getElementById('header-select-all');
const mbBulkBtn = document.getElementById('mb-bulk-btn');
const processBtn = document.getElementById('process-btn');
const clearQueueBtn = document.getElementById('clear-queue-btn');

// Processing Modal Elements
const processModal = document.getElementById('process-modal');
const progressFill = document.getElementById('progress-fill');
const progressPercent = document.getElementById('progress-percent');
const progressFraction = document.getElementById('progress-fraction');
const logPanel = document.getElementById('log-panel');
const modalSuccessActions = document.getElementById('modal-success-actions');
const modalCloseBtn = document.getElementById('modal-close-btn');
const progressCurrentFile = document.getElementById('progress-current-file');

// Manual Search Modal Elements
const searchModal = document.getElementById('search-modal');
const searchArtistInput = document.getElementById('search-artist');
const searchTitleInput = document.getElementById('search-title');
const searchCancelBtn = document.getElementById('search-cancel-btn');
const searchSubmitBtn = document.getElementById('search-submit-btn');

// Event Listeners
scanBtn.addEventListener('click', scanDirectory);
browseSourceBtn.addEventListener('click', handleBrowseSource);
browseDestBtn.addEventListener('click', handleBrowseDest);
selectAllBtn.addEventListener('click', () => toggleAllSelection(true));
deselectAllBtn.addEventListener('click', () => toggleAllSelection(false));
headerSelectAll.addEventListener('change', (e) => toggleAllSelection(e.target.checked));
mbBulkBtn.addEventListener('click', fetchMusicBrainzBulk);
processBtn.addEventListener('click', processSelectedFiles);
clearQueueBtn.addEventListener('click', () => {
    filesState = [];
    selectedSources.clear();
    updateStats();
    renderTable();
    emptyState.classList.remove('hidden');
    statsPanel.classList.add('hidden');
    workspacePanel.classList.add('hidden');
});
modalCloseBtn.addEventListener('click', () => {
    processModal.classList.add('hidden');
    scanDirectory();
});

// Manual Search Bindings
searchCancelBtn.addEventListener('click', () => {
    searchModal.classList.add('hidden');
    manualSearchIndex = null;
});
searchSubmitBtn.addEventListener('click', executeManualSearch);

// Load saved paths from localStorage if available
document.addEventListener('DOMContentLoaded', () => {
    const savedSource = localStorage.getItem('music_source_dir');
    const savedDest = localStorage.getItem('music_dest_dir');
    if (savedSource) sourceDirInput.value = savedSource;
    if (savedDest) destDirInput.value = savedDest;
});

// Helper: Show/Hide spinner on a button
function setBtnLoading(btn, isLoading) {
    const spinner = btn.querySelector('.spinner');
    const text = btn.querySelector('.btn-text') || btn;
    if (isLoading) {
        btn.disabled = true;
        if (spinner) spinner.classList.remove('hidden');
    } else {
        btn.disabled = false;
        if (spinner) spinner.classList.add('hidden');
    }
}

// 1. Scan Directory
async function scanDirectory() {
    const sourceDir = sourceDirInput.value.trim();
    if (!sourceDir) {
        alert("Por favor, digite o caminho da pasta de origem.");
        return;
    }
    
    const destDir = destDirInput.value.trim();
    
    // Save to localStorage
    localStorage.setItem('music_source_dir', sourceDir);
    if (destDir) localStorage.setItem('music_dest_dir', destDir);
    
    isScanning = true;
    setBtnLoading(scanBtn, true);
    
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
            throw new Error(err.detail || "Erro ao varrer diretório.");
        }
        
        const data = await response.json();
        filesState = data.files || [];
        
        // Reset selection state (select only non-organized files by default)
        selectedSources.clear();
        let anyDeselected = false;
        filesState.forEach(f => {
            if (!f.already_organized) {
                selectedSources.add(f.source);
            } else {
                anyDeselected = true;
            }
        });
        headerSelectAll.checked = !anyDeselected && filesState.length > 0;
        
        updateStats();
        renderTable();
        
        if (filesState.length > 0) {
            emptyState.classList.add('hidden');
            statsPanel.classList.remove('hidden');
            workspacePanel.classList.remove('hidden');
        } else {
            emptyState.classList.remove('hidden');
            statsPanel.classList.add('hidden');
            workspacePanel.classList.add('hidden');
            alert("Nenhum arquivo de áudio compatível (.mp3, .flac, .m4a) encontrado neste diretório.");
        }
        
    } catch (error) {
        alert("Erro: " + error.message);
    } finally {
        isScanning = false;
        setBtnLoading(scanBtn, false);
    }
}

// 2. Update stats and selections labels
function updateStats() {
    totalCountEl.innerText = filesState.length;
    
    const mbCount = filesState.filter(f => f.proposed.source === 'MusicBrainz').length;
    mbMatchesCountEl.innerText = mbCount;
    
    const coverCount = filesState.filter(f => f.proposed.has_cover).length;
    coverCountEl.innerText = coverCount;
    
    selectedCountLabel.innerText = `${selectedSources.size} de ${filesState.length} músicas selecionadas para gravar`;
}

// 3. Render Music Table
function renderTable() {
    musicListTbody.innerHTML = '';
    
    filesState.forEach((item, index) => {
        const row = document.createElement('tr');
        row.dataset.index = index;
        
        const isChecked = selectedSources.has(item.source);
        const extUpper = (item.original.extension || '.mp3').toUpperCase().replace('.', '');
        
        const coverBadge = item.proposed.has_cover 
            ? `<span class="badge badge-has-cover">Com Capa</span>`
            : `<span class="badge badge-no-cover">Sem Capa</span>`;
            
        let lyricsBadge = '';
        if (item.proposed.lyrics) {
            if (item.proposed.lyrics.synced) {
                lyricsBadge = `<span class="badge" style="background: rgba(14, 165, 233, 0.15); color: var(--accent-cyan); border: 1px solid rgba(14, 165, 233, 0.3); margin-left: 5px;">Letra Sinc</span>`;
            } else if (item.proposed.lyrics.plain) {
                lyricsBadge = `<span class="badge" style="background: rgba(245, 158, 11, 0.15); color: #f59e0b; border: 1px solid rgba(245, 158, 11, 0.3); margin-left: 5px;">Letra Plain</span>`;
            }
        } else {
            lyricsBadge = `<span class="badge" style="background: rgba(255, 255, 255, 0.03); color: var(--text-secondary); border: 1px solid rgba(255, 255, 255, 0.05); margin-left: 5px;">Sem Letra</span>`;
        }
            
        const alreadyOrganizedBadge = item.already_organized 
            ? `<span class="badge" style="background: rgba(16, 185, 129, 0.15); color: var(--success); border: 1px solid rgba(16, 185, 129, 0.3); margin-left: 5px;">Já Organizado</span>`
            : '';
            
        const extBadge = `<span class="badge" style="background: rgba(255, 255, 255, 0.05); color: var(--text-secondary); border: 1px solid var(--border-color); margin-left: 5px;">${extUpper}</span>`;
            
        const sourceBadge = item.proposed.source === 'MusicBrainz'
            ? `<span class="badge badge-mb" id="badge-source-${index}">MusicBrainz</span>`
            : `<span class="badge badge-local" id="badge-source-${index}">Local</span>`;
            
        row.innerHTML = `
            <td>
                <input type="checkbox" class="row-select" ${isChecked ? 'checked' : ''} data-source="${item.source}">
            </td>
            <td>
                <div class="orig-info">
                    <span class="orig-name">${item.original.filename}</span>
                    <span class="orig-meta">
                        Tag original: 
                        ${item.original.artist || 'Sem Artista'} - 
                        ${item.original.title || 'Sem Título'} 
                        [${item.original.album || 'Sem Álbum'}]
                    </span>
                    <div style="margin-top: 5px;" id="badges-${index}">${coverBadge}${lyricsBadge}${alreadyOrganizedBadge}${extBadge}</div>
                </div>
            </td>
            <td>
                <div class="direction-arrow">
                    <svg class="arrow-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <polyline points="9 18 15 12 9 6"></polyline>
                    </svg>
                </div>
            </td>
            <td>
                <div class="proposed-form">
                    <div class="form-group span-2">
                        <label>Título</label>
                        <input type="text" value="${item.proposed.title}" class="input-prop-title" data-index="${index}">
                    </div>
                    <div class="form-group">
                        <label>Artista (Faixa)</label>
                        <input type="text" value="${item.proposed.artist}" class="input-prop-artist" data-index="${index}">
                    </div>
                    <div class="form-group">
                        <label>Artista do Álbum (Grupo/Pasta)</label>
                        <input type="text" value="${item.proposed.album_artist}" class="input-prop-album_artist" data-index="${index}">
                    </div>
                    <div class="form-group span-2">
                        <label>Álbum</label>
                        <input type="text" value="${item.proposed.album}" class="input-prop-album" data-index="${index}">
                    </div>
                    <div class="form-group" style="grid-column: span 1;">
                        <label>Faixa</label>
                        <input type="text" value="${item.proposed.track}" class="input-prop-track" data-index="${index}">
                    </div>
                    <div class="form-group" style="grid-column: span 1;">
                        <div style="display: flex; gap: 8px;">
                            <div style="flex: 1;">
                                <label>Disco</label>
                                <input type="text" value="${item.proposed.disc}" class="input-prop-disc" data-index="${index}" style="width: 100%;">
                            </div>
                            <div style="flex: 1.5;">
                                <label>Ano</label>
                                <input type="text" value="${item.proposed.year}" class="input-prop-year" data-index="${index}" style="width: 100%;">
                            </div>
                        </div>
                    </div>
                </div>
            </td>
            <td>
                <div class="cell-actions" style="gap: 4px;">
                    ${sourceBadge}
                    <button class="btn secondary-btn cell-mb-btn" data-index="${index}" style="padding: 5px 8px; font-size: 11px;">
                        Buscar MB
                    </button>
                    <button class="btn text-btn cell-manual-btn" data-index="${index}" style="padding: 3px 6px; font-size: 10px; color: var(--accent-cyan);">
                        Busca Manual
                    </button>
                </div>
            </td>
        `;
        
        musicListTbody.appendChild(row);
    });
    
    // Bind change listeners to inputs
    document.querySelectorAll('.input-prop-title').forEach(el => el.addEventListener('input', updateLocalState));
    document.querySelectorAll('.input-prop-artist').forEach(el => el.addEventListener('input', updateLocalState));
    document.querySelectorAll('.input-prop-album_artist').forEach(el => el.addEventListener('input', updateLocalState));
    document.querySelectorAll('.input-prop-album').forEach(el => el.addEventListener('input', updateLocalState));
    document.querySelectorAll('.input-prop-track').forEach(el => el.addEventListener('input', updateLocalState));
    document.querySelectorAll('.input-prop-disc').forEach(el => el.addEventListener('input', updateLocalState));
    document.querySelectorAll('.input-prop-year').forEach(el => el.addEventListener('input', updateLocalState));
    
    // Bind checkbox listeners
    document.querySelectorAll('.row-select').forEach(el => el.addEventListener('change', handleRowSelect));
    
    // Bind single MB search buttons
    document.querySelectorAll('.cell-mb-btn').forEach(el => el.addEventListener('click', async (e) => {
        const index = e.target.dataset.index;
        e.target.disabled = true;
        e.target.innerText = 'Buscando...';
        await fetchMusicBrainzSingle(index);
        e.target.disabled = false;
        e.target.innerText = 'Buscar MB';
    }));
    
    // Bind single manual search buttons
    document.querySelectorAll('.cell-manual-btn').forEach(el => el.addEventListener('click', (e) => {
        manualSearchIndex = parseInt(e.target.dataset.index);
        const item = filesState[manualSearchIndex];
        searchArtistInput.value = item.proposed.artist || '';
        searchTitleInput.value = item.proposed.title || '';
        searchModal.classList.remove('hidden');
    }));
}

// 4. Update state when user edits inputs in the table
function updateLocalState(e) {
    const index = parseInt(e.target.dataset.index);
    const field = e.target.className.replace('input-prop-', '');
    filesState[index].proposed[field] = e.target.value;
}

// 5. Handle Checkbox selection
function handleRowSelect(e) {
    const source = e.target.dataset.source;
    if (e.target.checked) {
        selectedSources.add(source);
    } else {
        selectedSources.delete(source);
    }
    headerSelectAll.checked = selectedSources.size === filesState.length;
    updateStats();
}

// 6. Toggle all selection boxes
function toggleAllSelection(isChecked) {
    selectedSources.clear();
    if (isChecked) {
        filesState.forEach(f => selectedSources.add(f.source));
    }
    
    document.querySelectorAll('.row-select').forEach(el => {
        el.checked = isChecked;
    });
    headerSelectAll.checked = isChecked;
    updateStats();
}

// Helper: Update badges of a row dynamically
function updateRowBadges(index) {
    const item = filesState[index];
    const badgesContainer = document.getElementById(`badges-${index}`);
    if (!badgesContainer) return;
    
    const coverBadge = item.proposed.has_cover 
        ? `<span class="badge badge-has-cover">Com Capa</span>`
        : `<span class="badge badge-no-cover">Sem Capa</span>`;
        
    let lyricsBadge = '';
    if (item.proposed.lyrics) {
        if (item.proposed.lyrics.synced) {
            lyricsBadge = `<span class="badge" style="background: rgba(14, 165, 233, 0.15); color: var(--accent-cyan); border: 1px solid rgba(14, 165, 233, 0.3); margin-left: 5px;">Letra Sinc</span>`;
        } else if (item.proposed.lyrics.plain) {
            lyricsBadge = `<span class="badge" style="background: rgba(245, 158, 11, 0.15); color: #f59e0b; border: 1px solid rgba(245, 158, 11, 0.3); margin-left: 5px;">Letra Plain</span>`;
        }
    } else {
        lyricsBadge = `<span class="badge" style="background: rgba(255, 255, 255, 0.03); color: var(--text-secondary); border: 1px solid rgba(255, 255, 255, 0.05); margin-left: 5px;">Sem Letra</span>`;
    }
        
    const alreadyOrganizedBadge = item.already_organized 
        ? `<span class="badge" style="background: rgba(16, 185, 129, 0.15); color: var(--success); border: 1px solid rgba(16, 185, 129, 0.3); margin-left: 5px;">Já Organizado</span>`
        : '';
        
    const extUpper = (item.original.extension || '.mp3').toUpperCase().replace('.', '');
    const extBadge = `<span class="badge" style="background: rgba(255, 255, 255, 0.05); color: var(--text-secondary); border: 1px solid var(--border-color); margin-left: 5px;">${extUpper}</span>`;
    
    badgesContainer.innerHTML = `${coverBadge}${lyricsBadge}${alreadyOrganizedBadge}${extBadge}`;
}

// 7. Search MusicBrainz for a single track
async function fetchMusicBrainzSingle(index) {
    const item = filesState[index];
    const artist = item.proposed.artist;
    const title = item.proposed.title;
    const filename = item.original.filename;
    
    try {
        const response = await fetch('/api/musicbrainz', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ artist, title, filename })
        });
        
        const data = await response.json();
        if (data.success) {
            // Update proposed tags in state
            filesState[index].proposed = {
                ...filesState[index].proposed,
                ...data.tags,
                lyrics: data.lyrics || null,
                source: "MusicBrainz"
            };
            
            // Re-render only that row for speed
            const row = document.querySelector(`tr[data-index="${index}"]`);
            if (row) {
                row.querySelector('.input-prop-title').value = data.tags.title || '';
                row.querySelector('.input-prop-artist').value = data.tags.artist || '';
                row.querySelector('.input-prop-album_artist').value = data.tags.album_artist || '';
                row.querySelector('.input-prop-album').value = data.tags.album || '';
                row.querySelector('.input-prop-track').value = data.tags.track || '';
                row.querySelector('.input-prop-disc').value = data.tags.disc || '1';
                row.querySelector('.input-prop-year').value = data.tags.year || '';
                
                // Update Source Badge
                const badge = document.getElementById(`badge-source-${index}`);
                if (badge) {
                    badge.className = 'badge badge-mb';
                    badge.innerText = 'MusicBrainz';
                }
                
                // Update Badges (Cover, Lyrics, etc.)
                updateRowBadges(index);
            }
            updateStats();
        } else {
            console.log(`MusicBrainz search returned no results for: ${title}`);
        }
    } catch (e) {
        console.error("Error fetching single MusicBrainz tags:", e);
    }
}

// 8. Execute Manual Search Modal Query
async function executeManualSearch() {
    const artist = searchArtistInput.value.trim();
    const title = searchTitleInput.value.trim();
    
    if (!title) {
        alert("Por favor, preencha o Título da Música.");
        return;
    }
    
    setBtnLoading(searchSubmitBtn, true);
    
    try {
        const response = await fetch('/api/musicbrainz', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ artist, title })
        });
        
        const data = await response.json();
        if (data.success && manualSearchIndex !== null) {
            // Apply suggestions to state
            filesState[manualSearchIndex].proposed = {
                ...filesState[manualSearchIndex].proposed,
                ...data.tags,
                lyrics: data.lyrics || null,
                source: "MusicBrainz"
            };
            
            // Close modal
            searchModal.classList.add('hidden');
            
            // Re-render row
            const index = manualSearchIndex;
            const row = document.querySelector(`tr[data-index="${index}"]`);
            if (row) {
                row.querySelector('.input-prop-title').value = data.tags.title || '';
                row.querySelector('.input-prop-artist').value = data.tags.artist || '';
                row.querySelector('.input-prop-album_artist').value = data.tags.album_artist || '';
                row.querySelector('.input-prop-album').value = data.tags.album || '';
                row.querySelector('.input-prop-track').value = data.tags.track || '';
                row.querySelector('.input-prop-disc').value = data.tags.disc || '1';
                row.querySelector('.input-prop-year').value = data.tags.year || '';
                
                // Update Source Badge
                const badge = document.getElementById(`badge-source-${index}`);
                if (badge) {
                    badge.className = 'badge badge-mb';
                    badge.innerText = 'MusicBrainz';
                }
                
                // Update Badges (Cover, Lyrics, etc.)
                updateRowBadges(index);
            }
            updateStats();
            manualSearchIndex = null;
        } else {
            alert("Nenhum metadado encontrado para essa busca no MusicBrainz.");
        }
    } catch (err) {
        alert("Erro na busca: " + err.message);
    } finally {
        setBtnLoading(searchSubmitBtn, false);
    }
}

// 9. Bulk fetch MusicBrainz for all checked files (Rate Limited 1.2s delay)
async function fetchMusicBrainzBulk() {
    const selectedList = filesState.filter(f => selectedSources.has(f.source));
    if (selectedList.length === 0) {
        alert("Selecione pelo menos uma música para buscar em lote.");
        return;
    }
    
    if (isFetchingBulk) return;
    
    isFetchingBulk = true;
    setBtnLoading(mbBulkBtn, true);
    mbBulkBtn.querySelector('.btn-text').innerText = "Buscando Lote (Aguarde)...";
    
    for (let i = 0; i < filesState.length; i++) {
        if (!selectedSources.has(filesState[i].source)) continue;
        
        const row = document.querySelector(`tr[data-index="${i}"]`);
        if (row) row.style.background = 'rgba(139, 92, 246, 0.05)';
        
        await fetchMusicBrainzSingle(i);
        
        if (row) row.style.background = '';
        await new Promise(resolve => setTimeout(resolve, 1200));
    }
    
    isFetchingBulk = false;
    setBtnLoading(mbBulkBtn, false);
    mbBulkBtn.querySelector('.btn-text').innerText = "Buscar Lote MusicBrainz";
    alert("Busca em lote concluída!");
}

// 10. Process selected files in batches and generate final index
async function processSelectedFiles() {
    const destDir = destDirInput.value.trim();
    if (!destDir) {
        alert("Por favor, digite o caminho da pasta de destino (onde os arquivos organizados serão gravados).");
        return;
    }
    
    const selectedList = filesState.filter(f => selectedSources.has(f.source));
    if (selectedList.length === 0) {
        alert("Nenhum arquivo selecionado para gravar.");
        return;
    }
    
    // Save destination to localStorage
    localStorage.setItem('music_dest_dir', destDir);
    
    // Open modal
    processModal.classList.remove('hidden');
    progressFill.style.width = '0%';
    progressPercent.innerText = '0%';
    progressFraction.innerText = `0 / ${selectedList.length}`;
    logPanel.innerHTML = '';
    modalSuccessActions.classList.add('hidden');
    
    const total = selectedList.length;
    let processed = 0;
    
    const batchSize = 1;
    
    for (let i = 0; i < total; i += batchSize) {
        const batch = selectedList.slice(i, i + batchSize);
        progressCurrentFile.innerText = `Processando: ${batch[0].original.filename}...`;
        const mappings = batch.map(file => ({
            source: file.source,
            tags: file.proposed
        }));
        
        try {
            const response = await fetch('/api/process', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ dest_dir: destDir, mappings: mappings })
            });
            
            if (!response.ok) {
                const err = await response.json();
                let errMsg = "Erro no servidor ao processar lote.";
                if (err && err.detail) {
                    if (typeof err.detail === 'string') {
                        errMsg = err.detail;
                    } else if (Array.isArray(err.detail)) {
                        errMsg = err.detail.map(d => `${d.loc ? d.loc.join('.') + ': ' : ''}${d.msg}`).join(' | ');
                    } else {
                        errMsg = JSON.stringify(err.detail);
                    }
                }
                throw new Error(errMsg);
            }
            
            const result = await response.json();
            processed += batch.length;
            
            const percent = Math.round((processed / total) * 100);
            progressFill.style.width = `${percent}%`;
            progressPercent.innerText = `${percent}%`;
            progressFraction.innerText = `${processed} / ${total}`;
            
            result.results.forEach(res => {
                const item = document.createElement('div');
                item.className = `log-item ${res.status}`;
                if (res.status === 'success') {
                    const basename = res.dest.split(/[\\/]/).pop();
                    const artistFolder = res.dest.split(/[\\/]/).slice(-3, -1).join(' / ');
                    item.innerHTML = `✓ Gravado: <span style="color: var(--accent-cyan);">${artistFolder} / ${basename}</span>`;
                } else {
                    item.innerText = `✗ Erro ao copiar ${res.source.split(/[\\/]/).pop()}: ${res.message}`;
                }
                logPanel.appendChild(item);
            });
            
            logPanel.scrollTop = logPanel.scrollHeight;
            await new Promise(resolve => setTimeout(resolve, 100));
            
        } catch (error) {
            const item = document.createElement('div');
            item.className = 'log-item error';
            item.innerText = `💥 Erro geral no lote: ${error.message}`;
            logPanel.appendChild(item);
            logPanel.scrollTop = logPanel.scrollHeight;
            break;
        }
    }
    
    // Generate Library Index
    const indexLog = document.createElement('div');
    indexLog.className = 'log-item info';
    indexLog.innerText = `📄 Gerando arquivo de relatório (library_index.txt)...`;
    logPanel.appendChild(indexLog);
    logPanel.scrollTop = logPanel.scrollHeight;
    
    try {
        const indexResponse = await fetch('/api/generate-index', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ dest_dir: destDir })
        });
        
        if (indexResponse.ok) {
            const indexData = await indexResponse.json();
            const successLog = document.createElement('div');
            successLog.className = 'log-item success';
            successLog.innerHTML = `✓ Relatório gerado com sucesso: <span style="color: var(--accent-cyan); font-weight: bold;">library_index.txt</span> (${indexData.summary.tracks} músicas organizadas).`;
            logPanel.appendChild(successLog);
        } else {
            throw new Error("Erro na resposta do servidor.");
        }
    } catch (e) {
        const errorLog = document.createElement('div');
        errorLog.className = 'log-item error';
        errorLog.innerText = `⚠ Falha ao gerar o arquivo de relatório: ${e.message}`;
        logPanel.appendChild(errorLog);
    }
    
    const summaryItem = document.createElement('div');
    summaryItem.className = 'log-item info';
    summaryItem.style.fontWeight = 'bold';
    summaryItem.style.marginTop = '10px';
    summaryItem.innerText = `🏁 Processamento Concluído! ${processed} de ${total} arquivos tratados e organizados com sucesso para DAPs.`;
    logPanel.appendChild(summaryItem);
    logPanel.scrollTop = logPanel.scrollHeight;
    
    modalSuccessActions.classList.remove('hidden');
}

// 11. Native Folder Browser Handlers
async function handleBrowseSource() {
    const path = await openNativeFolderPicker("Selecione a Pasta de Origem (Músicas Bagunçadas)");
    if (path) {
        sourceDirInput.value = path;
        // Automatically start directory scan
        scanDirectory();
    }
}

async function handleBrowseDest() {
    const path = await openNativeFolderPicker("Selecione a Pasta de Destino (Biblioteca Organizada)");
    if (path) {
        destDirInput.value = path;
        // If we already have files in the table, scan again to check what's already organized
        if (filesState.length > 0) {
            scanDirectory();
        }
    }
}

async function openNativeFolderPicker(title) {
    try {
        const response = await fetch('/api/browse-directory', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title: title })
        });
        const data = await response.json();
        if (data.success) {
            return data.path;
        } else {
            console.log("Folder browser closed or cancelled:", data.message);
            return null;
        }
    } catch (e) {
        console.error("Error opening folder picker:", e);
        return null;
    }
}

// 12. Drag & Drop Folder Resolution Handlers
const dragOverlay = document.getElementById('drag-drop-overlay');

window.addEventListener('dragenter', (e) => {
    e.preventDefault();
    dragOverlay.classList.remove('hidden');
    dragOverlay.classList.add('active');
});

// We need this dragover event to allow drop to fire
window.addEventListener('dragover', (e) => {
    e.preventDefault();
});

window.addEventListener('dragleave', (e) => {
    e.preventDefault();
    // Hide overlay when leaving screen
    if (e.clientX === 0 && e.clientY === 0) {
        dragOverlay.classList.remove('active');
        setTimeout(() => dragOverlay.classList.add('hidden'), 200);
    }
});

window.addEventListener('drop', async (e) => {
    e.preventDefault();
    dragOverlay.classList.remove('active');
    setTimeout(() => dragOverlay.classList.add('hidden'), 200);
    
    const items = e.dataTransfer.items;
    if (!items) return;
    
    const folderNames = [];
    for (let i = 0; i < items.length; i++) {
        // webkitGetAsEntry extracts directory entries recursively
        const entry = items[i].webkitGetAsEntry();
        if (entry && entry.isDirectory) {
            folderNames.push(entry.name);
        }
    }
    
    if (folderNames.length > 0) {
        for (const folderName of folderNames) {
            await resolveAndAddFolder(folderName);
        }
    }
});

async function resolveAndAddFolder(folderName) {
    try {
        const response = await fetch('/api/locate-folder', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ folder_name: folderName })
        });
        
        const data = await response.json();
        if (data.success && data.paths.length > 0) {
            if (data.paths.length === 1) {
                // Unique matching path found!
                const path = data.paths[0];
                if (filesState.length === 0) {
                    sourceDirInput.value = path;
                    localStorage.setItem('music_source_dir', path);
                }
                await scanAndMergeDirectory(path);
            } else {
                // Duplicate matches found, open resolver modal
                const chosenPath = await showPathResolverModal(folderName, data.paths);
                if (chosenPath) {
                    if (filesState.length === 0) {
                        sourceDirInput.value = chosenPath;
                        localStorage.setItem('music_source_dir', chosenPath);
                    }
                    await scanAndMergeDirectory(chosenPath);
                }
            }
        } else {
            alert(`Não encontramos a pasta "${folderName}" em seus diretórios comuns (Músicas, Downloads, Área de Trabalho ou Documentos). Vamos abrir o seletor nativo para você localizá-la.`);
            const path = await openNativeFolderPicker(`Selecione a pasta "${folderName}" no seu computador`);
            if (path) {
                if (filesState.length === 0) {
                    sourceDirInput.value = path;
                    localStorage.setItem('music_source_dir', path);
                }
                await scanAndMergeDirectory(path);
            }
        }
    } catch (e) {
        console.error("Error resolving dropped folder:", e);
    }
}

async function scanAndMergeDirectory(path) {
    if (!path) return;
    
    setBtnLoading(scanBtn, true);
    scanBtn.querySelector('.btn-text').innerText = "Varrendo...";
    
    try {
        const destDir = destDirInput.value.trim();
        const payload = { source_dir: path };
        if (destDir) payload.dest_dir = destDir;
        
        const response = await fetch('/api/scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || `Erro ao varrer pasta: ${path}`);
        }
        
        const data = await response.json();
        const newFiles = data.files || [];
        
        if (newFiles.length === 0) {
            alert(`Nenhum arquivo de áudio compatível (.mp3, .flac, .m4a) encontrado na pasta:\n${path}`);
            return;
        }
        
        // Merge into filesState, avoiding duplicate source files
        const existingSources = new Set(filesState.map(f => f.source));
        
        newFiles.forEach(nf => {
            if (!existingSources.has(nf.source)) {
                filesState.push(nf);
                // Check newly added files by default if they are not already organized
                if (!nf.already_organized) {
                    selectedSources.add(nf.source);
                }
            }
        });
        
        headerSelectAll.checked = Array.from(document.querySelectorAll('.row-select')).every(el => el.checked) && filesState.length > 0;
        
        // Show panels
        emptyState.classList.add('hidden');
        statsPanel.classList.remove('hidden');
        workspacePanel.classList.remove('hidden');
        
        updateStats();
        renderTable();
        
    } catch (e) {
        alert("Erro ao varrer diretório: " + e.message);
    } finally {
        setBtnLoading(scanBtn, false);
        scanBtn.querySelector('.btn-text').innerText = "Varrer e Analisar Músicas";
    }
}

// 13. Duplicate Path Resolver Modal Logics
const pathResolverModal = document.getElementById('path-resolver-modal');
const pathResolverOptions = document.getElementById('path-resolver-options');
const pathResolverCancel = document.getElementById('path-resolver-cancel');

let currentResolverResolve = null;

pathResolverCancel.addEventListener('click', () => {
    pathResolverModal.classList.add('hidden');
    if (currentResolverResolve) {
        currentResolverResolve(null);
        currentResolverResolve = null;
    }
});

function showPathResolverModal(folderName, paths) {
    pathResolverOptions.innerHTML = '';
    pathResolverModal.classList.remove('hidden');
    
    return new Promise((resolve) => {
        currentResolverResolve = resolve;
        
        paths.forEach(path => {
            const card = document.createElement('div');
            card.className = 'path-option-card';
            
            // Layout clean showing structure details
            const parts = path.split(/[\\/]/);
            const parentName = parts.slice(-3, -1).join(' / ');
            const baseName = parts[parts.length - 1];
            
            card.innerHTML = `
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="icon" style="color: var(--accent-purple); min-width: 16px;"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg>
                <div style="display: flex; flex-direction: column; text-align: left; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                    <span style="font-weight: 600; color: white;">${baseName}</span>
                    <span style="font-size: 11px; color: var(--text-secondary);">${path}</span>
                </div>
            `;
            
            card.addEventListener('click', () => {
                pathResolverModal.classList.add('hidden');
                currentResolverResolve = null;
                resolve(path);
            });
            
            pathResolverOptions.appendChild(card);
        });
    });
}
