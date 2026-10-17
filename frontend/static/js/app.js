// Wvids Panel Main Frontend Logic

let chartInstance = null;
let selectedFile = null;

document.addEventListener("DOMContentLoaded", () => {
    initTabs();
    initUploadDropzone();
    initEventListeners();
    loadDashboard();
    loadAccounts();
    loadRecentUploads();
    loadLogs();
    loadTorConfig();
    loadTorStatus();
    loadBatchSettings();
    loadBatchFiles();
    loadBatchStatus();
    loadBulkRedactStatus();
    // Periodic refresh
    setInterval(loadTorStatus, 30000);
    setInterval(loadBatchStatus, 2500);
    setInterval(loadBulkRedactStatus, 2000);
});

// --- Toast Notifications ---
function showToast(message, type = "info") {
    const container = document.getElementById("toastContainer");
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = "0";
        toast.style.transform = "translateX(100%)";
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// --- Tabs Management ---
function initTabs() {
    const tabBtns = document.querySelectorAll(".tab-btn");
    const tabContents = document.querySelectorAll(".tab-content");

    tabBtns.forEach(btn => {
        btn.addEventListener("click", () => {
            const target = btn.dataset.tab;
            tabBtns.forEach(b => b.classList.remove("active"));
            tabContents.forEach(c => c.classList.remove("active"));

            btn.classList.add("active");
            const panel = document.getElementById(target);
            if (panel) {
                panel.classList.add("active");
                if (target === "tab-dashboard" && chartInstance) {
                    chartInstance.resize();
                }
                if (target === "tab-logs") {
                    loadLogs();
                }
                if (target === "tab-antidetect") {
                    loadTorStatus();
                }
                if (target === "tab-upload") {
                    loadBatchFiles();
                    loadBatchStatus();
                }
                if (target === "tab-redact") {
                    loadBulkRedactStatus();
                }
            }
        });
    });
}

// --- Dashboard & Statistics ---
async function loadDashboard() {
    try {
        const res = await fetch("/api/statistics");
        const data = await res.json();

        if (data.success && data.account) {
            updateHeaderStatus(data.account);

            const stats = data.stats || {};
            document.getElementById("statSubscribers").textContent = (stats.subscribers || 0).toLocaleString();
            document.getElementById("statViewsToday").textContent = (stats.views_today || 0).toLocaleString();
            document.getElementById("statTotalViews").textContent = (stats.total_views || 0).toLocaleString();

            renderViewsChart(stats.chart_labels || [], stats.chart_data || []);
        } else {
            updateHeaderStatus(null);
            document.getElementById("chartStatusText").textContent = data.error || "Добавьте аккаунт во вкладке 'Куки & Аккаунты'";
            renderViewsChart([], []);
        }
    } catch (err) {
        console.error("Dashboard load error:", err);
    }
}

function updateHeaderStatus(account) {
    const dot = document.getElementById("headerStatusDot");
    const userEl = document.getElementById("headerUsername");
    const uidEl = document.getElementById("headerUid");
    const curNick = document.getElementById("currentNicknameInput");
    const profLink = document.getElementById("profileChannelLink");
    const profUid = document.getElementById("profileUidText");

    if (account && account.username) {
        dot.classList.add("active");
        userEl.textContent = `@${account.username}`;
        uidEl.textContent = `(UID: ${account.uid})`;
        if (curNick) curNick.value = account.username;
        if (profLink) {
            profLink.href = `https://wvids.com/@${account.username}`;
            profLink.textContent = `https://wvids.com/@${account.username}`;
        }
        if (profUid) profUid.textContent = account.uid || "-";
    } else {
        dot.classList.remove("active");
        userEl.textContent = "Не авторизован";
        uidEl.textContent = "";
        if (curNick) curNick.value = "";
        if (profLink) {
            profLink.href = "https://wvids.com/";
            profLink.textContent = "https://wvids.com/";
        }
        if (profUid) profUid.textContent = "-";
    }
}

function renderViewsChart(labels, series) {
    const ctx = document.getElementById("viewsChartCanvas");
    if (!ctx) return;

    if (chartInstance) {
        chartInstance.destroy();
    }

    const hasData = series && series.length > 0;
    const finalLabels = hasData ? labels : ["Нет данных"];
    const finalSeries = hasData ? series : [0];

    chartInstance = new Chart(ctx, {
        type: "line",
        data: {
            labels: finalLabels,
            datasets: [{
                label: "Просмотры",
                data: finalSeries,
                borderColor: "#06b6d4",
                borderWidth: 3,
                tension: 0.35,
                fill: true,
                backgroundColor: "rgba(6, 182, 212, 0.12)",
                pointRadius: 4,
                pointBackgroundColor: "#06b6d4",
                pointHoverRadius: 6,
                pointHoverBackgroundColor: "#ffffff"
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: "#111726",
                    titleColor: "#94a3b8",
                    bodyColor: "#ffffff",
                    borderColor: "rgba(6, 182, 212, 0.4)",
                    borderWidth: 1,
                    padding: 10,
                    displayColors: false
                }
            },
            scales: {
                x: {
                    grid: { color: "rgba(255, 255, 255, 0.05)" },
                    ticks: { color: "#64748b", font: { size: 11 } }
                },
                y: {
                    grid: { color: "rgba(255, 255, 255, 0.05)" },
                    ticks: { color: "#64748b", font: { size: 11 } },
                    beginAtZero: true
                }
            }
        }
    });
}

// --- Upload Pipeline ---
function initUploadDropzone() {
    const dropzone = document.getElementById("uploadDropzone");
    const fileInput = document.getElementById("browserFileInput");

    if (!dropzone || !fileInput) return;

    dropzone.addEventListener("click", () => fileInput.click());

    fileInput.addEventListener("change", (e) => {
        if (e.target.files && e.target.files[0]) {
            handleSelectedFile(e.target.files[0]);
        }
    });

    dropzone.addEventListener("dragover", (e) => {
        e.preventDefault();
        dropzone.classList.add("dragover");
    });

    dropzone.addEventListener("dragleave", () => {
        dropzone.classList.remove("dragover");
    });

    dropzone.addEventListener("drop", (e) => {
        e.preventDefault();
        dropzone.classList.remove("dragover");
        if (e.dataTransfer.files && e.dataTransfer.files[0]) {
            handleSelectedFile(e.dataTransfer.files[0]);
        }
    });
}

function handleSelectedFile(file) {
    selectedFile = file;
    const dropzoneText = document.querySelector("#uploadDropzone .dropzone-text");
    const dropzoneSub = document.querySelector("#uploadDropzone .dropzone-sub");
    const titleInput = document.getElementById("uploadTitleInput");

    const sizeMb = (file.size / (1024 * 1024)).toFixed(1);
    dropzoneText.textContent = `Выбран: ${file.name} (${sizeMb} МБ)`;
    dropzoneSub.textContent = `Тип: ${file.type || 'Медиа'}. Нажмите, чтобы выбрать другой файл.`;

    if (titleInput && !titleInput.value) {
        titleInput.value = file.name.replace(/\.[^/.]+$/, "");
    }
}

// --- Event Listeners ---
function initEventListeners() {
    // Refresh Stats
    document.getElementById("btnRefreshStats")?.addEventListener("click", () => {
        showToast("Обновление статистики...", "info");
        loadDashboard();
        loadRecentUploads();
    });

    // Start Upload
    document.getElementById("btnStartUpload")?.addEventListener("click", async () => {
        const localPath = document.getElementById("localFilePathInput").value.trim();
        const title = document.getElementById("uploadTitleInput").value.trim();
        const desc = document.getElementById("uploadDescInput").value.trim();

        if (!selectedFile && !localPath) {
            showToast("Выберите файл через окно загрузки или укажите локальный путь к файлу", "error");
            return;
        }

        const progContainer = document.getElementById("uploadProgressContainer");
        const progFill = document.getElementById("uploadProgressFill");
        const progText = document.getElementById("uploadPercentText");
        const progStatus = document.getElementById("uploadStatusText");
        const btnUpload = document.getElementById("btnStartUpload");

        progContainer.style.display = "block";
        btnUpload.disabled = true;
        btnUpload.style.opacity = "0.6";

        progFill.style.width = "20%";
        progText.textContent = "20%";
        progStatus.textContent = "Инициализация анонимного TLS/HTTP2 соединения...";

        try {
            let res;
            if (localPath) {
                // Direct PC Path upload
                progFill.style.width = "40%";
                progText.textContent = "40%";
                progStatus.textContent = "Отправка файла на сервер wvids.com...";

                res = await fetch("/api/upload-local-path", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        file_path: localPath,
                        title: title,
                        description: desc
                    })
                });
            } else {
                // Browser file multipart upload
                progFill.style.width = "40%";
                progText.textContent = "40%";
                progStatus.textContent = "Передача файла с ПК на сервер...";

                const formData = new FormData();
                formData.append("file", selectedFile);
                formData.append("title", title);
                formData.append("description", desc);

                res = await fetch("/api/upload", {
                    method: "POST",
                    body: formData
                });
            }

            progFill.style.width = "80%";
            progText.textContent = "80%";
            progStatus.textContent = "Применение метаданных и публикация...";

            const data = await res.json();
            if (res.ok && data.success) {
                progFill.style.width = "100%";
                progText.textContent = "100%";
                progStatus.textContent = "Успешно выложено! 🎉";

                showToast(data.result?.message || "Файл успешно выложен!", "success");
                loadRecentUploads();
                loadDashboard();

                // Clear form
                selectedFile = null;
                document.getElementById("localFilePathInput").value = "";
                document.getElementById("uploadTitleInput").value = "";
                document.getElementById("uploadDescInput").value = "";
                document.querySelector("#uploadDropzone .dropzone-text").textContent = "Перетащите видео или фото сюда, или нажмите для выбора";
            } else {
                progStatus.textContent = "Ошибка: " + (data.detail || "Сбой загрузки");
                showToast(data.detail || "Ошибка при выгрузке файла", "error");
            }
        } catch (err) {
            progStatus.textContent = "Ошибка сети: " + err.message;
            showToast("Ошибка сети при выгрузке", "error");
        } finally {
            btnUpload.disabled = false;
            btnUpload.style.opacity = "1";
        }
    });

    // Change Nickname
    document.getElementById("btnChangeNickname")?.addEventListener("click", async () => {
        const newNick = document.getElementById("newNicknameInput").value.trim();
        if (!newNick) {
            showToast("Введите новый никнейм", "error");
            return;
        }

        const btn = document.getElementById("btnChangeNickname");
        btn.disabled = true;

        try {
            const res = await fetch("/api/account/nickname", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ new_nickname: newNick })
            });
            const data = await res.json();

            if (res.ok && data.success) {
                showToast(data.message, "success");
                document.getElementById("newNicknameInput").value = "";
                loadDashboard();
            } else {
                showToast(data.detail || "Не удалось сменить никнейм", "error");
            }
        } catch (err) {
            showToast("Ошибка запроса: " + err.message, "error");
        } finally {
            btn.disabled = false;
        }
    });

    // Verify & Save Account (All traffic routed strictly via Tor)
    document.getElementById("btnVerifySaveAccount")?.addEventListener("click", async () => {
        const name = document.getElementById("authAccountName").value.trim();
        const cookies = document.getElementById("authCookiesInput").value.trim();
        const fp = document.getElementById("authFingerprintSelect").value;

        if (!cookies) {
            showToast("Вставьте куки аккаунта", "error");
            return;
        }

        const btn = document.getElementById("btnVerifySaveAccount");
        btn.disabled = true;
        btn.textContent = "Проверка сессии через Tor & TLS...";

        try {
            const res = await fetch("/api/accounts/verify", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    name: name,
                    cookies: cookies,
                    fingerprint: fp
                })
            });
            const data = await res.json();

            if (res.ok && data.success) {
                showToast(data.message, "success");
                document.getElementById("authCookiesInput").value = "";
                loadAccounts();
                loadDashboard();
            } else {
                showToast(data.detail || "Ошибка проверки куки", "error");
            }
        } catch (err) {
            showToast("Сбой соединения: " + err.message, "error");
        } finally {
            btn.disabled = false;
            btn.textContent = "Проверить и сохранить аккаунт";
        }
    });

    // Batch Auto-Publisher Controls
    document.getElementById("btnRefreshBatchFiles")?.addEventListener("click", loadBatchFiles);
    document.getElementById("btnStartBatch")?.addEventListener("click", startBatchUpload);
    document.getElementById("btnStopBatch")?.addEventListener("click", stopBatchUpload);
    document.getElementById("batchFolderInput")?.addEventListener("change", loadBatchFiles);

    // Tor Network Controls
    document.getElementById("headerTorChip")?.addEventListener("click", () => {
        const torTabBtn = document.querySelector('.tab-btn[data-tab="tab-antidetect"]');
        if (torTabBtn) torTabBtn.click();
    });

    document.getElementById("torPortSelect")?.addEventListener("change", (e) => {
        const customContainer = document.getElementById("torCustomPortContainer");
        if (customContainer) {
            customContainer.style.display = e.target.value === "custom" ? "block" : "none";
        }
    });

    document.getElementById("btnDetectTorPort")?.addEventListener("click", detectTorPort);
    document.getElementById("btnTestTorCircuit")?.addEventListener("click", testTorCircuit);
    document.getElementById("btnSaveTorSettings")?.addEventListener("click", saveTorSettings);

    // Logs Controls
    document.getElementById("btnRefreshLogs")?.addEventListener("click", loadLogs);
    document.getElementById("btnClearLogs")?.addEventListener("click", async () => {
        await fetch("/api/logs/clear", { method: "POST" });
        loadLogs();
        showToast("Журнал очищен", "info");
    });

    // Bulk Post Redaction (all pages)
    document.getElementById("btnScanAllPosts")?.addEventListener("click", scanAllPosts);
    document.getElementById("btnStartBulkRedact")?.addEventListener("click", startBulkRedact);
    document.getElementById("btnStopBulkRedact")?.addEventListener("click", stopBulkRedact);

    // Check all posts checkbox
    document.getElementById("checkAllPosts")?.addEventListener("change", (e) => {
        const checked = e.target.checked;
        document.querySelectorAll(".post-checkbox").forEach(cb => cb.checked = checked);
    });

    // Filter posts table
    document.getElementById("searchPostsFilter")?.addEventListener("input", (e) => {
        filterPostsTable(e.target.value);
    });
}

// --- Accounts List ---
async function loadAccounts() {
    const container = document.getElementById("accountsListContainer");
    if (!container) return;

    try {
        const res = await fetch("/api/accounts");
        const data = await res.json();
        const accounts = data.accounts || [];
        const activeId = data.active_account_id;

        if (accounts.length === 0) {
            container.innerHTML = `
                <div style="text-align: center; color: var(--text-muted); padding: 2rem;">
                    Нет сохраненных аккаунтов. Вставьте cookies слева.
                </div>
            `;
            return;
        }

        container.innerHTML = accounts.map(acc => {
            const isActive = acc.id === activeId;
            return `
                <div class="stat-card" style="border-color: ${isActive ? 'var(--accent-cyan)' : 'var(--border-color)'};">
                    <div style="flex: 1;">
                        <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.25rem;">
                            <strong style="color: #fff;">@${acc.username || 'unknown'}</strong>
                            ${isActive ? '<span class="brand-badge" style="background: rgba(16, 185, 129, 0.2); color: var(--accent-green); border-color: rgba(16, 185, 129, 0.4);">Активен</span>' : ''}
                        </div>
                        <div style="font-size: 0.8rem; color: var(--text-muted);">
                            UID: ${acc.uid} | Подписчиков: ${acc.subscribers || 0} | Маршрут: 🧅 Tor SOCKS5 (Zero Leak)
                        </div>
                    </div>
                    <div style="display: flex; gap: 0.5rem;">
                        ${!isActive ? `<button class="btn btn-secondary btn-sm" onclick="activateAccount(${acc.id})">Выбрать</button>` : ''}
                        <button class="btn btn-danger btn-sm" onclick="removeAccount(${acc.id})">Удалить</button>
                    </div>
                </div>
            `;
        }).join("");
    } catch (err) {
        console.error("Failed to load accounts:", err);
    }
}

async function activateAccount(id) {
    try {
        const res = await fetch(`/api/accounts/activate/${id}`, { method: "POST" });
        if (res.ok) {
            showToast("Аккаунт активирован", "success");
            loadAccounts();
            loadDashboard();
        }
    } catch (err) {
        showToast("Ошибка активации аккаунта", "error");
    }
}

async function removeAccount(id) {
    if (!confirm("Удалить этот аккаунт из панели?")) return;
    try {
        const res = await fetch(`/api/accounts/${id}`, { method: "DELETE" });
        if (res.ok) {
            showToast("Аккаунт удален", "info");
            loadAccounts();
            loadDashboard();
        }
    } catch (err) {
        showToast("Ошибка удаления", "error");
    }
}

// --- Recent Uploads ---
async function loadRecentUploads() {
    const tbody = document.getElementById("recentUploadsTableBody");
    const countEl = document.getElementById("statUploadsCount");
    if (!tbody) return;

    try {
        const res = await fetch("/api/uploads");
        const data = await res.json();
        const uploads = data.uploads || [];

        if (countEl) {
            countEl.textContent = uploads.filter(u => u.status === "success").length;
        }

        if (uploads.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="7" style="text-align: center; color: var(--text-muted); padding: 2rem;">
                        Нет выложенных файлов
                    </td>
                </tr>
            `;
            return;
        }

        tbody.innerHTML = uploads.map(u => {
            const dateStr = new Date(u.created_at * 1000).toLocaleString("ru-RU", {
                day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit"
            });
            const sizeMb = (u.file_size / (1024 * 1024)).toFixed(1);
            const isSuccess = u.status === "success";

            return `
                <tr>
                    <td>#${u.id}</td>
                    <td><strong>${u.title || u.filename}</strong></td>
                    <td style="color: var(--text-muted);">${u.filename}</td>
                    <td>${sizeMb} MB</td>
                    <td><code>${u.remote_fid || '-'}</code></td>
                    <td>
                        <span class="brand-badge" style="background: ${isSuccess ? 'rgba(16, 185, 129, 0.2)' : 'rgba(239, 68, 68, 0.2)'}; color: ${isSuccess ? 'var(--accent-green)' : 'var(--accent-red)'}; border-color: transparent;">
                            ${u.status}
                        </span>
                    </td>
                    <td style="color: var(--text-muted); font-size: 0.8rem;">${dateStr}</td>
                </tr>
            `;
        }).join("");
    } catch (err) {
        console.error("Failed to load uploads:", err);
    }
}

// --- Logs Console ---
async function loadLogs() {
    const consoleBox = document.getElementById("logsConsole");
    if (!consoleBox) return;

    try {
        const res = await fetch("/api/logs");
        const data = await res.json();
        const logs = data.logs || [];

        if (logs.length === 0) {
            consoleBox.innerHTML = '<div style="color: var(--text-muted); text-align: center; padding: 2rem;">Журнал пуст</div>';
            return;
        }

        consoleBox.innerHTML = logs.map(l => {
            const t = new Date(l.created_at * 1000).toLocaleTimeString("ru-RU");
            return `
                <div class="log-entry">
                    <span class="log-time">[${t}]</span>
                    <span class="log-level ${l.level}">${l.level}</span>
                    <span class="log-text">${l.message} ${l.details ? `<span style="color: #64748b;">(${l.details})</span>` : ''}</span>
                </div>
            `;
        }).join("");
    } catch (err) {
        console.error("Failed to load logs:", err);
    }
}

// --- Tor Network Management ---
async function loadTorConfig() {
    try {
        const res = await fetch("/api/tor/config");
        const data = await res.json();
        if (data.success && data.config) {
            const cfg = data.config;
            const select = document.getElementById("torPortSelect");
            const customInput = document.getElementById("torCustomPortInput");
            const customContainer = document.getElementById("torCustomPortContainer");
            const strictSwitch = document.getElementById("torStrictSwitch");

            if (strictSwitch) strictSwitch.checked = !!cfg.strict;

            if (select) {
                if (cfg.port === 9150 || cfg.port === 9050) {
                    select.value = String(cfg.port);
                    if (customContainer) customContainer.style.display = "none";
                } else {
                    select.value = "custom";
                    if (customContainer) customContainer.style.display = "block";
                    if (customInput) customInput.value = cfg.port;
                }
            }

            const uploadRoute = document.getElementById("uploadTorRouteText");
            if (uploadRoute) uploadRoute.textContent = `127.0.0.1:${cfg.port}`;
        }
    } catch (err) {
        console.error("Failed to load Tor config:", err);
    }
}

async function loadTorStatus() {
    const chipDot = document.getElementById("headerTorDot");
    const chipText = document.getElementById("headerTorStatusText");
    const statusBadge = document.getElementById("torStatusBadge");
    const exitIp = document.getElementById("torExitIpValue");
    const isTorVal = document.getElementById("torIsTorValue");
    const portVal = document.getElementById("torActivePortValue");
    const latencyVal = document.getElementById("torLatencyValue");
    const uploadRoute = document.getElementById("uploadTorRouteText");

    try {
        const res = await fetch("/api/tor/status");
        const data = await res.json();
        const st = data.status || {};

        if (st.connected) {
            if (chipDot) chipDot.className = "tor-dot active";
            if (chipText) chipText.textContent = `Tor: ${st.ip}`;
            if (statusBadge) {
                statusBadge.style.color = "var(--accent-green)";
                statusBadge.innerHTML = '<span class="tor-dot active"></span> Подключено (Активен)';
            }
            if (exitIp) exitIp.textContent = st.ip || "Определен";
            if (isTorVal) {
                isTorVal.style.color = st.is_tor ? "var(--accent-green)" : "var(--accent-amber)";
                isTorVal.textContent = st.is_tor ? "Да (Tor Exit Node)" : "Внешний IP";
            }
            if (portVal) portVal.textContent = `127.0.0.1:${st.port}`;
            if (latencyVal) latencyVal.textContent = `${st.latency_ms} мс`;
            if (uploadRoute) uploadRoute.textContent = `127.0.0.1:${st.port}`;
        } else {
            if (chipDot) chipDot.className = "tor-dot";
            if (chipText) chipText.textContent = "Tor: Отключен";
            if (statusBadge) {
                statusBadge.style.color = "var(--accent-red)";
                statusBadge.innerHTML = '<span class="tor-dot"></span> Отключено / Сбой';
            }
            if (exitIp) exitIp.textContent = "Недоступен";
            if (isTorVal) {
                isTorVal.style.color = "var(--accent-red)";
                isTorVal.textContent = "Нет связи";
            }
            if (portVal) portVal.textContent = `127.0.0.1:${st.port || 9150}`;
            if (latencyVal) latencyVal.textContent = "-";
        }
    } catch (err) {
        console.error("Failed to load Tor status:", err);
    }
}

async function testTorCircuit() {
    const btn = document.getElementById("btnTestTorCircuit");
    if (btn) {
        btn.disabled = true;
        btn.textContent = "Тестирование цепи...";
    }

    try {
        let port = parseInt(document.getElementById("torPortSelect").value, 10);
        if (isNaN(port) || document.getElementById("torPortSelect").value === "custom") {
            port = parseInt(document.getElementById("torCustomPortInput").value, 10) || 9150;
        }

        const res = await fetch("/api/tor/test", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ port: port })
        });
        const data = await res.json();
        const r = data.result || {};

        if (data.success && r.connected) {
            showToast(`🧅 Tor цепь активна! Выходной IP: ${r.ip} (${r.latency_ms}мс)`, "success");
            loadTorStatus();
        } else {
            showToast(r.error || "Не удалось подключиться к Tor на указанном порту", "error");
            loadTorStatus();
        }
    } catch (err) {
        showToast("Ошибка проверки Tor: " + err.message, "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = `
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="23 4 23 10 17 10"></polyline>
                    <polyline points="1 20 1 14 7 14"></polyline>
                    <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path>
                </svg>
                Проверить цепь Tor
            `;
        }
    }
}

async function detectTorPort() {
    const btn = document.getElementById("btnDetectTorPort");
    if (btn) {
        btn.disabled = true;
        btn.textContent = "Поиск...";
    }

    try {
        const res = await fetch("/api/tor/detect", { method: "POST" });
        const data = await res.json();

        if (data.success && data.port) {
            const select = document.getElementById("torPortSelect");
            if (select) {
                if (data.port === 9150 || data.port === 9050) {
                    select.value = String(data.port);
                    document.getElementById("torCustomPortContainer").style.display = "none";
                } else {
                    select.value = "custom";
                    document.getElementById("torCustomPortContainer").style.display = "block";
                    document.getElementById("torCustomPortInput").value = data.port;
                }
            }
            showToast(`Найден активный Tor порт: ${data.port}`, "success");
            await saveTorSettings();
        } else {
            showToast(data.error || "Активный порт Tor не найден. Запустите Tor Browser.", "error");
        }
    } catch (err) {
        showToast("Ошибка автопоиска порта: " + err.message, "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = `
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <circle cx="12" cy="12" r="10"></circle>
                    <line x1="2" y1="12" x2="22" y2="12"></line>
                    <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path>
                </svg>
                Автопоиск порта
            `;
        }
    }
}

async function saveTorSettings() {
    let port = parseInt(document.getElementById("torPortSelect").value, 10);
    if (isNaN(port) || document.getElementById("torPortSelect").value === "custom") {
        port = parseInt(document.getElementById("torCustomPortInput").value, 10) || 9150;
    }
    const strict = document.getElementById("torStrictSwitch").checked;

    try {
        const res = await fetch("/api/tor/config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                enabled: true,
                port: port,
                strict: strict
            })
        });
        const data = await res.json();
        if (data.success) {
            showToast(data.message || "Настройки Tor сохранены", "success");
            loadTorStatus();
        } else {
            showToast("Ошибка сохранения настроек Tor", "error");
        }
    } catch (err) {
        showToast("Сбой запроса: " + err.message, "error");
    }
}

// --- Batch Auto-Publisher Management ---
async function loadBatchSettings() {
    try {
        const res = await fetch("/api/batch/settings");
        const data = await res.json();
        if (data.success) {
            const folderInput = document.getElementById("batchFolderInput");
            const intervalInput = document.getElementById("batchIntervalInput");
            const titleInput = document.getElementById("batchTitleInput");
            const descInput = document.getElementById("batchDescInput");

            if (folderInput && data.folder) folderInput.value = data.folder;
            if (intervalInput && data.interval_min) intervalInput.value = data.interval_min;
            if (titleInput && data.title) titleInput.value = data.title;
            if (descInput && data.desc) descInput.value = data.desc;
        }
    } catch (err) {
        console.error("Failed to load batch settings:", err);
    }
}

async function loadBatchFiles() {
    const folderInput = document.getElementById("batchFolderInput");
    const folder = folderInput ? folderInput.value.trim() : "videos";
    const tbody = document.getElementById("batchFilesTableBody");
    const badge = document.getElementById("folderTitleBadge");
    const label = document.getElementById("batchFolderLabel");
    const totalEl = document.getElementById("batchTotalCount");

    if (badge) badge.textContent = folder || "videos";
    if (label) label.textContent = folder || "videos";

    try {
        const res = await fetch(`/api/batch/files?folder=${encodeURIComponent(folder)}`);
        const data = await res.json();
        const files = data.files || [];

        if (totalEl) totalEl.textContent = files.length;

        if (!tbody) return;

        if (files.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="5" style="text-align: center; color: var(--text-muted); padding: 2rem;">
                        В папке <code>${data.folder || folder}</code> пока нет медиа файлов (MP4, WEBM, MOV, JPG, PNG). Поместите видео в эту папку.
                    </td>
                </tr>
            `;
            return;
        }

        tbody.innerHTML = files.map((f, i) => {
            let statusBadge = '';
            if (f.status === 'uploaded') {
                statusBadge = '<span class="brand-badge" style="background: rgba(16, 185, 129, 0.2); color: var(--accent-green); border-color: transparent;">✅ Опубликовано</span>';
            } else if (f.status === 'duplicate') {
                statusBadge = '<span class="brand-badge" style="background: rgba(245, 158, 11, 0.2); color: var(--accent-amber); border-color: transparent;">⏩ Дубликат (already exists)</span>';
            } else {
                statusBadge = '<span class="brand-badge" style="background: rgba(6, 182, 212, 0.15); color: var(--accent-cyan); border-color: transparent;">⏳ Ожидает очереди</span>';
            }

            return `
                <tr>
                    <td>${i + 1}</td>
                    <td><strong>${f.name}</strong></td>
                    <td style="color: var(--text-muted);">${f.size_mb} MB</td>
                    <td><code>${f.ext}</code></td>
                    <td>${statusBadge}</td>
                </tr>
            `;
        }).join("");
    } catch (err) {
        console.error("Failed to load batch files:", err);
    }
}

async function loadBatchStatus() {
    try {
        const res = await fetch("/api/batch/status");
        const data = await res.json();
        const st = data.status || {};

        const statusEl = document.getElementById("batchStatusText");
        const totalEl = document.getElementById("batchTotalCount");
        const successEl = document.getElementById("batchSuccessCount");
        const dupEl = document.getElementById("batchDuplicateCount");
        const timerEl = document.getElementById("batchCountdownTimer");
        const msgBox = document.getElementById("batchMessageBox");
        const startBtn = document.getElementById("btnStartBatch");
        const stopBtn = document.getElementById("btnStopBatch");

        if (totalEl && st.total_files !== undefined && st.total_files > 0) {
            totalEl.textContent = st.total_files;
        }
        if (successEl) successEl.textContent = st.success_count || 0;
        if (dupEl) dupEl.textContent = st.duplicate_count || 0;

        if (timerEl) {
            const rem = st.delay_remaining || 0;
            const m = Math.floor(rem / 60).toString().padStart(2, "0");
            const s = (rem % 60).toString().padStart(2, "0");
            timerEl.textContent = `${m}:${s}`;
        }

        if (msgBox && st.last_message) {
            msgBox.innerHTML = `<strong>Текущее действие:</strong> ${st.last_message}`;
        }

        if (st.is_running) {
            if (startBtn) startBtn.style.display = "none";
            if (stopBtn) stopBtn.style.display = "inline-flex";

            if (st.state === "waiting_delay") {
                if (statusEl) {
                    statusEl.style.color = "var(--accent-amber)";
                    statusEl.textContent = `⏳ Пауза перед следующим (${st.delay_remaining} с)`;
                }
            } else {
                if (statusEl) {
                    statusEl.style.color = "var(--accent-green)";
                    statusEl.textContent = `🚀 Выгрузка: ${st.current_file || 'обработка'}`;
                }
            }
        } else {
            if (startBtn) startBtn.style.display = "inline-flex";
            if (stopBtn) stopBtn.style.display = "none";
            if (statusEl) {
                statusEl.style.color = "var(--text-muted)";
                statusEl.textContent = "Ожидание запуска";
            }
        }
    } catch (err) {
        // silent catch
    }
}

async function startBatchUpload() {
    const folder = document.getElementById("batchFolderInput")?.value.trim() || "videos";
    const interval = parseFloat(document.getElementById("batchIntervalInput")?.value) || 5;
    const title = document.getElementById("batchTitleInput")?.value.trim() || "";
    const desc = document.getElementById("batchDescInput")?.value.trim() || "";

    const btn = document.getElementById("btnStartBatch");
    if (btn) btn.disabled = true;

    try {
        const res = await fetch("/api/batch/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                folder_path: folder,
                delay_minutes: interval,
                title_template: title,
                desc_template: desc
            })
        });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast("🚀 Пакетная публикация видео запущена!", "success");
            loadBatchStatus();
            loadBatchFiles();
        } else {
            showToast(data.detail || data.error || "Не удалось запустить публикацию", "error");
        }
    } catch (err) {
        showToast("Сбой запуска: " + err.message, "error");
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function stopBatchUpload() {
    try {
        const res = await fetch("/api/batch/stop", { method: "POST" });
        const data = await res.json();
        if (data.success) {
            showToast("⏹️ Публикация очереди остановлена", "info");
            loadBatchStatus();
            loadBatchFiles();
        }
    } catch (err) {
        showToast("Ошибка остановки: " + err.message, "error");
    }
}

// --- Bulk Post Redaction (All pages pagination) ---
let cachedPosts = [];

async function scanAllPosts() {
    const btn = document.getElementById("btnScanAllPosts");
    const tbody = document.getElementById("postsTableBody");
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<span class="spinner" style="display:inline-block;width:14px;height:14px;border:2px solid #fff;border-top-color:transparent;border-radius:50%;animation:spin 0.8s linear infinite;"></span> Сканирование страниц 1, 2, 3...`;
    }

    if (tbody) {
        tbody.innerHTML = `
            <tr>
                <td colspan="9" style="text-align: center; color: var(--accent-cyan); padding: 2rem;">
                    ⏳ Выполняется глубокое сканирование всех страниц профиля через Tor (page=1, page=2, page=3, page=4...)...
                </td>
            </tr>
        `;
    }

    try {
        const res = await fetch("/api/posts/scan?max_pages=50");
        const data = await res.json();

        if (res.ok && data.success) {
            cachedPosts = data.posts || [];
            updateRedactSummary(data.username, cachedPosts);
            renderPostsTable(cachedPosts);
            showToast(`✓ Найдено ${cachedPosts.length} публикаций на всех страницах!`, "success");
        } else {
            showToast(data.detail || data.error || "Ошибка сканирования постов", "error");
            if (tbody) {
                tbody.innerHTML = `
                    <tr>
                        <td colspan="9" style="text-align: center; color: var(--accent-red); padding: 2rem;">
                            Ошибка: ${data.detail || data.error || "Не удалось загрузить посты"}
                        </td>
                    </tr>
                `;
            }
        }
    } catch (err) {
        showToast("Сбой сканирования: " + err.message, "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = `
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <circle cx="11" cy="11" r="8"></circle>
                    <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
                </svg>
                🔍 Сканировать все страницы
            `;
        }
    }
}

function updateRedactSummary(username, posts) {
    const userEl = document.getElementById("redactChannelName");
    const totalEl = document.getElementById("badgeTotalPostsCount");
    const pagesEl = document.getElementById("badgeTotalPagesCount");
    const videosEl = document.getElementById("badgeVideosCount");
    const photosEl = document.getElementById("badgePhotosCount");

    if (userEl) userEl.textContent = username ? `@${username}` : "-";
    if (totalEl) totalEl.textContent = posts.length;

    const uniquePages = new Set(posts.map(p => p.page)).size;
    if (pagesEl) pagesEl.textContent = uniquePages;

    const vids = posts.filter(p => p.type === "video").length;
    const photos = posts.filter(p => p.type === "photo").length;
    if (videosEl) videosEl.textContent = vids;
    if (photosEl) photosEl.textContent = photos;
}

function renderPostsTable(posts) {
    const tbody = document.getElementById("postsTableBody");
    if (!tbody) return;

    if (!posts || posts.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="9" style="text-align: center; color: var(--text-muted); padding: 2rem;">
                    Публикации не найдены.
                </td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = posts.map((p, idx) => {
        const isVideo = p.type === "video";
        const typeBadge = isVideo
            ? `<span class="badge" style="background: rgba(16, 185, 129, 0.15); color: var(--accent-green); border: 1px solid rgba(16, 185, 129, 0.3); padding: 2px 6px; font-size: 0.75rem; border-radius: 4px;">🎬 Видео</span>`
            : `<span class="badge" style="background: rgba(56, 189, 248, 0.15); color: var(--accent-cyan); border: 1px solid rgba(56, 189, 248, 0.3); padding: 2px 6px; font-size: 0.75rem; border-radius: 4px;">📷 Фото</span>`;

        let statusBadge = `<span class="badge" id="status-badge-${p.file_id}" style="background: rgba(255, 255, 255, 0.05); color: var(--text-muted); padding: 2px 6px; font-size: 0.75rem; border-radius: 4px;">Ожидает</span>`;
        if (p.status === "updated") {
            statusBadge = `<span class="badge" id="status-badge-${p.file_id}" style="background: rgba(16, 185, 129, 0.2); color: var(--accent-green); padding: 2px 6px; font-size: 0.75rem; border-radius: 4px;">✓ Обновлен</span>`;
        } else if (p.status === "failed") {
            statusBadge = `<span class="badge" id="status-badge-${p.file_id}" style="background: rgba(239, 68, 68, 0.2); color: var(--accent-red); padding: 2px 6px; font-size: 0.75rem; border-radius: 4px;">Ошибка</span>`;
        }

        return `
            <tr data-fid="${p.file_id}" data-title="${escapeHtml(p.title || '')}">
                <td><input type="checkbox" class="post-checkbox" data-fid="${p.file_id}" checked></td>
                <td style="color: var(--text-muted); font-size: 0.85rem;">${idx + 1}</td>
                <td><code style="color: var(--accent-cyan); font-family: monospace;">${p.file_id}</code></td>
                <td><span class="badge" style="background: rgba(168, 85, 247, 0.15); color: var(--accent-purple); padding: 2px 6px; font-size: 0.75rem; border-radius: 4px;">Стр. ${p.page || 1}</span></td>
                <td>${typeBadge}</td>
                <td style="max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" id="title-cell-${p.file_id}" title="${escapeHtml(p.title || '')}">
                    ${escapeHtml(p.title || '<без названия>')}
                </td>
                <td style="color: var(--text-muted); font-size: 0.85rem;">👁️ ${(p.views || 0).toLocaleString()}</td>
                <td>
                    <a href="${p.url || ('https://wvids.com/' + p.file_id)}" target="_blank" style="color: var(--accent-cyan); text-decoration: none; font-size: 0.85rem;">
                        ↗ Открыть
                    </a>
                </td>
                <td>${statusBadge}</td>
            </tr>
        `;
    }).join("");
}

function filterPostsTable(query) {
    const q = (query || "").toLowerCase().trim();
    const rows = document.querySelectorAll("#postsTableBody tr");
    rows.forEach(row => {
        const fid = row.dataset.fid || "";
        const title = (row.dataset.title || "").toLowerCase();
        if (!q || fid.includes(q) || title.includes(q)) {
            row.style.display = "";
        } else {
            row.style.display = "none";
        }
    });
}

function escapeHtml(str) {
    if (!str) return "";
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

async function startBulkRedact() {
    const titleTpl = document.getElementById("bulkRedactTitleInput")?.value.trim() || "";
    const descTpl = document.getElementById("bulkRedactDescInput")?.value.trim() || "";

    if (!titleTpl && !descTpl) {
        showToast("Укажите хотя бы новый заголовок или новое описание", "error");
        return;
    }

    const checkedBoxes = Array.from(document.querySelectorAll(".post-checkbox:checked"));
    const checkedFids = checkedBoxes.map(cb => cb.dataset.fid);

    if (cachedPosts.length > 0 && checkedFids.length === 0) {
        showToast("Выберите хотя бы один пост из таблицы галочкой", "error");
        return;
    }

    const startBtn = document.getElementById("btnStartBulkRedact");
    if (startBtn) startBtn.disabled = true;

    try {
        const res = await fetch("/api/posts/bulk-redact", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                title_template: titleTpl,
                desc_template: descTpl,
                file_ids: checkedFids.length > 0 ? checkedFids : null
            })
        });
        const data = await res.json();

        if (res.ok && data.success) {
            showToast(`🚀 ${data.message}`, "success");
            loadBulkRedactStatus();
        } else {
            showToast(data.detail || data.error || "Не удалось запустить процесс", "error");
        }
    } catch (err) {
        showToast("Сбой запуска: " + err.message, "error");
    } finally {
        if (startBtn) startBtn.disabled = false;
    }
}

async function stopBulkRedact() {
    try {
        const res = await fetch("/api/posts/bulk-redact/stop", { method: "POST" });
        const data = await res.json();
        if (data.success) {
            showToast("⏹️ Массовое редактирование остановлено", "info");
            loadBulkRedactStatus();
        }
    } catch (err) {
        showToast("Ошибка остановки: " + err.message, "error");
    }
}

async function loadBulkRedactStatus() {
    try {
        const res = await fetch("/api/posts/status");
        if (!res.ok) return;
        const data = await res.json();
        const st = data.status;
        if (!st) return;

        const progContainer = document.getElementById("bulkRedactProgressContainer");
        const statusText = document.getElementById("bulkRedactStatusText");
        const percentText = document.getElementById("bulkRedactPercentText");
        const progFill = document.getElementById("bulkRedactProgressFill");
        const startBtn = document.getElementById("btnStartBulkRedact");
        const stopBtn = document.getElementById("btnStopBulkRedact");

        if (st.is_running) {
            if (progContainer) progContainer.style.display = "block";
            if (statusText) statusText.textContent = st.status_text;
            if (percentText) percentText.textContent = `${st.percent || 0}%`;
            if (progFill) progFill.style.width = `${st.percent || 0}%`;
            if (startBtn) startBtn.style.display = "none";
            if (stopBtn) stopBtn.style.display = "inline-flex";

            // Live update rows in table
            if (st.posts && st.posts.length > 0) {
                st.posts.forEach(p => {
                    const badge = document.getElementById(`status-badge-${p.file_id}`);
                    const titleCell = document.getElementById(`title-cell-${p.file_id}`);
                    if (badge) {
                        if (p.status === "updated") {
                            badge.textContent = "✓ Обновлен";
                            badge.style.background = "rgba(16, 185, 129, 0.2)";
                            badge.style.color = "var(--accent-green)";
                        } else if (p.status === "failed") {
                            badge.textContent = "Ошибка";
                            badge.style.background = "rgba(239, 68, 68, 0.2)";
                            badge.style.color = "var(--accent-red)";
                        }
                    }
                    if (titleCell && p.title) {
                        titleCell.textContent = p.title;
                    }
                });
            }
        } else {
            if (startBtn) startBtn.style.display = "inline-flex";
            if (stopBtn) stopBtn.style.display = "none";
            if (st.percent === 100 && progContainer) {
                if (statusText) statusText.textContent = st.status_text;
                if (percentText) percentText.textContent = "100%";
                if (progFill) progFill.style.width = "100%";
            }
        }
    } catch (err) {
        // silent
    }
}

