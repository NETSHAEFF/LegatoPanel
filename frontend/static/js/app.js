// Wvids Panel Main Frontend Logic

let chartInstance = null;
let selectedFile = null;
let currentActiveAccountId = null;

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
    loadNetworkStatus();
    loadBatchSettings();
    loadBatchFiles();
    loadBatchStatus();
    loadBulkRedactStatus();
    loadProcessingSettings();
    initProcessingStudio();
    // Periodic refresh
    setInterval(loadTorStatus, 30000);
    setInterval(loadNetworkStatus, 25000);
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
                if (target === "tab-processing") {
                    updateLivePreview();
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
            currentActiveAccountId = data.account.id;
            updateHeaderStatus(data.account);

            const stats = data.stats || {};
            document.getElementById("statSubscribers").textContent = (stats.subscribers || 0).toLocaleString();
            document.getElementById("statViewsToday").textContent = (stats.views_today || 0).toLocaleString();
            document.getElementById("statTotalViews").textContent = (stats.total_views || 0).toLocaleString();

            renderViewsChart(stats.chart_labels || [], stats.chart_data || []);
        } else {
            currentActiveAccountId = null;
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
    const headerSuspendedBadge = document.getElementById("headerSuspendedBadge");
    const suspendedBanner = document.getElementById("accountSuspendedBanner");
    const bannerAccName = document.getElementById("suspendedAccountName");
    const bannerTime = document.getElementById("suspendedAccountTime");
    const uploadSuspendedWarning = document.getElementById("uploadSuspendedWarning");
    const btnStartUpload = document.getElementById("btnStartUpload");

    const isSuspended = account && (account.is_suspended === 1 || account.is_suspended === true);

    if (account && account.username) {
        if (isSuspended) {
            dot.className = "status-dot suspended";
            if (headerSuspendedBadge) headerSuspendedBadge.style.display = "inline-flex";
            if (suspendedBanner) suspendedBanner.style.display = "block";
            if (bannerAccName) bannerAccName.textContent = `@${account.username}`;
            const reason = account.suspension_reason || "Account suspended";
            if (bannerTime) {
                let timeStr = "";
                if (account.suspended_at) {
                    timeStr = ` | Зафиксировано: ${new Date(account.suspended_at * 1000).toLocaleString()}`;
                }
                bannerTime.innerHTML = `Причина: <strong>${reason}</strong>${timeStr} | Рекомендуется сменить активный аккаунт`;
            }
            if (uploadSuspendedWarning) uploadSuspendedWarning.style.display = "block";
            if (btnStartUpload) {
                btnStartUpload.disabled = true;
                btnStartUpload.style.opacity = "0.5";
                btnStartUpload.style.cursor = "not-allowed";
                btnStartUpload.title = "Аккаунт заблокирован (Account suspended). Загрузка невозможна.";
            }
        } else {
            dot.className = "status-dot active";
            if (headerSuspendedBadge) headerSuspendedBadge.style.display = "none";
            if (suspendedBanner) suspendedBanner.style.display = "none";
            if (uploadSuspendedWarning) uploadSuspendedWarning.style.display = "none";
            if (btnStartUpload) {
                btnStartUpload.disabled = false;
                btnStartUpload.style.opacity = "";
                btnStartUpload.style.cursor = "";
                btnStartUpload.title = "";
            }
        }

        userEl.textContent = `@${account.username}`;
        uidEl.textContent = `(UID: ${account.uid})`;
        if (curNick) curNick.value = account.username;
        if (profLink) {
            profLink.href = `https://wvids.com/@${account.username}`;
            profLink.textContent = `https://wvids.com/@${account.username}`;
        }
        if (profUid) profUid.textContent = account.uid || "-";
    } else {
        dot.className = "status-dot";
        if (headerSuspendedBadge) headerSuspendedBadge.style.display = "none";
        if (suspendedBanner) suspendedBanner.style.display = "none";
        if (uploadSuspendedWarning) uploadSuspendedWarning.style.display = "none";
        if (btnStartUpload) {
            btnStartUpload.disabled = false;
            btnStartUpload.style.opacity = "";
            btnStartUpload.style.cursor = "";
            btnStartUpload.title = "";
        }
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

    document.getElementById("btnStartStandaloneTor")?.addEventListener("click", startStandaloneTor);
    document.getElementById("btnStopStandaloneTor")?.addEventListener("click", stopStandaloneTor);
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

    // Suspended Banner Controls
    document.getElementById("btnCheckAccountStatus")?.addEventListener("click", () => {
        if (currentActiveAccountId) {
            checkAccountStatus(currentActiveAccountId);
        } else {
            showToast("Нет активного аккаунта для проверки", "error");
        }
    });

    document.getElementById("btnSwitchSuspendedAccount")?.addEventListener("click", () => {
        const authTabBtn = document.querySelector('.tab-btn[data-tab="tab-auth"]');
        if (authTabBtn) authTabBtn.click();
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
            const isSuspended = acc.is_suspended === 1 || acc.is_suspended === true;
            const cardClass = isSuspended ? "stat-card account-suspended-card" : "stat-card";
            const borderStyle = isSuspended
                ? "border-color: rgba(239, 68, 68, 0.7); background: rgba(239, 68, 68, 0.06);"
                : (isActive ? "border-color: var(--accent-cyan);" : "border-color: var(--border-color);");

            return `
                <div class="${cardClass}" style="${borderStyle}">
                    <div style="flex: 1;">
                        <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.25rem; flex-wrap: wrap;">
                            <strong style="color: ${isSuspended ? '#fca5a5' : '#fff'};">@${acc.username || 'unknown'}</strong>
                            ${isActive ? '<span class="brand-badge" style="background: rgba(16, 185, 129, 0.2); color: var(--accent-green); border-color: rgba(16, 185, 129, 0.4);">Активен</span>' : ''}
                            ${isSuspended ? `<span class="suspended-badge" title="${acc.suspension_reason || 'Account suspended'}">⛔ Account suspended</span>` : ''}
                        </div>
                        <div style="font-size: 0.8rem; color: var(--text-muted);">
                            UID: ${acc.uid} | Подписчиков: ${acc.subscribers || 0} | Маршрут: 🧅 Tor SOCKS5 (Zero Leak)
                        </div>
                        ${isSuspended ? `
                            <div style="font-size: 0.78rem; color: #ef4444; margin-top: 0.25rem; font-weight: 500;">
                                ⚠️ Статус: ${acc.suspension_reason || 'Account suspended'}
                            </div>
                        ` : ''}
                    </div>
                    <div style="display: flex; gap: 0.4rem; align-items: center; flex-wrap: wrap;">
                        <button class="btn btn-secondary btn-sm" onclick="checkAccountStatus(${acc.id})" title="Проверить live статус на wvids.com">🔄 Статус</button>
                        ${isSuspended ?
                            `<button class="btn btn-secondary btn-sm" style="color: #10b981; border-color: rgba(16, 185, 129, 0.4);" onclick="toggleSimulateSuspension(${acc.id}, false)" title="Снять блокировку (симуляцию)">✅ Снять блок</button>` :
                            `<button class="btn btn-secondary btn-sm" style="color: #f87171; border-color: rgba(239, 68, 68, 0.4);" onclick="toggleSimulateSuspension(${acc.id}, true)" title="Протестировать уведомление бана">🚨 Тест бана</button>`
                        }
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

async function checkAccountStatus(id) {
    showToast("Проверка статуса аккаунта на wvids.com...", "info");
    try {
        const res = await fetch(`/api/accounts/${id}/check-status`, { method: "POST" });
        const data = await res.json();
        if (data.success) {
            if (data.is_suspended) {
                showToast(`🚨 Аккаунт заблокирован: ${data.reason}`, "error");
            } else {
                showToast("✅ Аккаунт активен (блокировок не обнаружено)", "success");
            }
            loadAccounts();
            loadDashboard();
        } else {
            showToast(data.error || "Ошибка проверки статуса", "error");
        }
    } catch (err) {
        showToast("Сбой проверки: " + err.message, "error");
    }
}

async function toggleSimulateSuspension(id, simulate) {
    try {
        const res = await fetch(`/api/accounts/${id}/simulate-suspension`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ simulate: simulate })
        });
        const data = await res.json();
        if (data.success) {
            showToast(data.message, simulate ? "warning" : "success");
            loadAccounts();
            loadDashboard();
        } else {
            showToast(data.error || "Ошибка изменения статуса", "error");
        }
    } catch (err) {
        showToast("Ошибка: " + err.message, "error");
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

            // Feed visibility badge
            let feedBadge = '<span style="color: var(--text-muted); font-size: 0.8rem;">—</span>';
            if (u.remote_fid) {
                if (u.feed_status === 'in_feed') {
                    feedBadge = `
                        <div style="display: inline-flex; align-items: center; gap: 0.35rem;">
                            <span class="brand-badge" style="background: rgba(16, 185, 129, 0.2); color: var(--accent-green);" title="Подтверждено: видео есть в общей ленте /feed (виден зрителям)">
                                🟢 В ленте
                            </span>
                            <button class="btn btn-secondary btn-xs" onclick="checkUploadFeed(${u.id}, '${u.remote_fid}')" title="Перепроверить видимость" style="padding: 0.2rem 0.4rem; font-size: 0.75rem;">🔄</button>
                        </div>
                    `;
                } else if (u.feed_status === 'shadowbanned') {
                    feedBadge = `
                        <div style="display: inline-flex; align-items: center; gap: 0.35rem;">
                            <span class="brand-badge" style="background: rgba(239, 68, 68, 0.25); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.4);" title="🚨 Видео отсутствует в общей ленте /feed (Теневой бан). Причины: Tor IP, ссылки на Telegram в описании или фильтр гео.">
                                🚨 Shadowban
                            </span>
                            <button class="btn btn-secondary btn-xs" onclick="checkUploadFeed(${u.id}, '${u.remote_fid}')" title="Перепроверить видимость" style="padding: 0.2rem 0.4rem; font-size: 0.75rem;">🔄</button>
                        </div>
                    `;
                } else if (u.feed_status === 'checking') {
                    feedBadge = `
                        <span class="brand-badge" style="background: rgba(245, 158, 11, 0.2); color: var(--accent-amber);" title="Идет проверка в ленте wvids.com/feed...">
                            ⏳ Проверка...
                        </span>
                    `;
                } else {
                    feedBadge = `
                        <button class="btn btn-secondary btn-xs" onclick="checkUploadFeed(${u.id}, '${u.remote_fid}')" style="padding: 0.25rem 0.6rem; font-size: 0.75rem;" title="Проверить отображение в общей ленте /feed">
                            🔍 Проверить
                        </button>
                    `;
                }
            }

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
                    <td>${feedBadge}</td>
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

            // Load Exit Country & StrictNodes
            const countrySelect = document.getElementById("torExitCountrySelect");
            const customCountryInput = document.getElementById("torCustomCountryInput");
            const customCountryContainer = document.getElementById("torCustomCountryContainer");
            const strictNodesSwitch = document.getElementById("torStrictNodesSwitch");

            if (strictNodesSwitch) {
                strictNodesSwitch.checked = cfg.strict_nodes !== false;
            }

            if (countrySelect) {
                const country = (cfg.exit_country || "all").toLowerCase();
                const standardOptions = ["all", "ru", "us", "de", "nl", "fr", "gb", "pl", "ca", "se", "ch"];
                if (standardOptions.includes(country)) {
                    countrySelect.value = country;
                    if (customCountryContainer) customCountryContainer.style.display = "none";
                } else {
                    countrySelect.value = "custom";
                    if (customCountryContainer) customCountryContainer.style.display = "block";
                    if (customCountryInput) customCountryInput.value = country;
                }
            }
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
        const svc = data.service || {};
        const modeVal = document.getElementById("torModeValue");
        const btnStart = document.getElementById("btnStartStandaloneTor");
        const btnStop = document.getElementById("btnStopStandaloneTor");

        if (modeVal) {
            if (svc.managed_by_panel) {
                modeVal.textContent = `Автономный (PID: ${svc.pid})`;
                modeVal.style.color = "var(--accent-green)";
            } else if (st.connected) {
                modeVal.textContent = st.port === 9150 ? "Tor Browser" : "Фоновая служба";
                modeVal.style.color = "var(--accent-cyan)";
            } else {
                modeVal.textContent = "Не запущен";
                modeVal.style.color = "var(--accent-red)";
            }
        }

        if (btnStart && btnStop) {
            if (svc.managed_by_panel) {
                btnStart.style.display = "none";
                btnStop.style.display = "inline-flex";
            } else {
                btnStart.style.display = "inline-flex";
                btnStop.style.display = "none";
            }
        }

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
            const countryVal = document.getElementById("torCountryValue");
            if (countryVal) {
                if (st.country && st.country !== "Unknown") {
                    const flag = getCountryFlagEmoji(st.country_code);
                    countryVal.innerHTML = `<span style="font-size: 1.15rem;">${flag}</span> <strong style="color: var(--accent-cyan);">${st.country}</strong> ${st.city ? `<span style="color: var(--text-muted); font-size: 0.8rem;">(${st.city})</span>` : ''}`;
                } else {
                    countryVal.textContent = "🌐 Определение...";
                }
            }
            if (chipText && st.country_code) {
                chipText.textContent = `Tor: ${st.ip} [${getCountryFlagEmoji(st.country_code)} ${st.country_code}]`;
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
            if (portVal) portVal.textContent = `127.0.0.1:${st.port || 9050}`;
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

async function startStandaloneTor() {
    const btn = document.getElementById("btnStartStandaloneTor");
    if (btn) {
        btn.disabled = true;
        btn.textContent = "Запуск Tor...";
    }
    try {
        const res = await fetch("/api/tor/service/start", { method: "POST" });
        const data = await res.json();
        if (data.success) {
            showToast("🧅 Автономный Tor успешно запущен в фоне (без браузера)!", "success");
            loadTorStatus();
        } else {
            showToast(data.error || "Не удалось запустить Tor", "error");
        }
    } catch (err) {
        showToast("Ошибка запуска Tor: " + err.message, "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = `
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polygon points="5 3 19 12 5 21 5 3"></polygon>
                </svg>
                Запустить Tor в фоне
            `;
        }
    }
}

async function stopStandaloneTor() {
    const btn = document.getElementById("btnStopStandaloneTor");
    if (btn) btn.disabled = true;
    try {
        const res = await fetch("/api/tor/service/stop", { method: "POST" });
        const data = await res.json();
        if (data.success) {
            showToast(data.message || "Фоновый Tor остановлен", "info");
            loadTorStatus();
        }
    } catch (err) {
        showToast("Ошибка остановки Tor: " + err.message, "error");
    } finally {
        if (btn) btn.disabled = false;
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

    const countrySelect = document.getElementById("torExitCountrySelect");
    let exitCountry = countrySelect ? countrySelect.value : "all";
    if (exitCountry === "custom") {
        exitCountry = (document.getElementById("torCustomCountryInput")?.value || "all").trim();
    }
    const strictNodes = document.getElementById("torStrictNodesSwitch")?.checked ?? true;

    try {
        const res = await fetch("/api/tor/config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                enabled: true,
                port: port,
                strict: strict,
                exit_country: exitCountry,
                strict_nodes: strictNodes
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



// ==========================================================================
// Watermark Studio & Video Uniqueizer Module
// ==========================================================================

let currentProcSettings = {
    watermark_enabled: true,
    watermark_text: "@wvids",
    watermark_font: "arial.ttf",
    watermark_size: 36,
    watermark_color: "#ffffff",
    watermark_opacity: 0.9,
    watermark_position: "bottom_right",
    watermark_margin_x: 30,
    watermark_margin_y: 30,
    watermark_box_enabled: true,
    watermark_box_color: "#000000",
    watermark_box_opacity: 0.55,
    watermark_box_border: 10,

    unique_enabled: true,
    unique_metadata: true,
    unique_device_profile: "random",
    unique_duration_mode: "random",
    unique_duration_delta: 1.0,
    unique_visual_hash: true,
    unique_noise: true,
    unique_audio_hash: true,
    unique_preset: "fast",
    unique_crf: 20
};

// Map font filenames to CSS font-family
const FONT_CSS_MAP = {
    "arial.ttf": "Arial, sans-serif",
    "arialbd.ttf": "Arial, sans-serif",
    "impact.ttf": "Impact, 'Arial Black', sans-serif",
    "segoeui.ttf": "'Segoe UI', Roboto, sans-serif",
    "segoeuib.ttf": "'Segoe UI', Roboto, sans-serif",
    "tahoma.ttf": "Tahoma, Verdana, sans-serif",
    "trebuc.ttf": "'Trebuchet MS', 'Lucida Sans', sans-serif",
    "calibri.ttf": "Calibri, Candara, sans-serif",
    "calibrib.ttf": "Calibri, Candara, sans-serif",
    "times.ttf": "'Times New Roman', Times, serif",
    "georgia.ttf": "Georgia, serif",
    "comic.ttf": "'Comic Sans MS', cursive, sans-serif",
    "bahnschrift.ttf": "Bahnschrift, 'Segoe UI', sans-serif"
};

function hexToRgba(hex, alpha) {
    let clean = (hex || "#FFFFFF").replace("#", "").trim();
    if (clean.length === 3) {
        clean = clean.split("").map(c => c + c).join("");
    }
    const r = parseInt(clean.substring(0, 2), 16) || 255;
    const g = parseInt(clean.substring(2, 4), 16) || 255;
    const b = parseInt(clean.substring(4, 6), 16) || 255;
    const a = Math.max(0, Math.min(1, parseFloat(alpha !== undefined ? alpha : 1.0)));
    return `rgba(${r}, ${g}, ${b}, ${a})`;
}

async function loadProcessingSettings() {
    try {
        const res = await fetch("/api/processor/settings");
        const data = await res.json();
        if (!data.success) return;

        if (data.settings) {
            currentProcSettings = Object.assign({}, currentProcSettings, data.settings);
        }

        // Populate Font dropdown if returned
        if (data.fonts && data.fonts.length > 0) {
            const fontSel = document.getElementById("procWmFont");
            if (fontSel) {
                const curVal = currentProcSettings.watermark_font || fontSel.value;
                fontSel.innerHTML = "";
                data.fonts.forEach(f => {
                    const opt = document.createElement("option");
                    opt.value = f.id;
                    opt.textContent = f.name;
                    if (f.id === curVal) opt.selected = true;
                    fontSel.appendChild(opt);
                });
            }
        }

        // Populate Device profile dropdown if returned
        if (data.devices && data.devices.length > 0) {
            const devSel = document.getElementById("procUniqDevice");
            if (devSel) {
                const curDev = currentProcSettings.unique_device_profile || "random";
                devSel.innerHTML = '<option value="random">🎲 Случайный смартфон (Apple / Samsung / Pixel / Sony)</option>';
                data.devices.forEach(d => {
                    const opt = document.createElement("option");
                    opt.value = d.id;
                    opt.textContent = `${d.make} ${d.model} (${d.software || ""})`;
                    if (d.id === curDev) opt.selected = true;
                    devSel.appendChild(opt);
                });
            }
        }

        // Populate form controls from settings
        applySettingsToForm(currentProcSettings);
        updateLivePreview();
        updateBatchTabBadges();
    } catch (err) {
        console.error("Failed to load processing settings:", err);
    }
}

function applySettingsToForm(s) {
    // Mode Switcher
    const mode = s.watermark_mode || "both";
    setWatermarkMode(mode);

    // Watermark Master switch
    const wmEnabled = document.getElementById("procWmEnabled");
    if (wmEnabled) wmEnabled.checked = s.watermark_enabled !== false;

    // Text Watermark
    const wmText = document.getElementById("procWmText");
    if (wmText) wmText.value = s.watermark_text || "@wvids";

    const wmFont = document.getElementById("procWmFont");
    if (wmFont && s.watermark_font) wmFont.value = s.watermark_font;

    const wmSize = document.getElementById("procWmSize");
    const wmSizeBadge = document.getElementById("procWmSizeBadge");
    if (wmSize) {
        wmSize.value = s.watermark_size || 36;
        if (wmSizeBadge) wmSizeBadge.textContent = `${wmSize.value}px`;
    }

    const wmColorPicker = document.getElementById("procWmColorPicker");
    const wmColorText = document.getElementById("procWmColorText");
    if (wmColorPicker) wmColorPicker.value = s.watermark_color || "#ffffff";
    if (wmColorText) wmColorText.value = s.watermark_color || "#ffffff";

    const wmOpacity = document.getElementById("procWmOpacity");
    const wmOpacityBadge = document.getElementById("procWmOpacityBadge");
    if (wmOpacity) {
        wmOpacity.value = s.watermark_opacity !== undefined ? s.watermark_opacity : 0.9;
        if (wmOpacityBadge) wmOpacityBadge.textContent = `${Math.round(wmOpacity.value * 100)}%`;
    }

    // Text Position buttons
    const pos = s.watermark_position || "bottom_right";
    document.querySelectorAll(".pos-btn:not(.media-pos-btn)").forEach(btn => {
        if (btn.dataset.pos === pos) {
            btn.classList.add("active");
        } else {
            btn.classList.remove("active");
        }
    });

    const wmMarginX = document.getElementById("procWmMarginX");
    const wmMarginXBadge = document.getElementById("procWmMarginXBadge");
    if (wmMarginX) {
        wmMarginX.value = s.watermark_margin_x !== undefined ? s.watermark_margin_x : 30;
        if (wmMarginXBadge) wmMarginXBadge.textContent = `${wmMarginX.value}px`;
    }

    const wmMarginY = document.getElementById("procWmMarginY");
    const wmMarginYBadge = document.getElementById("procWmMarginYBadge");
    if (wmMarginY) {
        wmMarginY.value = s.watermark_margin_y !== undefined ? s.watermark_margin_y : 30;
        if (wmMarginYBadge) wmMarginYBadge.textContent = `${wmMarginY.value}px`;
    }

    // Text Background Box
    const wmBoxEnabled = document.getElementById("procWmBoxEnabled");
    if (wmBoxEnabled) wmBoxEnabled.checked = s.watermark_box_enabled !== false;

    const wmBoxColorPicker = document.getElementById("procWmBoxColorPicker");
    const wmBoxColorText = document.getElementById("procWmBoxColorText");
    if (wmBoxColorPicker) wmBoxColorPicker.value = s.watermark_box_color || "#000000";
    if (wmBoxColorText) wmBoxColorText.value = s.watermark_box_color || "#000000";

    const wmBoxOpacity = document.getElementById("procWmBoxOpacity");
    const wmBoxOpacityBadge = document.getElementById("procWmBoxOpacityBadge");
    if (wmBoxOpacity) {
        wmBoxOpacity.value = s.watermark_box_opacity !== undefined ? s.watermark_box_opacity : 0.55;
        if (wmBoxOpacityBadge) wmBoxOpacityBadge.textContent = `${Math.round(wmBoxOpacity.value * 100)}%`;
    }

    const wmBoxBorder = document.getElementById("procWmBoxBorder");
    const wmBoxBorderBadge = document.getElementById("procWmBoxBorderBadge");
    if (wmBoxBorder) {
        wmBoxBorder.value = s.watermark_box_border !== undefined ? s.watermark_box_border : 10;
        if (wmBoxBorderBadge) wmBoxBorderBadge.textContent = `${wmBoxBorder.value}px`;
    }

    // Text Timing
    const wmStartTime = document.getElementById("procWmStartTime");
    const wmStartTimeBadge = document.getElementById("procWmStartTimeBadge");
    if (wmStartTime) {
        wmStartTime.value = s.watermark_start_time !== undefined ? s.watermark_start_time : 0;
        if (wmStartTimeBadge) {
            const v = parseFloat(wmStartTime.value);
            wmStartTimeBadge.textContent = v === 0 ? "0.0с (сразу)" : `${v.toFixed(1)}с`;
        }
    }

    const wmDuration = document.getElementById("procWmDuration");
    const wmDurationBadge = document.getElementById("procWmDurationBadge");
    if (wmDuration) {
        wmDuration.value = s.watermark_duration !== undefined ? s.watermark_duration : 0;
        if (wmDurationBadge) {
            const v = parseFloat(wmDuration.value);
            wmDurationBadge.textContent = v === 0 ? "До конца" : `${v}с`;
        }
    }

    // Media Watermark
    if (s.watermark_media_name && s.watermark_media_path) {
        renderActiveMediaCard(s.watermark_media_name, s.watermark_media_type || "image");
    } else {
        renderActiveMediaCard("", "image");
    }

    const medScale = document.getElementById("procWmMediaScale");
    const medScaleBadge = document.getElementById("procWmMediaScaleBadge");
    if (medScale) {
        medScale.value = s.watermark_media_scale !== undefined ? s.watermark_media_scale : 18;
        if (medScaleBadge) medScaleBadge.textContent = `${medScale.value}%`;
    }

    const medOpacity = document.getElementById("procWmMediaOpacity");
    const medOpacityBadge = document.getElementById("procWmMediaOpacityBadge");
    if (medOpacity) {
        medOpacity.value = s.watermark_media_opacity !== undefined ? s.watermark_media_opacity : 0.85;
        if (medOpacityBadge) medOpacityBadge.textContent = `${Math.round(medOpacity.value * 100)}%`;
    }

    // Media Position buttons
    const medPos = s.watermark_media_position || "top_right";
    document.querySelectorAll(".media-pos-btn").forEach(btn => {
        if (btn.dataset.pos === medPos) {
            btn.classList.add("active");
        } else {
            btn.classList.remove("active");
        }
    });

    const medMarginX = document.getElementById("procWmMediaMarginX");
    const medMarginXBadge = document.getElementById("procWmMediaMarginXBadge");
    if (medMarginX) {
        medMarginX.value = s.watermark_media_margin_x !== undefined ? s.watermark_media_margin_x : 30;
        if (medMarginXBadge) medMarginXBadge.textContent = `${medMarginX.value}px`;
    }

    const medMarginY = document.getElementById("procWmMediaMarginY");
    const medMarginYBadge = document.getElementById("procWmMediaMarginYBadge");
    if (medMarginY) {
        medMarginY.value = s.watermark_media_margin_y !== undefined ? s.watermark_media_margin_y : 30;
        if (medMarginYBadge) medMarginYBadge.textContent = `${medMarginY.value}px`;
    }

    const medStartTime = document.getElementById("procWmMediaStartTime");
    const medStartTimeBadge = document.getElementById("procWmMediaStartTimeBadge");
    if (medStartTime) {
        medStartTime.value = s.watermark_media_start_time !== undefined ? s.watermark_media_start_time : 2.0;
        if (medStartTimeBadge) {
            const v = parseFloat(medStartTime.value);
            medStartTimeBadge.textContent = v === 0 ? "0.0с (сразу)" : `${v.toFixed(1)}с`;
        }
    }

    const medDuration = document.getElementById("procWmMediaDuration");
    const medDurationBadge = document.getElementById("procWmMediaDurationBadge");
    if (medDuration) {
        medDuration.value = s.watermark_media_duration !== undefined ? s.watermark_media_duration : 0;
        if (medDurationBadge) {
            const v = parseFloat(medDuration.value);
            medDurationBadge.textContent = v === 0 ? "До конца" : `${v}с`;
        }
    }

    // Uniqueizer
    const uniqEnabled = document.getElementById("procUniqEnabled");
    if (uniqEnabled) uniqEnabled.checked = s.unique_enabled !== false;

    const uniqDurMode = document.getElementById("procUniqDurMode");
    if (uniqDurMode) uniqDurMode.value = s.unique_duration_mode || "random";

    const uniqDurDelta = document.getElementById("procUniqDurDelta");
    const uniqDurDeltaBadge = document.getElementById("procUniqDurDeltaBadge");
    if (uniqDurDelta) {
        uniqDurDelta.value = s.unique_duration_delta !== undefined ? s.unique_duration_delta : 1.0;
        if (uniqDurDeltaBadge) uniqDurDeltaBadge.textContent = `${parseFloat(uniqDurDelta.value).toFixed(1)}с`;
    }

    const uniqMetadata = document.getElementById("procUniqMetadata");
    if (uniqMetadata) uniqMetadata.checked = s.unique_metadata !== false;

    const uniqDevice = document.getElementById("procUniqDevice");
    if (uniqDevice) uniqDevice.value = s.unique_device_profile || "random";

    const uniqVisualHash = document.getElementById("procUniqVisualHash");
    if (uniqVisualHash) uniqVisualHash.checked = s.unique_visual_hash !== false;

    const uniqNoise = document.getElementById("procUniqNoise");
    if (uniqNoise) uniqNoise.checked = s.unique_noise !== false;

    const uniqAudioHash = document.getElementById("procUniqAudioHash");
    if (uniqAudioHash) uniqAudioHash.checked = s.unique_audio_hash !== false;

    const uniqPreset = document.getElementById("procUniqPreset");
    if (uniqPreset) uniqPreset.value = s.unique_preset || "fast";

    const uniqCrf = document.getElementById("procUniqCrf");
    const uniqCrfBadge = document.getElementById("procUniqCrfBadge");
    if (uniqCrf) {
        uniqCrf.value = s.unique_crf || 20;
        if (uniqCrfBadge) uniqCrfBadge.textContent = uniqCrf.value;
    }
}

function getActiveWatermarkMode() {
    const activeBtn = document.querySelector(".wm-mode-btn.active");
    return activeBtn ? activeBtn.dataset.mode : (currentProcSettings.watermark_mode || "both");
}

function setWatermarkMode(mode) {
    document.querySelectorAll(".wm-mode-btn").forEach(btn => {
        if (btn.dataset.mode === mode) btn.classList.add("active");
        else btn.classList.remove("active");
    });

    const textSec = document.getElementById("wmTextSection");
    const mediaSec = document.getElementById("wmMediaSection");
    const wmEnabledToggle = document.getElementById("procWmEnabled");

    if (mode === "none") {
        if (wmEnabledToggle) wmEnabledToggle.checked = false;
        if (textSec) textSec.style.opacity = "0.45";
        if (mediaSec) mediaSec.style.opacity = "0.45";
    } else {
        if (wmEnabledToggle) wmEnabledToggle.checked = true;
        if (textSec) {
            textSec.style.display = (mode === "text" || mode === "both") ? "block" : "none";
            textSec.style.opacity = "1";
        }
        if (mediaSec) {
            mediaSec.style.display = (mode === "media" || mode === "both") ? "block" : "none";
            mediaSec.style.opacity = "1";
        }
    }
    updateLivePreview();
}

function renderActiveMediaCard(fileName, mediaType) {
    const activeCard = document.getElementById("wmMediaActiveCard");
    const dropzone = document.getElementById("wmMediaDropzone");
    const nameEl = document.getElementById("wmMediaFileName");
    const typeBadge = document.getElementById("wmMediaTypeBadge");
    const thumbImg = document.getElementById("wmMediaThumbImg");
    const thumbVid = document.getElementById("wmMediaThumbVideo");

    if (!fileName) {
        if (activeCard) activeCard.style.display = "none";
        if (dropzone) dropzone.style.display = "flex";
        return;
    }

    if (activeCard) activeCard.style.display = "flex";
    if (dropzone) dropzone.style.display = "none";
    if (nameEl) nameEl.textContent = fileName;

    const isVideo = mediaType === "video" || /\.(mp4|mov|webm|mkv|avi|m4v)$/i.test(fileName);
    if (typeBadge) {
        typeBadge.textContent = isVideo ? "Видео" : "Изображение";
        typeBadge.style.color = isVideo ? "var(--accent-purple)" : "var(--accent-cyan)";
    }

    const url = `/api/processor/watermark-media-file?t=${Date.now()}`;
    if (isVideo) {
        if (thumbImg) thumbImg.style.display = "none";
        if (thumbVid) {
            thumbVid.src = url;
            thumbVid.style.display = "block";
            thumbVid.play().catch(() => {});
        }
    } else {
        if (thumbVid) thumbVid.style.display = "none";
        if (thumbImg) {
            thumbImg.src = url;
            thumbImg.style.display = "block";
        }
    }
}

async function uploadWatermarkMedia(file) {
    if (!file) return;
    const validExts = [".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"];
    const fileName = file.name.toLowerCase();
    if (!validExts.some(ext => fileName.endsWith(ext))) {
        showToast("Неподдерживаемый формат! Подходят изображения (PNG, JPG, WEBP, GIF) и видео (MP4, MOV, WEBM)", "error");
        return;
    }

    const dropzone = document.getElementById("wmMediaDropzone");
    const origHtml = dropzone ? dropzone.innerHTML : "";
    if (dropzone) {
        dropzone.innerHTML = '<div style="padding: 1.5rem; color: var(--accent-cyan); font-weight: 600;">⏳ Загрузка медиа-вотермарка в панель...</div>';
    }

    const fd = new FormData();
    fd.append("file", file);

    try {
        const res = await fetch("/api/processor/upload-watermark-media", {
            method: "POST",
            body: fd
        });
        const data = await res.json();
        if (data.success) {
            currentProcSettings = data.settings;
            renderActiveMediaCard(data.media_name, data.media_type);
            showToast(data.message || "Медиа-вотермарк успешно загружен!", "success");
            updateLivePreview();
        } else {
            showToast(data.detail || data.error || "Ошибка загрузки файла", "error");
            if (dropzone) dropzone.innerHTML = origHtml;
            bindDropzoneFileInput();
        }
    } catch (err) {
        showToast(`Ошибка сети при загрузке: ${err.message}`, "error");
        if (dropzone) dropzone.innerHTML = origHtml;
        bindDropzoneFileInput();
    }
}

async function deleteWatermarkMedia() {
    try {
        const res = await fetch("/api/processor/watermark-media", { method: "DELETE" });
        const data = await res.json();
        if (data.success) {
            currentProcSettings = data.settings;
            renderActiveMediaCard("", "image");
            updateLivePreview();
            showToast("Медиа-вотермарк успешно удален", "info");
        }
    } catch (err) {
        showToast(`Ошибка удаления медиа-вотермарка: ${err.message}`, "error");
    }
}

function bindDropzoneFileInput() {
    const fileInput = document.getElementById("procWmMediaFileInput");
    if (fileInput) {
        fileInput.onchange = (e) => {
            if (e.target.files && e.target.files[0]) {
                uploadWatermarkMedia(e.target.files[0]);
            }
        };
    }
}

// Global preset setters
window.setTextStartTimePreset = function(sec) {
    const el = document.getElementById("procWmStartTime");
    const badge = document.getElementById("procWmStartTimeBadge");
    if (el) {
        el.value = sec;
        if (badge) badge.textContent = sec === 0 ? "0.0с (сразу)" : `${sec.toFixed(1)}с`;
        updateLivePreview();
    }
};

window.setMediaStartTimePreset = function(sec) {
    const el = document.getElementById("procWmMediaStartTime");
    const badge = document.getElementById("procWmMediaStartTimeBadge");
    if (el) {
        el.value = sec;
        if (badge) badge.textContent = sec === 0 ? "0.0с (сразу)" : `${sec.toFixed(1)}с`;
        updateLivePreview();
    }
};

function collectSettingsFromForm() {
    const activeTextPosBtn = document.querySelector(".pos-btn:not(.media-pos-btn).active");
    const textPosition = activeTextPosBtn ? activeTextPosBtn.dataset.pos : "bottom_right";

    const activeMedPosBtn = document.querySelector(".media-pos-btn.active");
    const mediaPosition = activeMedPosBtn ? activeMedPosBtn.dataset.pos : "top_right";

    return {
        // Watermark General
        watermark_enabled: document.getElementById("procWmEnabled")?.checked ?? true,
        watermark_mode: getActiveWatermarkMode(),

        // Text Watermark
        watermark_text: document.getElementById("procWmText")?.value.trim() || "@wvids",
        watermark_font: document.getElementById("procWmFont")?.value || "arial.ttf",
        watermark_size: parseInt(document.getElementById("procWmSize")?.value || "36", 10),
        watermark_color: document.getElementById("procWmColorPicker")?.value || "#ffffff",
        watermark_opacity: parseFloat(document.getElementById("procWmOpacity")?.value || "0.9"),
        watermark_position: textPosition,
        watermark_margin_x: parseInt(document.getElementById("procWmMarginX")?.value || "30", 10),
        watermark_margin_y: parseInt(document.getElementById("procWmMarginY")?.value || "30", 10),
        watermark_box_enabled: document.getElementById("procWmBoxEnabled")?.checked ?? true,
        watermark_box_color: document.getElementById("procWmBoxColorPicker")?.value || "#000000",
        watermark_box_opacity: parseFloat(document.getElementById("procWmBoxOpacity")?.value || "0.55"),
        watermark_box_border: parseInt(document.getElementById("procWmBoxBorder")?.value || "10", 10),
        watermark_start_time: parseFloat(document.getElementById("procWmStartTime")?.value || "0"),
        watermark_duration: parseFloat(document.getElementById("procWmDuration")?.value || "0"),

        // Media Watermark
        watermark_media_path: currentProcSettings.watermark_media_path || "",
        watermark_media_name: currentProcSettings.watermark_media_name || "",
        watermark_media_type: currentProcSettings.watermark_media_type || "image",
        watermark_media_scale: parseInt(document.getElementById("procWmMediaScale")?.value || "18", 10),
        watermark_media_opacity: parseFloat(document.getElementById("procWmMediaOpacity")?.value || "0.85"),
        watermark_media_position: mediaPosition,
        watermark_media_margin_x: parseInt(document.getElementById("procWmMediaMarginX")?.value || "30", 10),
        watermark_media_margin_y: parseInt(document.getElementById("procWmMediaMarginY")?.value || "30", 10),
        watermark_media_start_time: parseFloat(document.getElementById("procWmMediaStartTime")?.value || "2.0"),
        watermark_media_duration: parseFloat(document.getElementById("procWmMediaDuration")?.value || "0"),

        // Uniqueizer
        unique_enabled: document.getElementById("procUniqEnabled")?.checked ?? true,
        unique_metadata: document.getElementById("procUniqMetadata")?.checked ?? true,
        unique_device_profile: document.getElementById("procUniqDevice")?.value || "random",
        unique_duration_mode: document.getElementById("procUniqDurMode")?.value || "random",
        unique_duration_delta: parseFloat(document.getElementById("procUniqDurDelta")?.value || "1.0"),
        unique_visual_hash: document.getElementById("procUniqVisualHash")?.checked ?? true,
        unique_noise: document.getElementById("procUniqNoise")?.checked ?? true,
        unique_audio_hash: document.getElementById("procUniqAudioHash")?.checked ?? true,
        unique_preset: document.getElementById("procUniqPreset")?.value || "fast",
        unique_crf: parseInt(document.getElementById("procUniqCrf")?.value || "20", 10)
    };
}

function updateLivePreview() {
    const previewTextEl = document.getElementById("previewWatermark");
    const previewMedEl = document.getElementById("previewMediaWatermark");
    const previewMedImg = document.getElementById("previewMediaImg");
    const previewMedVid = document.getElementById("previewMediaVideo");

    if (!previewTextEl) return;

    const s = collectSettingsFromForm();
    const mode = s.watermark_mode || "both";
    const isMasterOn = s.watermark_enabled && mode !== "none";

    // 1. TEXT WATERMARK PREVIEW
    const showText = isMasterOn && (mode === "text" || mode === "both");
    if (!showText) {
        previewTextEl.style.display = "none";
    } else {
        previewTextEl.style.display = "inline-block";

        let previewText = s.watermark_text || "@wvids";
        const headerUsername = document.getElementById("activeAccountUsername")?.textContent || "@channel";
        previewText = previewText
            .replace("{username}", headerUsername)
            .replace("{channel}", headerUsername)
            .replace("{title}", "Мое Видео")
            .replace("{date}", new Date().toISOString().slice(0, 10));

        previewTextEl.textContent = previewText;

        const fontKey = s.watermark_font || "arial.ttf";
        const fontCss = FONT_CSS_MAP[fontKey] || "Arial, sans-serif";
        const isBold = fontKey.includes("bd") || fontKey.includes("b.ttf") || fontKey === "impact.ttf";

        previewTextEl.style.fontFamily = fontCss;
        previewTextEl.style.fontWeight = isBold ? "700" : "500";
        previewTextEl.style.letterSpacing = (fontKey === "impact.ttf") ? "0.5px" : "normal";

        const previewFontSize = Math.max(12, Math.round(s.watermark_size * 0.45));
        previewTextEl.style.fontSize = `${previewFontSize}px`;
        previewTextEl.style.color = hexToRgba(s.watermark_color, s.watermark_opacity);

        if (s.watermark_box_enabled) {
            const previewPad = Math.max(3, Math.round(s.watermark_box_border * 0.45));
            previewTextEl.style.backgroundColor = hexToRgba(s.watermark_box_color, s.watermark_box_opacity);
            previewTextEl.style.padding = `${previewPad}px ${previewPad + 2}px`;
            previewTextEl.style.borderRadius = "4px";
        } else {
            previewTextEl.style.backgroundColor = "transparent";
            previewTextEl.style.padding = "0";
        }

        const scaledMx = Math.max(6, Math.round(s.watermark_margin_x * 0.4));
        const scaledMy = Math.max(6, Math.round(s.watermark_margin_y * 0.4));

        previewTextEl.style.top = "auto";
        previewTextEl.style.bottom = "auto";
        previewTextEl.style.left = "auto";
        previewTextEl.style.right = "auto";
        previewTextEl.style.transform = "none";

        switch (s.watermark_position) {
            case "top_left":
                previewTextEl.style.top = `${scaledMy}px`;
                previewTextEl.style.left = `${scaledMx}px`;
                break;
            case "top_center":
                previewTextEl.style.top = `${scaledMy}px`;
                previewTextEl.style.left = "50%";
                previewTextEl.style.transform = "translateX(-50%)";
                break;
            case "top_right":
                previewTextEl.style.top = `${scaledMy}px`;
                previewTextEl.style.right = `${scaledMx}px`;
                break;
            case "center":
                previewTextEl.style.top = "50%";
                previewTextEl.style.left = "50%";
                previewTextEl.style.transform = "translate(-50%, -50%)";
                break;
            case "bottom_left":
                previewTextEl.style.bottom = `${scaledMy}px`;
                previewTextEl.style.left = `${scaledMx}px`;
                break;
            case "bottom_center":
                previewTextEl.style.bottom = `${scaledMy}px`;
                previewTextEl.style.left = "50%";
                previewTextEl.style.transform = "translateX(-50%)";
                break;
            case "bottom_right":
            default:
                previewTextEl.style.bottom = `${scaledMy}px`;
                previewTextEl.style.right = `${scaledMx}px`;
                break;
        }
    }

    // 2. MEDIA WATERMARK PREVIEW
    const hasMediaFile = Boolean(currentProcSettings.watermark_media_name && currentProcSettings.watermark_media_path);
    const showMedia = isMasterOn && (mode === "media" || mode === "both") && hasMediaFile;

    if (!previewMedEl) return;

    if (!showMedia) {
        previewMedEl.style.display = "none";
    } else {
        previewMedEl.style.display = "flex";
        previewMedEl.style.width = `${Math.max(8, Math.min(60, s.watermark_media_scale))}%`;
        previewMedEl.style.opacity = `${s.watermark_media_opacity}`;

        const isVideo = currentProcSettings.watermark_media_type === "video" || /\.(mp4|mov|webm|mkv|avi|m4v)$/i.test(currentProcSettings.watermark_media_name || "");
        const mediaUrl = `/api/processor/watermark-media-file?t=${Date.now()}`;

        if (isVideo) {
            if (previewMedImg) previewMedImg.style.display = "none";
            if (previewMedVid) {
                if (previewMedVid.getAttribute("data-src") !== mediaUrl) {
                    previewMedVid.src = mediaUrl;
                    previewMedVid.setAttribute("data-src", mediaUrl);
                }
                previewMedVid.style.display = "block";
                previewMedVid.play().catch(() => {});
            }
        } else {
            if (previewMedVid) previewMedVid.style.display = "none";
            if (previewMedImg) {
                if (previewMedImg.getAttribute("data-src") !== mediaUrl) {
                    previewMedImg.src = mediaUrl;
                    previewMedImg.setAttribute("data-src", mediaUrl);
                }
                previewMedImg.style.display = "block";
            }
        }

        const medScaledMx = Math.max(6, Math.round(s.watermark_media_margin_x * 0.4));
        const medScaledMy = Math.max(6, Math.round(s.watermark_media_margin_y * 0.4));

        previewMedEl.style.top = "auto";
        previewMedEl.style.bottom = "auto";
        previewMedEl.style.left = "auto";
        previewMedEl.style.right = "auto";
        previewMedEl.style.transform = "none";

        switch (s.watermark_media_position) {
            case "top_left":
                previewMedEl.style.top = `${medScaledMy}px`;
                previewMedEl.style.left = `${medScaledMx}px`;
                break;
            case "top_center":
                previewMedEl.style.top = `${medScaledMy}px`;
                previewMedEl.style.left = "50%";
                previewMedEl.style.transform = "translateX(-50%)";
                break;
            case "top_right":
                previewMedEl.style.top = `${medScaledMy}px`;
                previewMedEl.style.right = `${medScaledMx}px`;
                break;
            case "center":
                previewMedEl.style.top = "50%";
                previewMedEl.style.left = "50%";
                previewMedEl.style.transform = "translate(-50%, -50%)";
                break;
            case "bottom_left":
                previewMedEl.style.bottom = `${medScaledMy}px`;
                previewMedEl.style.left = `${medScaledMx}px`;
                break;
            case "bottom_center":
                previewMedEl.style.bottom = `${medScaledMy}px`;
                previewMedEl.style.left = "50%";
                previewMedEl.style.transform = "translateX(-50%)";
                break;
            case "bottom_right":
            default:
                previewMedEl.style.bottom = `${medScaledMy}px`;
                previewMedEl.style.right = `${medScaledMx}px`;
                break;
        }
    }
}

function updateBatchTabBadges() {
    const wmBadge = document.getElementById("batchWatermarkBadge");
    const uniqBadge = document.getElementById("batchUniqueBadge");

    if (wmBadge) {
        const mode = currentProcSettings.watermark_mode || "both";
        if (currentProcSettings.watermark_enabled && mode !== "none") {
            wmBadge.className = "proc-status-badge on";
            let modeTitle = "Текст";
            if (mode === "both") modeTitle = "Все вместе (Текст + Медиа)";
            else if (mode === "media") modeTitle = "Медиа (Фото/Видео)";
            wmBadge.textContent = `🎨 Водяной знак: Активен (${modeTitle})`;
        } else {
            wmBadge.className = "proc-status-badge off";
            wmBadge.textContent = "🎨 Водяной знак: Отключен";
        }
    }

    if (uniqBadge) {
        if (currentProcSettings.unique_enabled) {
            uniqBadge.className = "proc-status-badge on";
            let durLabel = "Рандом";
            if (currentProcSettings.unique_duration_mode === "add") durLabel = "+Хвост";
            else if (currentProcSettings.unique_duration_mode === "trim") durLabel = "-Подрезка";
            else if (currentProcSettings.unique_duration_mode === "speed") durLabel = "Скорость";
            else if (currentProcSettings.unique_duration_mode === "none") durLabel = "Без сдвига";
            uniqBadge.textContent = `🛡️ Уникализация: Активна (${durLabel})`;
        } else {
            uniqBadge.className = "proc-status-badge off";
            uniqBadge.textContent = "🛡️ Уникализация: Отключена";
        }
    }
}

function initProcessingStudio() {
    // 1. Watermark Mode Selector Buttons
    document.querySelectorAll(".wm-mode-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            const mode = btn.dataset.mode;
            setWatermarkMode(mode);
        });
    });

    // 2. Text Position Buttons Click
    document.querySelectorAll(".pos-btn:not(.media-pos-btn)").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".pos-btn:not(.media-pos-btn)").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            updateLivePreview();
        });
    });

    // 3. Media Position Buttons Click
    document.querySelectorAll(".media-pos-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".media-pos-btn").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            updateLivePreview();
        });
    });

    // 4. Drag & Drop Media Upload Events
    const dropzone = document.getElementById("wmMediaDropzone");
    const fileInput = document.getElementById("procWmMediaFileInput");

    if (dropzone) {
        dropzone.addEventListener("click", () => {
            const inp = document.getElementById("procWmMediaFileInput");
            if (inp) inp.click();
        });

        dropzone.addEventListener("dragover", (e) => {
            e.preventDefault();
            e.stopPropagation();
            dropzone.classList.add("dragover");
        });

        dropzone.addEventListener("dragleave", (e) => {
            e.preventDefault();
            e.stopPropagation();
            dropzone.classList.remove("dragover");
        });

        dropzone.addEventListener("drop", (e) => {
            e.preventDefault();
            e.stopPropagation();
            dropzone.classList.remove("dragover");
            if (e.dataTransfer.files && e.dataTransfer.files[0]) {
                uploadWatermarkMedia(e.dataTransfer.files[0]);
            }
        });
    }

    bindDropzoneFileInput();

    // 5. Delete Watermark Media Button
    const btnDeleteMedia = document.getElementById("btnDeleteWmMedia");
    if (btnDeleteMedia) {
        btnDeleteMedia.addEventListener("click", deleteWatermarkMedia);
    }

    // 6. Text controls
    const wmText = document.getElementById("procWmText");
    if (wmText) wmText.addEventListener("input", updateLivePreview);

    const wmFont = document.getElementById("procWmFont");
    if (wmFont) wmFont.addEventListener("change", updateLivePreview);

    const wmSize = document.getElementById("procWmSize");
    const wmSizeBadge = document.getElementById("procWmSizeBadge");
    if (wmSize) {
        wmSize.addEventListener("input", () => {
            if (wmSizeBadge) wmSizeBadge.textContent = `${wmSize.value}px`;
            updateLivePreview();
        });
    }

    const wmColorPicker = document.getElementById("procWmColorPicker");
    const wmColorText = document.getElementById("procWmColorText");
    if (wmColorPicker && wmColorText) {
        wmColorPicker.addEventListener("input", () => {
            wmColorText.value = wmColorPicker.value;
            updateLivePreview();
        });
        wmColorText.addEventListener("input", () => {
            if (/^#[0-9A-Fa-f]{6}$/.test(wmColorText.value)) {
                wmColorPicker.value = wmColorText.value;
            }
            updateLivePreview();
        });
    }

    const wmOpacity = document.getElementById("procWmOpacity");
    const wmOpacityBadge = document.getElementById("procWmOpacityBadge");
    if (wmOpacity) {
        wmOpacity.addEventListener("input", () => {
            if (wmOpacityBadge) wmOpacityBadge.textContent = `${Math.round(wmOpacity.value * 100)}%`;
            updateLivePreview();
        });
    }

    const wmMarginX = document.getElementById("procWmMarginX");
    const wmMarginXBadge = document.getElementById("procWmMarginXBadge");
    if (wmMarginX) {
        wmMarginX.addEventListener("input", () => {
            if (wmMarginXBadge) wmMarginXBadge.textContent = `${wmMarginX.value}px`;
            updateLivePreview();
        });
    }

    const wmMarginY = document.getElementById("procWmMarginY");
    const wmMarginYBadge = document.getElementById("procWmMarginYBadge");
    if (wmMarginY) {
        wmMarginY.addEventListener("input", () => {
            if (wmMarginYBadge) wmMarginYBadge.textContent = `${wmMarginY.value}px`;
            updateLivePreview();
        });
    }

    const wmBoxEnabled = document.getElementById("procWmBoxEnabled");
    if (wmBoxEnabled) {
        wmBoxEnabled.addEventListener("change", () => {
            const group = document.getElementById("wmBoxSettingsGroup");
            if (group) group.style.display = wmBoxEnabled.checked ? "block" : "none";
            updateLivePreview();
        });
    }

    const wmBoxColorPicker = document.getElementById("procWmBoxColorPicker");
    const wmBoxColorText = document.getElementById("procWmBoxColorText");
    if (wmBoxColorPicker && wmBoxColorText) {
        wmBoxColorPicker.addEventListener("input", () => {
            wmBoxColorText.value = wmBoxColorPicker.value;
            updateLivePreview();
        });
        wmBoxColorText.addEventListener("input", () => {
            if (/^#[0-9A-Fa-f]{6}$/.test(wmBoxColorText.value)) {
                wmBoxColorPicker.value = wmBoxColorText.value;
            }
            updateLivePreview();
        });
    }

    const wmBoxOpacity = document.getElementById("procWmBoxOpacity");
    const wmBoxOpacityBadge = document.getElementById("procWmBoxOpacityBadge");
    if (wmBoxOpacity) {
        wmBoxOpacity.addEventListener("input", () => {
            if (wmBoxOpacityBadge) wmBoxOpacityBadge.textContent = `${Math.round(wmBoxOpacity.value * 100)}%`;
            updateLivePreview();
        });
    }

    const wmBoxBorder = document.getElementById("procWmBoxBorder");
    const wmBoxBorderBadge = document.getElementById("procWmBoxBorderBadge");
    if (wmBoxBorder) {
        wmBoxBorder.addEventListener("input", () => {
            if (wmBoxBorderBadge) wmBoxBorderBadge.textContent = `${wmBoxBorder.value}px`;
            updateLivePreview();
        });
    }

    const wmStartTime = document.getElementById("procWmStartTime");
    const wmStartTimeBadge = document.getElementById("procWmStartTimeBadge");
    if (wmStartTime) {
        wmStartTime.addEventListener("input", () => {
            const v = parseFloat(wmStartTime.value);
            if (wmStartTimeBadge) wmStartTimeBadge.textContent = v === 0 ? "0.0с (сразу)" : `${v.toFixed(1)}с`;
            updateLivePreview();
        });
    }

    const wmDuration = document.getElementById("procWmDuration");
    const wmDurationBadge = document.getElementById("procWmDurationBadge");
    if (wmDuration) {
        wmDuration.addEventListener("input", () => {
            const v = parseFloat(wmDuration.value);
            if (wmDurationBadge) wmDurationBadge.textContent = v === 0 ? "До конца" : `${v}с`;
            updateLivePreview();
        });
    }

    // 7. Media Watermark Slider Controls
    const medScale = document.getElementById("procWmMediaScale");
    const medScaleBadge = document.getElementById("procWmMediaScaleBadge");
    if (medScale) {
        medScale.addEventListener("input", () => {
            if (medScaleBadge) medScaleBadge.textContent = `${medScale.value}%`;
            updateLivePreview();
        });
    }

    const medOpacity = document.getElementById("procWmMediaOpacity");
    const medOpacityBadge = document.getElementById("procWmMediaOpacityBadge");
    if (medOpacity) {
        medOpacity.addEventListener("input", () => {
            if (medOpacityBadge) medOpacityBadge.textContent = `${Math.round(medOpacity.value * 100)}%`;
            updateLivePreview();
        });
    }

    const medMarginX = document.getElementById("procWmMediaMarginX");
    const medMarginXBadge = document.getElementById("procWmMediaMarginXBadge");
    if (medMarginX) {
        medMarginX.addEventListener("input", () => {
            if (medMarginXBadge) medMarginXBadge.textContent = `${medMarginX.value}px`;
            updateLivePreview();
        });
    }

    const medMarginY = document.getElementById("procWmMediaMarginY");
    const medMarginYBadge = document.getElementById("procWmMediaMarginYBadge");
    if (medMarginY) {
        medMarginY.addEventListener("input", () => {
            if (medMarginYBadge) medMarginYBadge.textContent = `${medMarginY.value}px`;
            updateLivePreview();
        });
    }

    const medStartTime = document.getElementById("procWmMediaStartTime");
    const medStartTimeBadge = document.getElementById("procWmMediaStartTimeBadge");
    if (medStartTime) {
        medStartTime.addEventListener("input", () => {
            const v = parseFloat(medStartTime.value);
            if (medStartTimeBadge) medStartTimeBadge.textContent = v === 0 ? "0.0с (сразу)" : `${v.toFixed(1)}с`;
            updateLivePreview();
        });
    }

    const medDuration = document.getElementById("procWmMediaDuration");
    const medDurationBadge = document.getElementById("procWmMediaDurationBadge");
    if (medDuration) {
        medDuration.addEventListener("input", () => {
            const v = parseFloat(medDuration.value);
            if (medDurationBadge) medDurationBadge.textContent = v === 0 ? "До конца" : `${v}с`;
            updateLivePreview();
        });
    }

    // Preset buttons click listeners
    const pText0 = document.getElementById("btnPresetText0");
    const pText2 = document.getElementById("btnPresetText2");
    const pText5 = document.getElementById("btnPresetText5");
    if (pText0) pText0.addEventListener("click", () => window.setTextStartTimePreset(0));
    if (pText2) pText2.addEventListener("click", () => window.setTextStartTimePreset(2));
    if (pText5) pText5.addEventListener("click", () => window.setTextStartTimePreset(5));

    const pMed0 = document.getElementById("btnPresetMedia0");
    const pMed2 = document.getElementById("btnPresetMedia2");
    const pMed5 = document.getElementById("btnPresetMedia5");
    if (pMed0) pMed0.addEventListener("click", () => window.setMediaStartTimePreset(0));
    if (pMed2) pMed2.addEventListener("click", () => window.setMediaStartTimePreset(2));
    if (pMed5) pMed5.addEventListener("click", () => window.setMediaStartTimePreset(5));

    // 8. Main toggles
    const procWmEnabled = document.getElementById("procWmEnabled");
    if (procWmEnabled) {
        procWmEnabled.addEventListener("change", () => {
            const body = document.getElementById("wmSettingsBody");
            if (body) body.style.opacity = procWmEnabled.checked ? "1" : "0.5";
            updateLivePreview();
        });
    }

    const procUniqEnabled = document.getElementById("procUniqEnabled");
    if (procUniqEnabled) {
        procUniqEnabled.addEventListener("change", () => {
            const body = document.getElementById("uniqSettingsBody");
            if (body) body.style.opacity = procUniqEnabled.checked ? "1" : "0.5";
        });
    }

    // 9. Uniqueizer slider badges
    const uniqDurDelta = document.getElementById("procUniqDurDelta");
    const uniqDurDeltaBadge = document.getElementById("procUniqDurDeltaBadge");
    if (uniqDurDelta) {
        uniqDurDelta.addEventListener("input", () => {
            if (uniqDurDeltaBadge) uniqDurDeltaBadge.textContent = `${parseFloat(uniqDurDelta.value).toFixed(1)}с`;
        });
    }

    const uniqCrf = document.getElementById("procUniqCrf");
    const uniqCrfBadge = document.getElementById("procUniqCrfBadge");
    if (uniqCrf) {
        uniqCrf.addEventListener("input", () => {
            if (uniqCrfBadge) uniqCrfBadge.textContent = uniqCrf.value;
        });
    }

    // 10. Preview Background Switchers
    const stage = document.getElementById("previewStage");
    const bgCinema = document.getElementById("btnPreviewBgCinema");
    const bgDark = document.getElementById("btnPreviewBgDark");
    const bgLight = document.getElementById("btnPreviewBgLight");

    if (stage) {
        if (bgCinema) bgCinema.addEventListener("click", () => stage.className = "preview-stage bg-cinema");
        if (bgDark) bgDark.addEventListener("click", () => stage.className = "preview-stage bg-dark");
        if (bgLight) bgLight.addEventListener("click", () => stage.className = "preview-stage bg-light");
    }

    // 11. Timing Simulator Test Button
    const btnPlayTiming = document.getElementById("btnPreviewPlayTiming");
    if (btnPlayTiming) {
        btnPlayTiming.addEventListener("click", () => {
            runTimingSimulation();
        });
    }

    // 12. Save Settings Button
    const btnSave = document.getElementById("btnSaveProcSettings");
    if (btnSave) {
        btnSave.addEventListener("click", async () => {
            const s = collectSettingsFromForm();
            btnSave.disabled = true;
            btnSave.textContent = "Сохранение...";
            try {
                const res = await fetch("/api/processor/settings", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ settings: s })
                });
                const data = await res.json();
                if (data.success) {
                    currentProcSettings = data.settings || s;
                    updateBatchTabBadges();
                    showToast(data.message || "Настройки сохранены!", "success");
                } else {
                    showToast(data.error || "Ошибка сохранения настроек", "error");
                }
            } catch (err) {
                showToast(`Ошибка сети: ${err.message}`, "error");
            } finally {
                btnSave.disabled = false;
                btnSave.textContent = "💾 Сохранить настройки";
            }
        });
    }

    // 13. Reset Settings Button
    const btnReset = document.getElementById("btnResetProcSettings");
    if (btnReset) {
        btnReset.addEventListener("click", () => {
            const defaultSettings = {
                watermark_enabled: true,
                watermark_mode: "both",
                watermark_text: "@wvids",
                watermark_font: "arial.ttf",
                watermark_size: 36,
                watermark_color: "#ffffff",
                watermark_opacity: 0.9,
                watermark_position: "bottom_right",
                watermark_margin_x: 30,
                watermark_margin_y: 30,
                watermark_box_enabled: true,
                watermark_box_color: "#000000",
                watermark_box_opacity: 0.55,
                watermark_box_border: 10,
                watermark_start_time: 0.0,
                watermark_duration: 0.0,

                watermark_media_path: "",
                watermark_media_name: "",
                watermark_media_type: "image",
                watermark_media_scale: 18,
                watermark_media_opacity: 0.85,
                watermark_media_position: "top_right",
                watermark_media_margin_x: 30,
                watermark_media_margin_y: 30,
                watermark_media_start_time: 2.0,
                watermark_media_duration: 0.0,

                unique_enabled: true,
                unique_metadata: true,
                unique_device_profile: "random",
                unique_duration_mode: "random",
                unique_duration_delta: 1.0,
                unique_visual_hash: true,
                unique_noise: true,
                unique_audio_hash: true,
                unique_preset: "fast",
                unique_crf: 20
            };
            applySettingsToForm(defaultSettings);
            updateLivePreview();
            showToast("Настройки сброшены к стандартным", "info");
        });
    }

    // 14. Quick jump from tab-upload button
    const btnGoToProc = document.getElementById("btnGoToProcessing");
    if (btnGoToProc) {
        btnGoToProc.addEventListener("click", () => {
            const targetBtn = document.querySelector('.tab-btn[data-tab="tab-processing"]');
            if (targetBtn) targetBtn.click();
        });
    }

    // 15. Run Test Benchmark Runner
    const btnRunTest = document.getElementById("btnRunTestProc");
    if (btnRunTest) {
        btnRunTest.addEventListener("click", async () => {
            btnRunTest.disabled = true;
            btnRunTest.innerHTML = '<span class="status-indicator warning"></span> Рендеринг теста через FFmpeg...';

            const resultCard = document.getElementById("testProcResultCard");
            if (resultCard) resultCard.style.display = "none";

            try {
                // Auto-save current settings before running test so benchmark reflects live options
                const cur = collectSettingsFromForm();
                await fetch("/api/processor/settings", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ settings: cur })
                });

                const res = await fetch("/api/processor/test", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({})
                });
                const data = await res.json();

                if (data.success) {
                    if (resultCard) {
                        resultCard.style.display = "block";
                        const timeBadge = document.getElementById("testProcTimeBadge");
                        const phashDiff = document.getElementById("testProcPhashDiff");
                        const durEl = document.getElementById("testProcDuration");
                        const devEl = document.getElementById("testProcDevice");
                        const sizeEl = document.getElementById("testProcSize");

                        if (timeBadge) timeBadge.textContent = `${data.elapsed_seconds}с`;
                        if (phashDiff) phashDiff.textContent = `${data.phash_difference_pct}% (дистанция: ${data.phash_hamming_distance} бит)`;
                        if (durEl) {
                            const deltaSign = data.duration_delta >= 0 ? `+${data.duration_delta}` : `${data.duration_delta}`;
                            durEl.textContent = `${data.orig_duration}s ➔ ${data.new_duration}s (${deltaSign}s)`;
                        }
                        if (devEl) devEl.textContent = data.device_profile || "iPhone 15 Pro";
                        if (sizeEl) {
                            const origKb = Math.round(data.orig_size_bytes / 1024);
                            const newKb = Math.round(data.new_size_bytes / 1024);
                            sizeEl.textContent = `${origKb} KB ➔ ${newKb} KB`;
                        }
                    }
                    showToast(`Тест завершен за ${data.elapsed_seconds}с! Сдвиг pHash: ${data.phash_difference_pct}%`, "success");
                } else {
                    showToast(data.detail || data.error || "Ошибка выполнения теста FFmpeg", "error");
                }
            } catch (err) {
                showToast(`Ошибка запуска теста: ${err.message}`, "error");
            } finally {
                btnRunTest.disabled = false;
                btnRunTest.innerHTML = `
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <polygon points="5 3 19 12 5 21 5 3"></polygon>
                    </svg>
                    Запустить тестовый прогон FFmpeg
                `;
            }
        });
    }

    // Trigger initial preview render
    updateLivePreview();
}

let timingSimInterval = null;
function runTimingSimulation() {
    if (timingSimInterval) {
        clearInterval(timingSimInterval);
        timingSimInterval = null;
    }

    const previewTextEl = document.getElementById("previewWatermark");
    const previewMedEl = document.getElementById("previewMediaWatermark");
    const btnPlay = document.getElementById("btnPreviewPlayTiming");

    const s = collectSettingsFromForm();
    const mode = s.watermark_mode || "both";
    const textStart = s.watermark_start_time || 0;
    const medStart = s.watermark_media_start_time || 0;

    let simSec = 0.0;
    const maxSec = Math.max(7.0, Math.max(textStart, medStart) + 2.5);

    if (btnPlay) {
        btnPlay.disabled = true;
        btnPlay.textContent = `⏱️ 0.0с`;
    }

    // Hide initially if start time is delayed
    if (previewTextEl && (mode === "text" || mode === "both")) {
        previewTextEl.style.opacity = textStart > 0 ? "0" : `${s.watermark_opacity}`;
        previewTextEl.style.transition = "opacity 0.25s ease";
    }
    if (previewMedEl && (mode === "media" || mode === "both")) {
        previewMedEl.style.opacity = medStart > 0 ? "0" : `${s.watermark_media_opacity}`;
        previewMedEl.style.transition = "opacity 0.25s ease";
    }

    timingSimInterval = setInterval(() => {
        simSec += 0.2;
        if (btnPlay) btnPlay.textContent = `⏱️ ${simSec.toFixed(1)}с`;

        if (previewTextEl && (mode === "text" || mode === "both")) {
            if (simSec >= textStart) {
                previewTextEl.style.opacity = `${s.watermark_opacity}`;
            }
        }

        if (previewMedEl && (mode === "media" || mode === "both")) {
            if (simSec >= medStart) {
                previewMedEl.style.opacity = `${s.watermark_media_opacity}`;
            }
        }

        if (simSec >= maxSec) {
            clearInterval(timingSimInterval);
            timingSimInterval = null;
            if (btnPlay) {
                btnPlay.disabled = false;
                btnPlay.textContent = "▶️ Тест тайминга";
            }
            updateLivePreview();
        }
    }, 200);
}


// --- Shadowban Feed Checker Functions ---
window.checkUploadFeed = async function(uploadId, fid) {
    if (typeof showToast === 'function') {
        showToast(`Проверка поста #${fid} в общей ленте /feed...`, "info");
    }
    try {
        const res = await fetch(`/api/uploads/${uploadId}/check-feed`, { method: "POST" });
        const data = await res.json();
        if (data.success) {
            if (data.in_feed) {
                if (typeof showToast === 'function') showToast(`🟢 Пост #${fid} подтвержден в общей ленте /feed!`, "success");
            } else {
                if (typeof showToast === 'function') showToast(`🚨 Пост #${fid} НЕ найден в /feed (Теневой бан!)`, "error");
            }
            if (typeof loadRecentUploads === 'function') await loadRecentUploads();
            if (typeof loadLogs === 'function') await loadLogs();
        } else {
            if (typeof showToast === 'function') showToast(data.detail || data.error || "Ошибка проверки", "error");
        }
    } catch (e) {
        if (typeof showToast === 'function') showToast("Ошибка соединения: " + e.message, "error");
    }
};

window.checkAllFeed = async function() {
    const btn = document.getElementById("btnCheckAllFeed");
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span>⏳</span> Проверка...';
    }
    if (typeof showToast === 'function') {
        showToast("Сканирование ленты /feed на наличие ваших постов...", "info");
    }
    try {
        const res = await fetch("/api/uploads/check-all-feed", { method: "POST" });
        const data = await res.json();
        if (data.success) {
            const msg = `Проверено: ${data.checked}. В ленте: ${data.in_feed_count}, Shadowban: ${data.shadowbanned_count}`;
            if (typeof showToast === 'function') {
                showToast(msg, data.shadowbanned_count > 0 ? "warning" : "success");
            }
            if (typeof loadRecentUploads === 'function') await loadRecentUploads();
            if (typeof loadLogs === 'function') await loadLogs();
        } else {
            if (typeof showToast === 'function') showToast(data.detail || data.error || "Ошибка проверки", "error");
        }
    } catch (e) {
        if (typeof showToast === 'function') showToast("Ошибка соединения: " + e.message, "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<span>🔍</span> Проверить видимость в /feed';
        }
    }
};

document.addEventListener("DOMContentLoaded", function() {
    const btn = document.getElementById("btnCheckAllFeed");
    if (btn) {
        btn.addEventListener("click", () => window.checkAllFeed());
    }
});


// --- Tor Geo & Exit Country Handlers ---
function getCountryFlagEmoji(code) {
    if (!code || typeof code !== 'string' || code.length !== 2) return "🌐";
    try {
        const codePoints = code.toUpperCase().split('').map(c => 127397 + c.charCodeAt(0));
        return String.fromCodePoint(...codePoints);
    } catch (e) {
        return "🌐";
    }
}

async function restartTorService() {
    const btn = document.getElementById("btnRestartTorService");
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span>⏳</span> Перезапуск Tor...';
    }
    showToast("Перезапуск службы Tor и перестроение цепи через выбранное ГЕО...", "info");
    try {
        // First ensure settings are saved
        await saveTorSettings();
        const res = await fetch("/api/tor/service/restart", { method: "POST" });
        const data = await res.json();
        if (data.success) {
            showToast("🧅 Tor успешно перезапущен! Построение новой цепи...", "success");
            setTimeout(loadTorStatus, 3000);
            setTimeout(loadTorStatus, 8000);
        } else {
            showToast(data.error || "Ошибка перезапуска Tor", "error");
        }
    } catch (e) {
        showToast("Сбой перезапуска: " + e.message, "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '🔄 Сменить IP / ГЕО';
        }
    }
}

document.addEventListener("DOMContentLoaded", function() {
    const countrySelect = document.getElementById("torExitCountrySelect");
    const customContainer = document.getElementById("torCustomCountryContainer");
    if (countrySelect && customContainer) {
        countrySelect.addEventListener("change", function() {
            customContainer.style.display = this.value === "custom" ? "block" : "none";
        });
    }

    const btnRestart = document.getElementById("btnRestartTorService");
    if (btnRestart) {
        btnRestart.addEventListener("click", restartTorService);
    }
});


// --- Network Routing Mode & VLESS Handlers ---
async function loadNetworkStatus() {
    try {
        const res = await fetch("/api/network/status");
        const data = await res.json();
        if (!data.success) return;

        const mode = data.mode || "vless";
        const act = data.active_status || {};
        const vless = data.vless_status || {};

        // 1. Update Mode Cards
        const cardVless = document.getElementById("modeCardVless");
        const cardTor = document.getElementById("modeCardTor");
        const cardDirect = document.getElementById("modeCardDirect");
        const badgeVless = document.getElementById("badgeModeVless");
        const badgeTor = document.getElementById("badgeModeTor");
        const badgeDirect = document.getElementById("badgeModeDirect");
        const modeLabel = document.getElementById("activeNetworkModeLabel");

        if (cardVless && cardTor && cardDirect) {
            [cardVless, cardTor, cardDirect].forEach(c => {
                c.style.border = "1px solid rgba(255, 255, 255, 0.12)";
                c.style.background = "rgba(255, 255, 255, 0.03)";
            });
            if (badgeVless) badgeVless.style.display = "none";
            if (badgeTor) badgeTor.style.display = "none";
            if (badgeDirect) badgeDirect.style.display = "none";

            if (mode === "vless") {
                cardVless.style.border = "2px solid var(--accent-green)";
                cardVless.style.background = "rgba(16, 185, 129, 0.1)";
                if (badgeVless) badgeVless.style.display = "inline-block";
                if (modeLabel) {
                    modeLabel.innerHTML = '⚡ VLESS Reality (Германия)';
                    modeLabel.style.color = 'var(--accent-green)';
                }
            } else if (mode === "tor") {
                cardTor.style.border = "2px solid var(--accent-cyan)";
                cardTor.style.background = "rgba(6, 182, 212, 0.1)";
                if (badgeTor) badgeTor.style.display = "inline-block";
                if (modeLabel) {
                    modeLabel.innerHTML = '🧅 Tor Network';
                    modeLabel.style.color = 'var(--accent-cyan)';
                }
            } else {
                cardDirect.style.border = "2px solid #94a3b8";
                cardDirect.style.background = "rgba(148, 163, 184, 0.1)";
                if (badgeDirect) badgeDirect.style.display = "inline-block";
                if (modeLabel) {
                    modeLabel.innerHTML = '🌐 Direct (Прямое соединение)';
                    modeLabel.style.color = '#94a3b8';
                }
            }
        }

        // 2. Update Header Status Chip
        const chip = document.getElementById("headerTorChip");
        const chipIcon = document.getElementById("headerNetworkIcon");
        const chipText = document.getElementById("headerTorStatusText");
        const chipDot = document.getElementById("headerTorDot");

        if (chipText && chipDot) {
            if (mode === "vless") {
                if (chipIcon) chipIcon.textContent = "⚡";
                chipDot.className = act.connected ? "tor-dot active" : "tor-dot";
                if (act.connected && act.ip) {
                    chipText.textContent = `VLESS: ${act.ip} [${getCountryFlagEmoji(act.country_code)} ${act.country_code || 'DE'}]`;
                } else {
                    chipText.textContent = "VLESS: Подключение...";
                }
            } else if (mode === "tor") {
                if (chipIcon) chipIcon.textContent = "🧅";
                chipDot.className = act.connected ? "tor-dot active" : "tor-dot";
                if (act.connected && act.ip) {
                    chipText.textContent = `Tor: ${act.ip} [${getCountryFlagEmoji(act.country_code)} ${act.country_code || '?'}]`;
                } else {
                    chipText.textContent = "Tor: Отключен";
                }
            } else {
                if (chipIcon) chipIcon.textContent = "🌐";
                chipDot.className = "tor-dot active";
                chipText.textContent = "Direct: Прямой IP";
            }
        }

        // 3. Update VLESS Telemetry Card
        const vIp = document.getElementById("vlessIpValue");
        const vCountry = document.getElementById("vlessCountryValue");
        const vIsp = document.getElementById("vlessIspValue");
        const vIsTor = document.getElementById("vlessIsTorValue");
        const vWvids = document.getElementById("vlessWvidsValue");
        const vBadge = document.getElementById("vlessServiceBadge");

        if (mode === "vless" && act.connected) {
            if (vIp) vIp.textContent = act.ip || "37.114.42.98";
            if (vCountry) {
                const flag = getCountryFlagEmoji(act.country_code);
                vCountry.innerHTML = `<span style="font-size: 1.15rem;">${flag}</span> <strong>${act.country}</strong> ${act.city ? `(${act.city})` : ''}`;
            }
            if (vIsp) vIsp.textContent = act.isp || act.org || "ProHosting24 (SYNLINQ)";
            if (vIsTor) {
                vIsTor.innerHTML = act.is_tor ? '<span style="color: var(--accent-red);">Обнаружен Tor</span>' : '<span style="color: var(--accent-green);">✅ Чистый IP (IsTor: false)</span>';
            }
            if (vWvids) {
                vWvids.innerHTML = act.wvids_accessible ? '<span style="color: var(--accent-green);">✅ HTTP 200 OK (Доступен)</span>' : '<span style="color: var(--accent-red);">Недоступен</span>';
            }
            if (vBadge) {
                vBadge.innerHTML = '<span class="tor-dot active" style="background: var(--accent-green); box-shadow: 0 0 8px var(--accent-green);"></span> Подключено (10808)';
            }
        }
    } catch (e) {
        console.error("Failed to load network status:", e);
    }
}

window.switchNetworkMode = async function(mode) {
    const labels = {
        "vless": "⚡ VLESS Reality (Германия)",
        "tor": "🧅 Tor Network",
        "direct": "🌐 Прямое соединение"
    };
    showToast(`Переключение маршрутизатора на: ${labels[mode] || mode}...`, "info");
    try {
        const res = await fetch("/api/network/mode", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ mode: mode })
        });
        const data = await res.json();
        if (data.success) {
            showToast(`Маршрутизатор успешно переключен на: ${data.label}`, "success");
            await loadNetworkStatus();
            await loadLogs();
        } else {
            showToast(data.detail || data.error || "Ошибка переключения", "error");
        }
    } catch (e) {
        showToast("Ошибка запроса: " + e.message, "error");
    }
};

window.testVlessConnection = async function() {
    const btn = document.getElementById("btnTestVless");
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span>⏳</span> Проверка VLESS...';
    }
    showToast("Тестирование соединения с сервером VLESS (🇩🇪 Германия)...", "info");
    try {
        const res = await fetch("/api/vless/test", { method: "POST" });
        const data = await res.json();
        const r = data.result || {};
        if (data.success && r.connected) {
            const flag = getCountryFlagEmoji(r.country_code);
            showToast(`⚡ VLESS активен! IP: ${r.ip} (${flag} ${r.country}), пинг: ${r.latency_ms}мс`, "success");
            await loadNetworkStatus();
        } else {
            showToast(r.error || "Не удалось подключиться к VLESS", "error");
        }
    } catch (e) {
        showToast("Ошибка проверки VLESS: " + e.message, "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<span>🔍</span> Проверить связь VLESS';
        }
    }
};

// Make header chip clickable to jump directly to network settings
document.addEventListener("DOMContentLoaded", function() {
    const chip = document.getElementById("headerTorChip");
    if (chip) {
        chip.addEventListener("click", function() {
            const tabBtn = document.querySelector('[data-tab="tab-antidetect"]');
            if (tabBtn) tabBtn.click();
        });
    }
});
