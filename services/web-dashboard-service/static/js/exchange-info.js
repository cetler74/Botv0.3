class ExchangeInfoPage {
    constructor() {
        this.refreshIntervalMs = 30000;
        this.intervalId = null;
        this.init();
    }

    init() {
        this.loadData();
        this.intervalId = setInterval(() => this.loadData(), this.refreshIntervalMs);
    }

    formatDateTime(value) {
        if (!value) return "N/A";
        try {
            const date = new Date(value);
            if (Number.isNaN(date.getTime())) return "N/A";
            return date.toLocaleString();
        } catch (_err) {
            return "N/A";
        }
    }

    statusClassByBool(flag) {
        return flag ? "text-green-600" : "text-red-600";
    }

    statusBadgeClass(status) {
        const normalized = String(status || "unknown").toLowerCase();
        if (normalized.includes("healthy") || normalized.includes("connected")) {
            return "bg-green-100 text-green-800";
        }
        if (normalized.includes("degraded") || normalized.includes("connecting")) {
            return "bg-yellow-100 text-yellow-800";
        }
        if (normalized.includes("error") || normalized.includes("disconnected") || normalized.includes("unreachable")) {
            return "bg-red-100 text-red-800";
        }
        return "bg-gray-100 text-gray-800";
    }

    renderExchangeStatus(exchanges) {
        const container = document.getElementById("exchange-status-grid");
        if (!container) return;

        const entries = Object.entries(exchanges || {});
        if (!entries.length) {
            container.innerHTML = '<div class="text-sm text-gray-500">No exchange status available.</div>';
            return;
        }

        container.innerHTML = entries.map(([exchange, data]) => {
            const st = data.exchange_status || {};
            const status = st.status || "unknown";
            return `
                <div class="border border-gray-200 rounded-lg p-4">
                    <div class="flex items-center justify-between mb-2">
                        <h4 class="font-semibold text-gray-900 capitalize">${exchange}</h4>
                        <span class="px-2 py-1 text-xs font-medium rounded ${this.statusBadgeClass(status)}">${status}</span>
                    </div>
                    <div class="space-y-1 text-sm">
                        <div class="flex justify-between">
                            <span class="text-gray-600">Last check</span>
                            <span class="text-gray-800">${this.formatDateTime(st.last_check)}</span>
                        </div>
                        <div class="flex justify-between">
                            <span class="text-gray-600">Error count</span>
                            <span class="text-gray-800">${st.error_count ?? 0}</span>
                        </div>
                        <div class="flex justify-between">
                            <span class="text-gray-600">WS from exchange</span>
                            <span class="${this.statusClassByBool(Boolean(st.websocket_connected))} font-medium">
                                ${st.websocket_connected ? "Connected" : "Disconnected"}
                            </span>
                        </div>
                    </div>
                </div>
            `;
        }).join("");
    }

    renderWebsocketStatus(exchanges) {
        const container = document.getElementById("websocket-status-grid");
        if (!container) return;

        const entries = Object.entries(exchanges || {});
        if (!entries.length) {
            container.innerHTML = '<div class="text-sm text-gray-500">No websocket status available.</div>';
            return;
        }

        container.innerHTML = entries.map(([exchange, data]) => {
            const ws = data.websocket || {};
            const connected = Boolean(ws.connected);
            const status = ws.status || "unknown";
            return `
                <div class="border border-gray-200 rounded-lg p-4">
                    <div class="flex items-center justify-between mb-2">
                        <h4 class="font-semibold text-gray-900 capitalize">${exchange}</h4>
                        <span class="px-2 py-1 text-xs font-medium rounded ${this.statusBadgeClass(status)}">${status}</span>
                    </div>
                    <div class="space-y-1 text-sm">
                        <div class="flex justify-between">
                            <span class="text-gray-600">Connected</span>
                            <span class="${this.statusClassByBool(connected)} font-medium">${connected ? "Yes" : "No"}</span>
                        </div>
                        <div class="flex justify-between">
                            <span class="text-gray-600">Last update</span>
                            <span class="text-gray-800">${this.formatDateTime(ws.last_update)}</span>
                        </div>
                        <div class="flex justify-between">
                            <span class="text-gray-600">Callbacks</span>
                            <span class="text-gray-800">${ws.registered_callbacks ?? 0}</span>
                        </div>
                    </div>
                </div>
            `;
        }).join("");
    }

    renderSelectedPairs(exchanges) {
        const container = document.getElementById("pairs-selection-grid");
        if (!container) return;

        const entries = Object.entries(exchanges || {});
        if (!entries.length) {
            container.innerHTML = '<div class="text-sm text-gray-500">No selected pair data available.</div>';
            return;
        }

        container.innerHTML = entries.map(([exchange, data]) => {
            const pairs = Array.isArray(data.selected_pairs) ? data.selected_pairs : [];
            const blacklistedPairs = Array.isArray(data.blacklisted_pairs) ? data.blacklisted_pairs : [];
            const blacklistedSet = new Set(blacklistedPairs);
            const timestamp = this.formatDateTime(data.pairs_last_selected_at);
            const pairsHtml = pairs.length
                ? pairs.map((pair) => {
                    const isBlacklisted = blacklistedSet.has(pair);
                    const baseClass = isBlacklisted
                        ? "bg-red-100 text-red-800 border border-red-200"
                        : "bg-gray-100 text-gray-800";
                    const badge = isBlacklisted
                        ? '<span class="ml-1 text-[10px] font-semibold uppercase tracking-wide">blacklisted</span>'
                        : "";
                    return `<span class="inline-block text-xs px-2 py-1 rounded mr-1 mb-1 ${baseClass}">${pair}${badge}</span>`;
                }).join("")
                : '<span class="text-sm text-gray-500">No selected pairs.</span>';
            const errorLine = data.pairs_error
                ? `<div class="text-xs text-red-600 mt-2">Pairs source error: ${data.pairs_error}</div>`
                : "";
            const selectedBlacklistedCount = Number(data.selected_blacklisted_count || 0);
            const blacklistedSummary = `
                <div class="text-xs mt-2 ${selectedBlacklistedCount > 0 ? "text-red-600" : "text-green-700"}">
                    Blacklisted in selection: ${selectedBlacklistedCount}
                </div>
            `;
            return `
                <div class="border border-gray-200 rounded-lg p-4">
                    <div class="flex items-center justify-between mb-2">
                        <h4 class="font-semibold text-gray-900 capitalize">${exchange}</h4>
                        <span class="text-xs text-gray-600">Count: ${data.selected_pairs_count ?? pairs.length}</span>
                    </div>
                    <div class="text-sm text-gray-600 mb-2">
                        Last selection: <span class="text-gray-800">${timestamp}</span>
                    </div>
                    <div class="min-h-[2rem]">${pairsHtml}</div>
                    ${blacklistedSummary}
                    ${errorLine}
                </div>
            `;
        }).join("");
    }

    async loadData() {
        try {
            const response = await fetch("/api/exchange-information");
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const payload = await response.json();
            const exchanges = payload.exchanges || {};

            this.renderExchangeStatus(exchanges);
            this.renderWebsocketStatus(exchanges);
            this.renderSelectedPairs(exchanges);

            const refreshNode = document.getElementById("exchange-info-last-refresh");
            if (refreshNode) {
                refreshNode.textContent = this.formatDateTime(payload.timestamp);
            }
        } catch (error) {
            const message = `Error loading exchange information: ${error.message}`;
            ["exchange-status-grid", "websocket-status-grid", "pairs-selection-grid"].forEach((id) => {
                const node = document.getElementById(id);
                if (node) node.innerHTML = `<div class="text-sm text-red-600">${message}</div>`;
            });
        }
    }
}

document.addEventListener("DOMContentLoaded", () => {
    window.exchangeInfoPage = new ExchangeInfoPage();
});

window.addEventListener("beforeunload", () => {
    if (window.exchangeInfoPage && window.exchangeInfoPage.intervalId) {
        clearInterval(window.exchangeInfoPage.intervalId);
    }
});
