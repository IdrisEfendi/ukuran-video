let currentFileId = null;
let currentTaskId = null;
let progressInterval = null;
let uploadedFileInfo = null;

const FORMATS = ['.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v'];
const TARGET_SIZES = [10, 20, 30, 50, 100, 200];
const SAVING_RATIOS = [25, 50, 75];

document.addEventListener('DOMContentLoaded', () => {
    initTheme();
    initDropZone();
    initFileInput();
    initTargetSize();
    initPresetSlider();
    initFormListeners();
    initCancelButton();
    initHistoryControls();
    loadHistory();
    initCleanup();
});

function initTheme() {
    const saved = localStorage.getItem('theme');
    if (saved === 'dark' || (!saved && window.matchMedia('(prefers-color-scheme: dark)').matches)) {
        document.documentElement.classList.add('dark');
    }
    document.getElementById('themeToggle').addEventListener('click', () => {
        document.documentElement.classList.toggle('dark');
        localStorage.setItem('theme', document.documentElement.classList.contains('dark') ? 'dark' : 'light');
    });
}

function initDropZone() {
    const dropZone = document.getElementById('dropZone');
    const fileInput = document.getElementById('fileInput');

    dropZone.addEventListener('click', () => fileInput.click());

    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('drag-over');
    });

    dropZone.addEventListener('dragleave', () => {
        dropZone.classList.remove('drag-over');
    });

    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('drag-over');
        const files = e.dataTransfer.files;
        if (files.length > 0) handleFile(files[0]);
    });
}

function initFileInput() {
    document.getElementById('fileInput').addEventListener('change', (e) => {
        if (e.target.files.length > 0) handleFile(e.target.files[0]);
    });
}

function validateFormat(filename) {
    const ext = '.' + filename.split('.').pop().toLowerCase();
    return FORMATS.includes(ext);
}

function formatSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + ' MB';
    return (bytes / 1073741824).toFixed(2) + ' GB';
}

function formatDuration(seconds) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    if (h > 0) return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
    return `${m}:${s.toString().padStart(2, '0')}`;
}

function formatBitrate(bps) {
    if (!bps || bps === 0) return 'N/A';
    return (bps / 1000).toFixed(0) + ' kbps';
}

function showElement(id) {
    document.getElementById(id).classList.remove('hidden');
}

function hideElement(id) {
    document.getElementById(id).classList.add('hidden');
}

function setStep(step) {
    document.querySelectorAll('.step').forEach((el, i) => {
        el.classList.remove('active', 'completed');
        if (i < step) el.classList.add('completed');
        else if (i === step) el.classList.add('active');
    });
}

async function handleFile(file) {
    if (!validateFormat(file.name)) {
        showToast('Format file tidak didukung. Gunakan: ' + FORMATS.join(', '), 'error');
        return;
    }

    if (file.size > 10 * 1024 * 1024 * 1024) {
        showToast('File terlalu besar. Maksimal 10 GB.', 'error');
        return;
    }

    setStep(0);
    showElement('uploadProgress');
    hideElement('fileInfo');
    hideElement('previewSection');
    hideElement('settingsPanel');
    hideElement('compressSection');

    document.getElementById('uploadProgressBar').style.width = '0%';
    document.getElementById('uploadPercent').textContent = '0%';
    document.getElementById('uploadFileName').textContent = file.name;

    const formData = new FormData();
    formData.append('file', file);

    try {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/api/upload');

        xhr.upload.onprogress = (e) => {
            if (e.lengthComputable) {
                const pct = Math.round((e.loaded / e.total) * 100);
                document.getElementById('uploadProgressBar').style.width = pct + '%';
                document.getElementById('uploadPercent').textContent = pct + '%';
            }
        };

        const result = await new Promise((resolve, reject) => {
            xhr.onload = () => {
                if (xhr.status === 200) resolve(JSON.parse(xhr.responseText));
                else reject(JSON.parse(xhr.responseText));
            };
            xhr.onerror = () => reject({ error: 'Network error' });
            xhr.send(formData);
        });

        document.getElementById('uploadProgressBar').style.width = '100%';
        document.getElementById('uploadPercent').textContent = '100%';

        setTimeout(() => {
            hideElement('uploadProgress');
            showFileInfo(result);
        }, 500);

    } catch (err) {
        hideElement('uploadProgress');
        showToast(err.error || 'Upload gagal', 'error');
    }
}

function showFileInfo(info) {
    currentFileId = info.file_id;
    uploadedFileInfo = info;

    document.getElementById('infoFilename').textContent = info.filename;
    document.getElementById('infoSize').textContent = formatSize(info.filesize);
    document.getElementById('infoResolution').textContent = `${info.width}x${info.height}`;
    document.getElementById('infoDuration').textContent = formatDuration(info.duration);
    document.getElementById('infoVideoCodec').textContent = info.video_codec.toUpperCase();
    document.getElementById('infoAudioCodec').textContent = info.audio_codec ? info.audio_codec.toUpperCase() : 'None';
    document.getElementById('infoFps').textContent = info.fps + ' fps';
    document.getElementById('infoBitrate').textContent = formatBitrate(info.bitrate || info.video_bitrate);

    const previewPlayer = document.getElementById('previewPlayer');
    previewPlayer.src = `/api/stream/${currentFileId}`;
    previewPlayer.load();

    showElement('fileInfo');
    showElement('previewSection');
    showElement('settingsPanel');
    showElement('compressSection');
    setStep(1);

    applyTargetSizeRecommendations(info.filesize);

    document.getElementById('fileInfo').scrollIntoView({ behavior: 'smooth', block: 'center' });

    updateEstimate();
}

function initTargetSize() {
    const container = document.getElementById('targetSizeOptions');

    const recommendedLabel = document.createElement('label');
    recommendedLabel.className = 'flex items-center px-4 py-2.5 border border-slate-200 dark:border-slate-600 rounded-xl cursor-pointer hover:border-blue-400 dark:hover:border-blue-500 transition-all has-[:checked]:border-blue-500 has-[:checked]:bg-blue-50 dark:has-[:checked]:bg-blue-900/20 has-[:checked]:ring-1 has-[:checked]:ring-blue-500';
    recommendedLabel.innerHTML = `
        <input type="radio" name="targetSize" value="recommended" class="mr-3" onchange="onTargetSizeChange()">
        <div>
            <span class="text-sm font-medium text-slate-700 dark:text-slate-300">Rekomendasi</span>
            <p id="recommendedSizeLabel" class="text-xs text-slate-400 mt-0.5">Ukuran asli, kualitas tetap</p>
        </div>
    `;
    container.appendChild(recommendedLabel);

    SAVING_RATIOS.forEach(ratio => {
        const savingLabel = document.createElement('label');
        savingLabel.className = 'flex items-center px-4 py-2.5 border border-slate-200 dark:border-slate-600 rounded-xl cursor-pointer hover:border-blue-400 dark:hover:border-blue-500 transition-all has-[:checked]:border-blue-500 has-[:checked]:bg-blue-50 dark:has-[:checked]:bg-blue-900/20 has-[:checked]:ring-1 has-[:checked]:ring-blue-500';
        savingLabel.innerHTML = `
            <input type="radio" name="targetSize" value="saving${ratio}" class="mr-3" onchange="onTargetSizeChange()">
            <div>
                <span class="text-sm font-medium text-slate-700 dark:text-slate-300">Hemat ${ratio}%</span>
                <p id="savingSizeLabel${ratio}" class="text-xs text-slate-400 mt-0.5">${ratio}% dari ukuran asli</p>
            </div>
        `;
        container.appendChild(savingLabel);
    });

    TARGET_SIZES.forEach(size => {
        const label = document.createElement('label');
        label.className = 'flex items-center px-4 py-2.5 border border-slate-200 dark:border-slate-600 rounded-xl cursor-pointer hover:border-blue-400 dark:hover:border-blue-500 transition-all has-[:checked]:border-blue-500 has-[:checked]:bg-blue-50 dark:has-[:checked]:bg-blue-900/20 has-[:checked]:ring-1 has-[:checked]:ring-blue-500';
        label.innerHTML = `
            <input type="radio" name="targetSize" value="${size}" class="mr-3" onchange="onTargetSizeChange()">
            <span class="text-sm font-medium text-slate-700 dark:text-slate-300">${size} MB</span>
        `;
        container.appendChild(label);
    });

    const customLabel = document.createElement('label');
    customLabel.className = 'flex items-center px-4 py-2.5 border border-slate-200 dark:border-slate-600 rounded-xl cursor-pointer hover:border-blue-400 dark:hover:border-blue-500 transition-all has-[:checked]:border-blue-500 has-[:checked]:bg-blue-50 dark:has-[:checked]:bg-blue-900/20 has-[:checked]:ring-1 has-[:checked]:ring-blue-500';
    customLabel.innerHTML = `
        <input type="radio" name="targetSize" value="custom" class="mr-3" onchange="onTargetSizeChange()">
        <span class="text-sm font-medium text-slate-700 dark:text-slate-300">Custom</span>
        <div class="ml-3 flex items-center">
            <input type="number" id="customSizeInput" value="75" min="1" max="10000"
                class="w-20 px-2 py-1 text-sm border border-slate-300 dark:border-slate-600 rounded-lg bg-white dark:bg-slate-700 text-slate-900 dark:text-slate-100 text-center"
                onchange="onTargetSizeChange()" onclick="document.querySelector('input[name=targetSize][value=custom]').checked=true; onTargetSizeChange();">
            <span class="ml-1 text-xs text-slate-500">MB</span>
        </div>
    `;
    container.appendChild(customLabel);

    document.querySelector('input[name="targetSize"][value="50"]').checked = true;
}

function onTargetSizeChange() {
    updateEstimate();
}

function applyTargetSizeRecommendations(filesize) {
    const recommendedInput = document.querySelector('input[name="targetSize"][value="recommended"]');
    const recommendedLabel = document.getElementById('recommendedSizeLabel');
    if (!recommendedInput) return;

    const sizeMb = Math.max(1, Math.round((filesize / 1048576) * 10) / 10);
    recommendedInput.dataset.sizeMb = sizeMb;
    if (recommendedLabel) recommendedLabel.textContent = `${sizeMb} MB · seperti asli`;
    recommendedInput.checked = true;

    SAVING_RATIOS.forEach(ratio => {
        const input = document.querySelector(`input[name="targetSize"][value="saving${ratio}"]`);
        if (!input) return;

        const savingMb = Math.max(1, Math.round(sizeMb * (ratio / 100) * 10) / 10);
        input.dataset.sizeMb = savingMb;
        const label = document.getElementById(`savingSizeLabel${ratio}`);
        if (label) label.textContent = `${savingMb} MB · ${ratio}% dari asli`;
    });
}

function getTargetSizeMB() {
    const selected = document.querySelector('input[name="targetSize"]:checked');
    if (!selected) return null;
    if (selected.value === 'custom') {
        return parseInt(document.getElementById('customSizeInput').value) || null;
    }
    if (selected.value === 'recommended' || selected.value.startsWith('saving')) {
        return parseFloat(selected.dataset.sizeMb) || null;
    }
    return parseInt(selected.value);
}

function initPresetSlider() {
    const slider = document.getElementById('presetSlider');
    const label = document.getElementById('presetLabel');
    const values = ['ultrafast', 'fast', 'medium', 'slow', 'veryslow'];
    const labels = ['Ultra Fast', 'Fast', 'Medium', 'Slow', 'Very Slow'];

    slider.addEventListener('input', () => {
        const idx = parseInt(slider.value);
        label.textContent = labels[idx];
    });
}

function initFormListeners() {
    document.querySelectorAll('input[name="codec"], input[name="audio"], input[name="mode"]').forEach(el => {
        el.addEventListener('change', () => {
            updateEstimate();
            toggleModeSettings();
        });
    });
    document.getElementById('crfValue').addEventListener('input', () => {
        document.getElementById('crfDisplay').textContent = document.getElementById('crfValue').value;
        updateEstimate();
    });
    document.getElementById('bitrateInput').addEventListener('input', updateEstimate);
    toggleModeSettings();
}

function toggleModeSettings() {
    const mode = document.querySelector('input[name="mode"]:checked')?.value;
    document.getElementById('crfSettings').classList.toggle('hidden', mode !== 'crf');
    document.getElementById('bitrateSettings').classList.toggle('hidden', mode !== 'bitrate');
}

async function updateEstimate() {
    if (!currentFileId) return;

    const targetSize = getTargetSizeMB();
    if (!targetSize) return;

    const codec = document.querySelector('input[name="codec"]:checked')?.value || 'h264';
    const audio = document.querySelector('input[name="audio"]:checked')?.value || 'keep';

    hideElement('estimateInfo');
    hideElement('warningAlert');

    try {
        const resp = await fetch('/api/estimate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_id: currentFileId, target_size_mb: targetSize, codec, audio_mode: audio })
        });
        const data = await resp.json();

        if (data.target_bitrate) {
            document.getElementById('estTargetSize').textContent = targetSize + ' MB';
            document.getElementById('estBitrate').textContent = data.target_bitrate_formatted;
            showElement('estimateInfo');
        }

        if (data.warning) {
            document.getElementById('warningText').textContent = data.warning.message;
            document.getElementById('recommendedSize').textContent = data.warning.recommended_size + ' MB';
            showElement('warningAlert');
        }
    } catch (err) {
        console.error('Estimate error:', err);
    }
}

async function startCompression() {
    if (!currentFileId) {
        showToast('Silakan upload video terlebih dahulu', 'error');
        return;
    }

    const targetSize = getTargetSizeMB();
    if (!targetSize) {
        showToast('Pilih target ukuran file', 'error');
        return;
    }

    if (currentTaskId) {
        showToast('Proses kompresi sedang berjalan', 'error');
        return;
    }

    const codec = document.querySelector('input[name="codec"]:checked')?.value || 'h264';
    const audio = document.querySelector('input[name="audio"]:checked')?.value || 'keep';
    const mode = document.querySelector('input[name="mode"]:checked')?.value || 'smart';
    const presetIdx = document.getElementById('presetSlider').value;
    const preset = ['ultrafast', 'fast', 'medium', 'slow', 'veryslow'][presetIdx];
    const crf = parseInt(document.getElementById('crfValue').value);
    const bitrate = document.getElementById('bitrateInput').value || null;

    const payload = {
        file_id: currentFileId,
        target_size_mb: targetSize,
        codec,
        preset,
        mode,
        audio_mode: audio,
        crf,
        custom_bitrate: bitrate ? bitrate + 'k' : null,
    };

    if (mode === 'bitrate' && !bitrate) {
        showToast('Masukkan bitrate target', 'error');
        return;
    }

    hideElement('compressSection');
    hideElement('comparisonSection');
    showElement('progressSection');
    hideElement('resultSection');

    setStep(2);
    document.getElementById('progressSection').scrollIntoView({ behavior: 'smooth', block: 'center' });

    document.getElementById('progressBar').style.width = '0%';
    document.getElementById('progressPercent').textContent = '0%';
    document.getElementById('progressSpeed').textContent = '--';
    document.getElementById('progressFps').textContent = '--';
    document.getElementById('progressEta').textContent = '--';
    document.getElementById('progressLog').innerHTML = '';
    document.getElementById('progressStage').textContent = 'Menganalisis video...';
    document.getElementById('cancelBtn').disabled = false;

    try {
        const resp = await fetch('/api/compress', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await resp.json();
        if (!resp.ok) {
            showToast(data.error || 'Gagal memulai kompresi', 'error');
            hideElement('progressSection');
            showElement('compressSection');
            setStep(1);
            return;
        }
        currentTaskId = data.task_id;
        startProgressPolling(data.task_id);
    } catch (err) {
        showToast('Gagal memulai kompresi', 'error');
        hideElement('progressSection');
        showElement('compressSection');
    }
}

function startProgressPolling(taskId) {
    if (progressInterval) clearInterval(progressInterval);

    progressInterval = setInterval(async () => {
        try {
            const resp = await fetch(`/api/progress/${taskId}`);
            const data = await resp.json();
            updateProgress(data);

            if (data.status === 'completed') {
                clearInterval(progressInterval);
                progressInterval = null;
                const completedTaskId = currentTaskId || data.task_id;
                currentTaskId = null;
                onCompressionComplete(data, completedTaskId);
            } else if (data.status === 'error') {
                clearInterval(progressInterval);
                progressInterval = null;
                currentTaskId = null;
                showToast(data.error || 'Kompresi gagal', 'error');
                hideElement('progressSection');
                showElement('compressSection');
                setStep(1);
            } else if (data.status === 'cancelled') {
                clearInterval(progressInterval);
                progressInterval = null;
                currentTaskId = null;
                hideElement('progressSection');
                showElement('compressSection');
                setStep(1);
            }
        } catch (err) {
            console.error('Progress poll error:', err);
        }
    }, 500);
}

function updateProgress(data) {
    const pct = Math.round(data.progress);
    document.getElementById('progressBar').style.width = pct + '%';
    document.getElementById('progressPercent').textContent = pct + '%';

    if (data.pass_number === 1) {
        document.getElementById('progressStage').textContent = `Pass 1/2: Menganalisis video... (${pct}%)`;
    } else if (data.pass_number === 2) {
        document.getElementById('progressStage').textContent = `Pass 2/2: Mengompres video... (${pct}%)`;
    }

    document.getElementById('progressSpeed').textContent = data.speed || '--';
    document.getElementById('progressFps').textContent = data.fps ? data.fps + ' fps' : '--';
    document.getElementById('progressEta').textContent = data.eta || '--';

    if (data.ffmpeg_log) {
        const logEl = document.getElementById('progressLog');
        const lastLines = data.ffmpeg_log.slice(-20);
        logEl.innerHTML = lastLines.map(l => `<div class="text-green-400">${escapeHtml(l)}</div>`).join('');
        logEl.scrollTop = logEl.scrollHeight;
    }

    document.getElementById('progressPct').textContent = pct;
}

function onCompressionComplete(data, completedTaskId) {
    const result = data.result;
    if (!result) return;

    document.getElementById('progressBar').style.width = '100%';
    document.getElementById('progressPercent').textContent = '100%';
    document.getElementById('progressStage').textContent = 'Selesai!';
    document.getElementById('cancelBtn').disabled = true;

    setTimeout(() => {
        hideElement('progressSection');
        showElement('resultSection');

        document.getElementById('origSize').textContent = result.original_size_formatted;
        document.getElementById('finalSize').textContent = result.final_size_formatted;
        document.getElementById('resultResolution').textContent = result.resolution;
        document.getElementById('resultDuration').textContent = result.duration;
        document.getElementById('resultCodec').textContent = result.codec.toUpperCase();

        const savingsRing = document.getElementById('savingsRing');
        const displayPct = Math.max(0, Math.min(result.savings_percent, 100));
        savingsRing.style.background = `conic-gradient(#10b981 ${displayPct * 3.6}deg, #e2e8f0 ${displayPct * 3.6}deg)`;

        document.getElementById('savingsRingValue').textContent = displayPct + '%';
        document.getElementById('savingsRingLabel').textContent = displayPct > 0 ? 'Hemat' : 'Tidak ada perubahan';

        document.getElementById('downloadBtn').onclick = () => {
            window.location.href = `/api/download/${completedTaskId}`;
        };

        showComparison(result, completedTaskId);

        setStep(3);
        document.getElementById('resultSection').scrollIntoView({ behavior: 'smooth', block: 'center' });

        loadHistory();
        setupNewCompression();
    }, 800);
}

function showComparison(result, taskIdForCompare) {
    const origFileId = result.original_file_id;
    if (!origFileId || !taskIdForCompare) return;

    showElement('comparisonSection');

    const origPlayer = document.getElementById('compareOriginalPlayer');
    origPlayer.src = `/api/stream/${origFileId}`;
    origPlayer.load();

    const resultPlayer = document.getElementById('compareResultPlayer');
    resultPlayer.src = `/api/stream_result/${taskIdForCompare}`;
    resultPlayer.load();

    const origSize = result.original_size || 0;
    const finalSize = result.final_size || 0;
    const sizeDiff = origSize - finalSize;
    const sizeDiffPct = origSize > 0 ? ((sizeDiff / origSize) * 100).toFixed(1) : 0;

    const origBitrate = result.original_bitrate || 0;
    const finalBitrate = result.bitrate || 0;
    const bitrateDiff = origBitrate - finalBitrate;

    const rows = [
        {
            label: 'Ukuran File',
            orig: result.original_size_formatted,
            final: result.final_size_formatted,
            diff: `${formatSize(sizeDiff)} (${sizeDiffPct}%)`,
            positive: sizeDiff > 0,
        },
        {
            label: 'Resolusi',
            orig: `${uploadedFileInfo ? uploadedFileInfo.width + 'x' + uploadedFileInfo.height : result.original_resolution || '-'}`,
            final: result.resolution,
            diff: '--',
        },
        {
            label: 'Bitrate Video',
            orig: result.original_bitrate_formatted || formatBitrate(origBitrate),
            final: result.bitrate_formatted || formatBitrate(finalBitrate),
            diff: bitrateDiff > 0 ? `-${formatBitrate(bitrateDiff)}` : formatBitrate(Math.abs(bitrateDiff)),
            positive: bitrateDiff > 0,
        },
        {
            label: 'Codec Video',
            orig: uploadedFileInfo ? uploadedFileInfo.video_codec.toUpperCase() : '--',
            final: result.codec.toUpperCase(),
            diff: '--',
        },
        {
            label: 'Durasi',
            orig: result.original_duration ? formatDuration(result.original_duration) : (uploadedFileInfo ? formatDuration(uploadedFileInfo.duration) : '--'),
            final: result.duration,
            diff: '--',
        },
    ];

    const tbody = document.getElementById('comparisonBody');
    tbody.innerHTML = '';

    rows.forEach((row, idx) => {
        const tr = document.createElement('tr');
        tr.className = idx < rows.length - 1 ? 'border-b border-slate-100 dark:border-slate-700' : '';

        let diffHtml = `<span class="text-slate-500">${escapeHtml(row.diff)}</span>`;
        if (row.positive !== undefined && row.diff !== '--') {
            if (row.positive) {
                diffHtml = `<span class="text-green-500 font-medium">${escapeHtml(row.diff)}</span>`;
            } else {
                diffHtml = `<span class="text-red-500 font-medium">${escapeHtml(row.diff)}</span>`;
            }
        }

        tr.innerHTML = `
            <td class="px-4 py-3 text-sm font-medium text-slate-700 dark:text-slate-300">${escapeHtml(row.label)}</td>
            <td class="px-4 py-3 text-sm text-blue-600 dark:text-blue-400">${escapeHtml(row.orig)}</td>
            <td class="px-4 py-3 text-sm text-green-600 dark:text-green-400">${escapeHtml(row.final)}</td>
            <td class="px-4 py-3 text-sm">${diffHtml}</td>
        `;
        tbody.appendChild(tr);
    });

    try {
        document.getElementById('compareOriginalPlayer').play();
    } catch (e) {}
    try {
        document.getElementById('compareResultPlayer').play();
    } catch (e) {}
}

function setupNewCompression() {
    document.getElementById('newCompressionBtn').onclick = () => {
        currentFileId = null;
        uploadedFileInfo = null;
        const previewPlayer = document.getElementById('previewPlayer');
        previewPlayer.pause();
        previewPlayer.removeAttribute('src');
        previewPlayer.load();

        const origPlayer = document.getElementById('compareOriginalPlayer');
        origPlayer.pause();
        origPlayer.removeAttribute('src');
        origPlayer.load();

        const resultPlayer = document.getElementById('compareResultPlayer');
        resultPlayer.pause();
        resultPlayer.removeAttribute('src');
        resultPlayer.load();

        hideElement('fileInfo');
        hideElement('previewSection');
        hideElement('settingsPanel');
        hideElement('compressSection');
        hideElement('resultSection');
        hideElement('comparisonSection');
        hideElement('estimateInfo');
        hideElement('warningAlert');
        document.getElementById('fileInput').value = '';
        setStep(0);
        window.scrollTo({ top: 0, behavior: 'smooth' });
    };
}

function initCancelButton() {
    document.getElementById('cancelBtn').addEventListener('click', async () => {
        if (!currentTaskId) return;
        try {
            await fetch(`/api/cancel/${currentTaskId}`, { method: 'POST' });
            showToast('Kompresi dibatalkan', 'info');
        } catch (err) {
            console.error(err);
        }
    });
}

function initCleanup() {
    fetch('/api/cleanup', { method: 'POST' }).catch(() => {});
}

function confirmModal({ title, message, confirmText = 'Ya', cancelText = 'Batal', checkboxLabel = '', checkboxDefault = false } = {}) {
    const modal = document.getElementById('confirmModal');
    if (!modal) return Promise.resolve({ confirmed: window.confirm(message || title || ''), checked: false });

    const okBtn = document.getElementById('confirmModalOk');
    const cancelBtn = document.getElementById('confirmModalCancel');
    const optionRow = document.getElementById('confirmModalOption');
    const checkbox = document.getElementById('confirmModalCheckbox');

    document.getElementById('confirmModalTitle').textContent = title || 'Konfirmasi';
    document.getElementById('confirmModalMessage').textContent = message || '';
    okBtn.textContent = confirmText;
    cancelBtn.textContent = cancelText;

    if (optionRow && checkbox) {
        document.getElementById('confirmModalCheckboxLabel').textContent = checkboxLabel;
        checkbox.checked = checkboxDefault;
        optionRow.classList.toggle('hidden', !checkboxLabel);
    }

    showElement('confirmModal');
    okBtn.focus();

    return new Promise(resolve => {
        function close(confirmed) {
            hideElement('confirmModal');
            okBtn.removeEventListener('click', onOk);
            cancelBtn.removeEventListener('click', onCancel);
            modal.removeEventListener('click', onBackdrop);
            document.removeEventListener('keydown', onKey);
            resolve({ confirmed, checked: !!(checkbox && checkboxLabel && checkbox.checked) });
        }

        function onOk() { close(true); }
        function onCancel() { close(false); }
        function onBackdrop(event) { if (event.target === modal) close(false); }
        function onKey(event) { if (event.key === 'Escape') close(false); }

        okBtn.addEventListener('click', onOk);
        cancelBtn.addEventListener('click', onCancel);
        modal.addEventListener('click', onBackdrop);
        document.addEventListener('keydown', onKey);
    });
}

function initHistoryControls() {
    const btn = document.getElementById('clearHistoryBtn');
    if (!btn) return;

    btn.addEventListener('click', async () => {
        const { confirmed, checked } = await confirmModal({
            title: 'Hapus riwayat kompresi?',
            message: 'Semua riwayat kompresi akan dihapus dan tidak bisa dikembalikan.',
            confirmText: 'Hapus',
            checkboxLabel: 'Sekaligus hapus semua file video asli dan hasil kompresi',
        });
        if (!confirmed) return;

        btn.disabled = true;
        try {
            const url = '/api/history' + (checked ? '?delete_files=1' : '');
            const resp = await fetch(url, { method: 'DELETE' });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) {
                showToast(data.error || 'Gagal menghapus riwayat', 'error');
                return;
            }
            const deletedFiles = Array.isArray(data.deleted_files) ? data.deleted_files.length : 0;
            showToast(
                deletedFiles > 0 ? `Riwayat dan ${deletedFiles} file dihapus` : 'Riwayat kompresi dihapus',
                'success'
            );
            await loadHistory();
        } catch (err) {
            console.error('Clear history error:', err);
            showToast('Gagal menghapus riwayat', 'error');
        } finally {
            btn.disabled = false;
        }
    });
}

async function deleteHistoryEntry(entryId, filename) {
    const { confirmed, checked } = await confirmModal({
        title: 'Hapus entri riwayat?',
        message: `Riwayat kompresi "${filename}" akan dihapus dan tidak bisa dikembalikan.`,
        confirmText: 'Hapus',
        checkboxLabel: 'Sekaligus hapus file video asli dan hasil kompresi',
    });
    if (!confirmed) return;

    try {
        const url = `/api/history/${entryId}` + (checked ? '?delete_files=1' : '');
        const resp = await fetch(url, { method: 'DELETE' });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            showToast(data.error || 'Gagal menghapus riwayat', 'error');
            return;
        }
        const deletedFiles = Array.isArray(data.deleted_files) ? data.deleted_files.length : 0;
        showToast(
            deletedFiles > 0 ? `Riwayat dan ${deletedFiles} file dihapus` : 'Riwayat dihapus',
            'success'
        );
        await loadHistory();
    } catch (err) {
        console.error('Delete history error:', err);
        showToast('Gagal menghapus riwayat', 'error');
    }
}

async function loadHistory() {
    try {
        const resp = await fetch('/api/history');
        const history = await resp.json();
        const tbody = document.getElementById('historyBody');
        tbody.innerHTML = '';

        const recent = history.filter(h => h.status === 'completed').slice(0, 10);
        const clearBtn = document.getElementById('clearHistoryBtn');
        if (clearBtn) clearBtn.classList.toggle('hidden', history.length === 0);

        if (recent.length === 0) {
            showElement('historyEmpty');
            hideElement('historyTable');
            return;
        }

        hideElement('historyEmpty');
        showElement('historyTable');

        recent.forEach(entry => {
            const tr = document.createElement('tr');
            tr.className = 'border-b border-slate-100 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700/50';
            tr.innerHTML = `
                <td class="px-4 py-3 text-sm text-slate-700 dark:text-slate-300">${escapeHtml(entry.filename)}</td>
                <td class="px-4 py-3 text-sm text-slate-500 dark:text-slate-400">${new Date(entry.timestamp).toLocaleString('id-ID')}</td>
                <td class="px-4 py-3 text-sm text-slate-500 dark:text-slate-400">${entry.original_size_formatted}</td>
                <td class="px-4 py-3 text-sm text-slate-500 dark:text-slate-400">${entry.final_size_formatted || '-'}</td>
                <td class="px-4 py-3 text-sm">
                    ${entry.savings_percent ? `<span class="px-2 py-1 text-xs font-semibold rounded-full bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400">-${entry.savings_percent}%</span>` : '-'}
                </td>
                <td class="px-4 py-3 text-sm">
                    ${entry.status === 'completed'
                        ? '<span class="text-green-500 flex items-center"><svg class="w-4 h-4 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>Selesai</span>'
                        : '<span class="text-yellow-500 flex items-center"><svg class="w-4 h-4 mr-1 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path></svg>Proses</span>'
                    }
                </td>
                <td class="px-4 py-3 text-right">
                    <button type="button" class="delete-history-row inline-flex items-center justify-center w-8 h-8 rounded-lg text-slate-400 hover:text-red-600 hover:bg-red-50 transition-colors dark:hover:bg-red-900/20" title="Hapus riwayat ini" aria-label="Hapus riwayat ini">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
                        </svg>
                    </button>
                </td>
            `;
            tbody.appendChild(tr);

            const deleteBtn = tr.querySelector('.delete-history-row');
            deleteBtn.addEventListener('click', () => deleteHistoryEntry(entry.id, entry.filename));
        });
    } catch (err) {
        console.error('History load error:', err);
    }
}

function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    const colors = {
        error: 'bg-red-500',
        success: 'bg-green-500',
        info: 'bg-blue-500',
        warning: 'bg-yellow-500',
    };

    const toast = document.createElement('div');
    toast.className = `${colors[type]} text-white px-6 py-3 rounded-xl shadow-lg flex items-center gap-3 transform translate-x-full opacity-0 transition-all duration-300`;
    toast.innerHTML = `
        <span class="text-sm font-medium">${escapeHtml(message)}</span>
        <button onclick="this.parentElement.remove()" class="ml-2 text-white/80 hover:text-white">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
            </svg>
        </button>
    `;

    container.appendChild(toast);

    requestAnimationFrame(() => {
        toast.classList.remove('translate-x-full', 'opacity-0');
    });

    setTimeout(() => {
        toast.classList.add('translate-x-full', 'opacity-0');
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
