(() => {
  "use strict";

  const STAGE_WIDTH = 1800;
  const STAGE_HEIGHT = 1100;
  const WORLD_MIN = -100;
  const WORLD_MAX = 101;
  const GRID_PIXELS = 22;
  const SVG_NS = "http://www.w3.org/2000/svg";
  const INFRASTRUCTURE_TYPES = new Set(["router", "switch", "access-point", "server"]);
  const EXPORT_FILES = new Set([
    "complist.csv", "complist2.csv", "sw_list.csv", "sw_conn.csv",
    "port_list.csv", "vlan_list.csv", "top_map.xml",
    "swlist.csv", "swconn.csv", "portlist.csv", "vlanlist.csv",
  ]);
  const ICON_TYPES = ["auto", "router", "switch", "access-point", "server", "workstation", "printer", "phone", "other"];
  const SHAPES = ["icon", "card", "circle", "text"];
  const {
    arrangeNodesHierarchically, arrangeNodesBySubnet, arrangeNodesBySwitch, arrangeNodesByVlan,
    cancelViewportGestures, constrainPanToBounds, fitTransform, focusTransform, moveSelectedNodes,
    nodeDisplayLabel, nodeVlanIds, normalizeManualVlanIds, nodesInsideBox, resolveIconType, shouldAddMarqueeSelection,
    healthWindowValue, heatmapGlowVisible, heatmapOverlayVisible, heatmapSource, orthogonalEdgePath, reconcileVisibleSelection, topologyIcon, unmanagedGroupChildren, visibleTopology,
    vlanGroups, vlanNodeDecoration, viewportToWorld, zoomScale,
  } = window.TopologyUtils;

  const displayPreferences = readDisplayPreferences();
  const initialTheme = readThemePreference();
  const initialSite = readSitePreference();

  const state = {
    nodes: [], edges: [], interfaces: [], vlans: [], importStatus: {},
    selectedNodeIds: new Set(), selectedNodeId: null, selectedEdgeId: null, panelMode: null,
    filter: "all", search: "", connecting: false, connectSourceId: null,
    transform: {x: 0, y: 0, scale: 1}, fitted: false, fittedForNodes: false,
    tool: "pan", snapToGrid: readSnapPreference(), vlanView: readVlanPreference(),
    heatmapEnabled: readHeatmapPreference(), heatmapWindow: readHeatmapWindowPreference(),
    heatmapShowHealthy: readHeatmapLayerPreference("topology-health-heatmap-show-healthy"),
    heatmapShowCritical: readHeatmapLayerPreference("topology-health-heatmap-show-critical"),
    heatmapMinLoss: readHeatmapMinLossPreference(),
    hiddenTypes: displayPreferences.hiddenTypes, hideManual: displayPreferences.hideManual,
    hideNoIp: displayPreferences.hideNoIp,
    labelMode: displayPreferences.labelMode, collapsedUnmanagedGroups: new Set(),
    inventoryCollapsed: readInventoryPreference(),
    theme: initialTheme,
    siteId: initialSite, sites: [], mqttClients: [], mqttStatus: {}, siteEpoch: 0,
    selectedImportMarker: null, livenessAvailable: true, livenessStale: false,
    panning: null, marquee: null, dragging: null,
  };
  const el = {
    viewport: document.getElementById("graph-viewport"), stage: document.getElementById("graph-stage"),
    heatmapLayer: document.getElementById("heatmap-layer"),
    nodeLayer: document.getElementById("node-layer"), edgeLayer: document.getElementById("edge-layer"),
    vlanLegend: document.getElementById("vlan-legend"),
    selectionBox: document.getElementById("selection-box"),
    inventory: document.getElementById("inventory-list"), detail: document.getElementById("detail-panel"),
    inventoryToggle: document.getElementById("toggle-inventory"),
    workspace: document.getElementById("workspace"), arrangeOptions: document.getElementById("arrange-options"),
    detailContent: document.getElementById("detail-content"), empty: document.getElementById("graph-empty"),
    emptyTitle: document.getElementById("graph-empty-title"),
    emptyMessage: document.getElementById("graph-empty-message"), emptyImport: document.getElementById("empty-import"),
    importButton: document.getElementById("import-button"), importFolder: document.getElementById("import-folder"),
    checkLiveness: document.getElementById("check-liveness"),
    importState: document.getElementById("import-state"), connectButton: document.getElementById("connect-button"),
    modeHint: document.getElementById("mode-hint"), zoomLabel: document.getElementById("zoom-label"),
    snapGrid: document.getElementById("snap-grid"), vlanView: document.getElementById("vlan-view"),
    heatmapToggle: document.getElementById("heatmap-toggle"), heatmapWindow: document.getElementById("heatmap-window"),
    heatmapShowHealthy: document.getElementById("heatmap-show-healthy"),
    heatmapShowCritical: document.getElementById("heatmap-show-critical"),
    heatmapMenuSummary: document.getElementById("heatmap-menu-summary"),
    heatmapMinLoss: document.getElementById("heatmap-min-loss"),
    heatmapMinLossSlider: document.getElementById("heatmap-min-loss-slider"),
    arrangeHierarchy: document.getElementById("arrange-hierarchy"),
    arrangeSwitch: document.getElementById("arrange-switch"), arrangeSubnet: document.getElementById("arrange-subnet"),
    arrangeVlan: document.getElementById("arrange-vlan"), panTool: document.getElementById("pan-tool"),
    saveCustomArrangement: document.getElementById("save-custom-arrangement"),
    restoreCustomArrangement: document.getElementById("restore-custom-arrangement"),
    boxSelectTool: document.getElementById("box-select-tool"),
    toast: document.getElementById("toast-region"), search: document.getElementById("node-search"),
    focusSearch: document.getElementById("focus-search"), hideManual: document.getElementById("hide-manual"),
    hideNoIp: document.getElementById("hide-no-ip"),
    labelMode: document.getElementById("label-mode"),
    themeToggle: document.getElementById("theme-toggle"),
    siteSelector: document.getElementById("site-selector"),
    livenessStatus: document.getElementById("liveness-status"),
    mqttClientsButton: document.getElementById("mqtt-clients-button"),
    mqttClientsPanel: document.getElementById("mqtt-clients-panel"),
    mqttClientsList: document.getElementById("mqtt-clients-list"),
    mqttConnectionState: document.getElementById("mqtt-connection-state"),
    pendingClientCount: document.getElementById("pending-client-count"),
  };

  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  const fmtTime = (value) => value
    ? new Intl.DateTimeFormat(undefined, {month: "short", day: "numeric", hour: "numeric", minute: "2-digit"}).format(new Date(value))
    : "-";

  function readSnapPreference() {
    try { return localStorage.getItem("topology-snap-grid") === "true"; }
    catch (_) { return false; }
  }

  function readSitePreference() {
    try { return sessionStorage.getItem("status-visualizer-site") || "local"; }
    catch (_) { return "local"; }
  }

  function readThemePreference() {
    try { return localStorage.getItem("topology-theme") === "light" ? "light" : "dark"; }
    catch (_) { return "dark"; }
  }

  function applyTheme(theme) {
    state.theme = theme === "light" ? "light" : "dark";
    document.documentElement.dataset.theme = state.theme;
    const light = state.theme === "light";
    el.themeToggle.setAttribute("aria-pressed", String(light));
    el.themeToggle.setAttribute("aria-label", `Switch to ${light ? "dark" : "light"} mode`);
    el.themeToggle.querySelector(".theme-icon").textContent = light ? "☾" : "☀";
    el.themeToggle.querySelector("span:last-child").textContent = light ? "Dark theme" : "Light theme";
  }

  function toggleTheme() {
    applyTheme(state.theme === "dark" ? "light" : "dark");
    try { localStorage.setItem("topology-theme", state.theme); }
    catch (_) { /* Preference remains session-only. */ }
  }

  function readVlanPreference() {
    try {
      const saved = localStorage.getItem("topology-vlan-view");
      return saved === null ? true : saved === "true";
    } catch (_) { return true; }
  }

  function readHeatmapPreference() {
    try { return localStorage.getItem("topology-health-heatmap") === "true"; }
    catch (_) { return false; }
  }

  function readHeatmapWindowPreference() {
    try {
      const value = localStorage.getItem("topology-health-window");
      return ["5m", "15m", "1h", "12h", "24h"].includes(value) ? value : "15m";
    } catch (_) { return "15m"; }
  }

  function readHeatmapLayerPreference(key) {
    try {
      const value = localStorage.getItem(key);
      return value === null ? true : value === "true";
    } catch (_) { return true; }
  }

  function normalizeHeatmapMinLoss(raw) {
    if (raw === "" || raw === null || raw === undefined) return 0;
    const value = Number(raw);
    if (!Number.isFinite(value) || value <= 0) return 0;
    return Math.min(99.9, Math.max(0.1, Math.round(value * 10) / 10));
  }

  function readHeatmapMinLossPreference() {
    try {
      return normalizeHeatmapMinLoss(localStorage.getItem("topology-health-min-loss"));
    } catch (_) { return 0; }
  }

  function readInventoryPreference() {
    try { return localStorage.getItem("topology-inventory-collapsed") === "true"; }
    catch (_) { return false; }
  }

  function readDisplayPreferences() {
    try {
      const parsed = JSON.parse(localStorage.getItem("topology-display-options") || "{}");
      const allowedModes = new Set(["hostname", "ip", "both"]);
      return {
        hiddenTypes: new Set(Array.isArray(parsed.hiddenTypes) ? parsed.hiddenTypes.map(String) : []),
        hideManual: Boolean(parsed.hideManual),
        hideNoIp: Boolean(parsed.hideNoIp),
        labelMode: allowedModes.has(parsed.labelMode) ? parsed.labelMode : "both",
      };
    } catch (_) { return {hiddenTypes: new Set(), hideManual: false, hideNoIp: false, labelMode: "both"}; }
  }

  function saveDisplayPreferences() {
    try {
      localStorage.setItem("topology-display-options", JSON.stringify({
        hiddenTypes: [...state.hiddenTypes], hideManual: state.hideManual, hideNoIp: state.hideNoIp, labelMode: state.labelMode,
      }));
    } catch (_) { /* Preference remains session-only. */ }
  }

  function toast(message, isError = false) {
    const item = document.createElement("div");
    item.className = `toast${isError ? " error" : ""}`;
    item.textContent = message;
    el.toast.append(item);
    setTimeout(() => item.remove(), 5000);
  }

  async function api(path, options = {}) {
    const requestSite = options.siteId || state.siteId;
    const isSiteScoped = ["/api/topology", "/api/devices", "/api/edges", "/api/layouts", "/api/import", "/api/liveness", "/api/exports"]
      .some((prefix) => path.startsWith(prefix));
    const headers = {
      ...(options.body ? {"Content-Type": "application/json"} : {}),
      ...(isSiteScoped ? {"X-Status-Visualizer-Site": requestSite} : {}),
      ...(options.headers || {}),
    };
    const {siteId: _siteId, ...fetchOptions} = options;
    const response = await fetch(path, {...fetchOptions, headers});
    if (!response.ok) {
      let message = `${response.status} ${response.statusText}`;
      try {
        const body = await response.json();
        message = typeof body.detail === "string" ? body.detail : (body.detail || []).map((item) => item.msg).join("; ") || message;
      } catch (_) { /* Keep the safe status fallback. */ }
      throw new Error(message);
    }
    return response.status === 204 ? null : response.json();
  }

  function nodeById(id) { return state.nodes.find((node) => node.id === id); }
  function edgeById(id) { return state.edges.find((edge) => edge.id === id); }
  function currentVisibleTopology() {
    return visibleTopology(state.nodes, state.edges, {
      hiddenTypes: state.hiddenTypes,
      hideManual: state.hideManual,
      hideNoIp: state.hideNoIp,
      collapsedGroupIds: state.collapsedUnmanagedGroups,
    });
  }
  function isInfrastructure(node) { return INFRASTRUCTURE_TYPES.has(node.node_type); }
  function isManualSource(node) { return ["local", "manual"].includes(node.source); }
  function nodeStatus(node) {
    if (node.health?.online === true) return "online";
    if (node.health?.online === false) return "offline";
    return ["online", "offline"].includes(node.liveness_state) ? node.liveness_state : "unknown";
  }
  function statusLabel(node) {
    if (state.siteId !== "local" && !state.livenessAvailable) {
      return state.livenessStale ? "Remote status stale" : "Remote status unavailable";
    }
    return nodeStatus(node) === "online" ? "Online" : nodeStatus(node) === "offline" ? "No ping reply" : "Not checked";
  }
  function nodeVlanLabel(node) {
    const ids = nodeVlanIds(node, state.interfaces, state.vlans);
    return ids.length === 1 ? `VLAN ${ids[0]}` : ids.length > 1 ? `${ids.length} VLANs` : "";
  }
  function nodeAriaLabel(node) {
    const vlan = state.vlanView ? nodeVlanLabel(node) : "";
    const label = nodeDisplayLabel(node, state.labelMode);
    return `${label.primary}${label.secondary ? `, ${label.secondary}` : ""}, ${statusLabel(node)}${vlan ? `, ${vlan}` : ""}`;
  }

  function relativeTime(value) {
    const elapsed = Math.max(0, Date.now() - new Date(value).getTime());
    if (!value || !Number.isFinite(elapsed)) return "—";
    const seconds = Math.round(elapsed / 1000);
    if (seconds < 60) return `${seconds} sec ago`;
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return `${minutes} min ago`;
    const hours = Math.round(minutes / 60);
    return hours < 24 ? `${hours} hr ago` : `${Math.round(hours / 24)} day ago`;
  }

  function vlanDecorationMarkup(decoration) {
    if (!decoration) return "";
    const segments = decoration.segments.map((segment) => (
      `<span style="--vlan-color:${segment.color}" title="${escapeHtml(segment.title)}"></span>`
    )).join("");
    const extra = decoration.extraCount
      ? `<span class="vlan-segment-extra">+${decoration.extraCount}</span>`
      : "";
    return `<span class="vlan-stripe" aria-hidden="true">${segments}${extra}</span>`
      + `<span class="vlan-badge" style="--vlan-color:${decoration.color}" title="${escapeHtml(decoration.title)}">${escapeHtml(decoration.label)}</span>`;
  }
  function applyTopology(data) {
    state.nodes = data.nodes || [];
    state.edges = data.edges || [];
    state.interfaces = data.interfaces || [];
    state.vlans = data.vlans || [];
    state.importStatus = data.import_status || {};
    state.collapsedUnmanagedGroups = new Set([...state.collapsedUnmanagedGroups]
      .filter((id) => unmanagedGroupChildren(state.nodes, state.edges, id).length));
    state.selectedNodeIds = new Set([...state.selectedNodeIds].filter((id) => nodeById(id)));
    if (state.selectedNodeId && !nodeById(state.selectedNodeId)) hidePanel();
    if (state.selectedEdgeId && !edgeById(state.selectedEdgeId)) hidePanel();
    reconcileSelectionWithVisibleTopology();
    renderStats(data.generated_at);
    renderImportState();
    renderInventory();
    renderGraph();
    if (state.nodes.length && !state.fittedForNodes) {
      state.fittedForNodes = true;
      requestAnimationFrame(fitGraph);
    }
    if (state.panelMode === "node" && state.selectedNodeId) renderNodeDetails(state.selectedNodeId);
    if (state.panelMode === "edge" && state.selectedEdgeId) renderEdgeDetails(state.selectedEdgeId);
  }

  async function loadTopology(showError = true) {
    const requestSite = state.siteId;
    const requestEpoch = state.siteEpoch;
    try {
      const topology = await api("/api/topology", {siteId: requestSite});
      if (state.siteId !== requestSite) return;
      if (state.siteEpoch !== requestEpoch) return;
      applyTopology(topology);
    }
    catch (error) { if (showError) toast(`Could not load topology: ${error.message}`, true); }
  }

  function siteIdentifier(site) { return String(site.id || site.site_id || site.client_id || ""); }
  function siteLabel(site) { return String(site.display_name || site.name || (siteIdentifier(site) === "local" ? "Local" : siteIdentifier(site))); }
  function siteImportMarker(site) { return site.last_imported_at || site.imported_at || site.last_snapshot_hash || null; }

  function renderSites() {
    const sites = state.sites.length ? state.sites : [{id: "local", display_name: "Local"}];
    if (!sites.some((site) => siteIdentifier(site) === state.siteId)) {
      state.siteId = "local";
      try { sessionStorage.setItem("status-visualizer-site", state.siteId); }
      catch (_) { /* Selection remains in memory for this tab. */ }
    }
    el.siteSelector.innerHTML = sites.map((site) => {
      const id = siteIdentifier(site);
      return `<option value="${escapeHtml(id)}" ${id === state.siteId ? "selected" : ""}>${escapeHtml(siteLabel(site))}</option>`;
    }).join("");
    syncSiteControls();
  }

  function syncSiteControls() {
    const remote = state.siteId !== "local";
    el.checkLiveness.disabled = remote;
    el.checkLiveness.hidden = remote;
    el.checkLiveness.querySelector("span:last-child").textContent = "Check device status";
    el.checkLiveness.title = remote ? "This remote site may not be reachable for ICMP from the central server." : "";
    el.livenessStatus.hidden = !remote;
  }

  function clientErrorMarkup(client) {
    return client.last_error ? `<p class="client-error">${escapeHtml(client.last_error)}</p>` : "";
  }

  function captureMqttClientEditorState() {
    const drafts = new Map();
    let focusedClientId = null;
    let selectionStart = 0;
    let selectionEnd = 0;
    el.mqttClientsList.querySelectorAll(".client-card").forEach((card) => {
      const clientId = card.dataset.clientId;
      const input = card.querySelector(".client-name");
      if (!clientId || !input) return;
      drafts.set(clientId, input.value);
      if (input === document.activeElement) {
        focusedClientId = clientId;
        selectionStart = input.selectionStart ?? input.value.length;
        selectionEnd = input.selectionEnd ?? input.value.length;
      }
    });
    return {drafts, focusedClientId, selectionStart, selectionEnd};
  }

  function renderMqttClients(editorState = null) {
    const clients = state.mqttClients;
    const pendingCount = clients.filter((client) => client.state === "pending").length;
    el.pendingClientCount.hidden = pendingCount === 0;
    el.pendingClientCount.textContent = String(pendingCount);
    el.mqttClientsButton.classList.toggle("has-pending", pendingCount > 0);
    const configured = Boolean(state.mqttStatus.configured);
    const connected = Boolean(state.mqttStatus.connected);
    el.mqttConnectionState.className = `client-connection-state ${connected ? "is-connected" : ""}`;
    el.mqttConnectionState.textContent = !configured ? "MQTT is not configured" : connected ? "MQTT connected" : "MQTT disconnected";
    if (!clients.length) {
      el.mqttClientsList.innerHTML = '<p class="client-empty">No MQTT clients discovered yet.</p>';
      return;
    }
    el.mqttClientsList.innerHTML = clients.map((client) => {
      const clientId = String(client.client_id || client.id || "");
      const currentName = client.display_name || "";
      const draftName = editorState?.drafts?.get(clientId);
      const inputValue = draftName !== undefined ? draftName : currentName;
      const stateLabel = String(client.state || "pending");
      const seen = client.last_seen_at ? `Last seen ${fmtTime(client.last_seen_at)}` : "Not seen yet";
      return `<article class="client-card" data-client-id="${escapeHtml(clientId)}">`
        + `<div class="client-card-heading"><div><strong>${escapeHtml(currentName || "Pending client")}</strong><code>${escapeHtml(clientId)}</code></div><span class="client-state client-state-${escapeHtml(stateLabel)}">${escapeHtml(stateLabel)}</span></div>`
        + `<label><span>Friendly name</span><input class="client-name" type="text" maxlength="100" value="${escapeHtml(inputValue)}" placeholder="Branch office"></label>`
        + `<p class="client-seen">${escapeHtml(seen)}</p>${clientErrorMarkup(client)}`
        + `<div class="client-actions"><button class="text-button client-approve" type="button">${stateLabel === "approved" ? "Save name" : "Approve"}</button>`
        + `<button class="text-button client-block" type="button">${stateLabel === "blocked" ? "Blocked" : "Block"}</button></div></article>`;
    }).join("");
    el.mqttClientsList.querySelectorAll(".client-card").forEach((card) => {
      const clientId = card.dataset.clientId;
      card.querySelector(".client-approve").addEventListener("click", () => updateMqttClient(clientId, "approved", card));
      card.querySelector(".client-block").addEventListener("click", () => updateMqttClient(clientId, "blocked", card));
    });
    if (editorState?.focusedClientId) {
      const input = el.mqttClientsList.querySelector(
        `.client-card[data-client-id="${CSS.escape(editorState.focusedClientId)}"] .client-name`,
      );
      if (input) {
        input.focus();
        input.setSelectionRange(editorState.selectionStart, editorState.selectionEnd);
      }
    }
  }

  async function updateMqttClient(clientId, nextState, card) {
    const displayName = card.querySelector(".client-name").value.trim();
    if (nextState === "approved" && !displayName) {
      toast("Enter a friendly name before approving this client.", true);
      card.querySelector(".client-name").focus();
      return;
    }
    const buttons = [...card.querySelectorAll("button")];
    buttons.forEach((button) => { button.disabled = true; });
    try {
      await api(`/api/mqtt/clients/${clientId}`, {
        method: "PATCH",
        headers: {"X-Status-Visualizer-Request": "1"},
        body: JSON.stringify({display_name: displayName || null, state: nextState}),
      });
      toast(nextState === "approved" ? `Approved ${displayName}.` : "Client blocked.");
      await refreshSitesAndClients();
    } catch (error) {
      toast(`Could not update client: ${error.message}`, true);
    } finally { buttons.forEach((button) => { button.disabled = false; }); }
  }

  async function refreshSitesAndClients(initial = false) {
    try {
      const [sites, clientData] = await Promise.all([api("/api/sites"), api("/api/mqtt/clients")]);
      const previousSite = state.siteId;
      const previousMarker = state.selectedImportMarker;
      state.sites = Array.isArray(sites) ? sites : [];
      state.mqttClients = Array.isArray(clientData.clients) ? clientData.clients : [];
      state.mqttStatus = clientData.mqtt || {};
      renderSites();
      const editorState = el.mqttClientsPanel.hidden ? null : captureMqttClientEditorState();
      renderMqttClients(editorState);
      const selectionFellBack = state.siteId !== previousSite;
      const selectedSite = state.sites.find((site) => siteIdentifier(site) === state.siteId);
      const nextMarker = selectedSite ? siteImportMarker(selectedSite) : null;
      state.selectedImportMarker = nextMarker;
      if (!initial && selectionFellBack) {
        state.siteEpoch += 1;
        state.fittedForNodes = false;
        clearSelection();
        await loadTopology(false);
        await loadLiveness(false);
      } else if (!initial && nextMarker && previousMarker !== nextMarker) {
        await loadTopology(false);
      }
    } catch (error) {
      if (initial) toast(`Could not load sites: ${error.message}`, true);
    }
  }

  async function selectSite(siteId) {
    if (!siteId || siteId === state.siteId) return;
    state.siteId = siteId;
    state.siteEpoch += 1;
    state.selectedImportMarker = siteImportMarker(state.sites.find((site) => siteIdentifier(site) === siteId) || {});
    state.fittedForNodes = false;
    clearSelection();
    try { sessionStorage.setItem("status-visualizer-site", state.siteId); }
    catch (_) { /* Selection remains in memory for this tab. */ }
    syncSiteControls();
    await loadTopology();
    await loadLiveness(false);
  }

  function applyLiveness(data) {
    state.livenessAvailable = data.available !== false;
    state.livenessStale = Boolean(data.stale);
    if (state.siteId !== "local") {
      el.livenessStatus.hidden = false;
      el.livenessStatus.querySelector("span").textContent = state.livenessAvailable
        ? `Remote status checked ${fmtTime(data.checked_at)}`
        : state.livenessStale && data.checked_at
          ? `Remote status stale (last checked ${fmtTime(data.checked_at)})`
          : "Remote status unavailable";
    }
    const byId = new Map((data.devices || []).map((item) => [item.device_id, item]));
    state.nodes = state.nodes.map((node) => {
      const status = byId.get(node.id);
      return status ? {
        ...node,
        health: {...status},
        liveness_state: status.state || (status.online === true ? "online" : status.online === false ? "offline" : node.liveness_state),
        liveness_checked_at: status.checked_at,
        liveness_latency_ms: status.current_rtt_ms ?? status.latency_ms,
      } : node;
    });
    renderInventory();
    renderGraph();
    if (state.panelMode === "node" && state.selectedNodeId) renderNodeDetails(state.selectedNodeId);
  }

  async function loadLiveness(showError = false) {
    const requestSite = state.siteId;
    const requestEpoch = state.siteEpoch;
    try {
      const liveness = await api("/api/liveness", {siteId: requestSite});
      if (state.siteId !== requestSite) return;
      if (state.siteEpoch !== requestEpoch) return;
      applyLiveness(liveness);
    }
    catch (error) { if (showError) toast(`Could not load status: ${error.message}`, true); }
  }

  async function checkLiveness() {
    if (state.siteId !== "local") return;
    const requestSite = state.siteId;
    el.checkLiveness.disabled = true;
    el.checkLiveness.querySelector("span:last-child").textContent = "Checking...";
    try {
      const result = await api("/api/liveness/check", {
        method: "POST",
        body: "{}",
        headers: {"X-Status-Visualizer-Request": "1"},
        siteId: requestSite,
      });
      if (state.siteId !== requestSite) return;
      applyLiveness(result);
      const checked = result.check?.checked || 0;
      toast(`Checked ${checked} addresses: ${result.check.online} online, ${result.check.offline} no reply`);
    } catch (error) {
      if (state.siteId === requestSite) toast(`Status check failed: ${error.message}`, true);
    }
    finally {
      syncSiteControls();
    }
  }

  function renderStats(generatedAt) {
    const infrastructure = state.nodes.filter(isInfrastructure).length;
    document.getElementById("stat-total").textContent = state.nodes.length;
    document.getElementById("stat-infrastructure").textContent = infrastructure;
    document.getElementById("stat-endpoints").textContent = state.nodes.length - infrastructure;
    document.getElementById("stat-connections").textContent = state.edges.length;
    document.getElementById("stat-vlans").textContent = state.vlans.length;
    document.getElementById("refresh-time").textContent = fmtTime(generatedAt);
    const imported = state.importStatus;
    document.getElementById("import-summary").textContent = imported.imported_at
      ? `${imported.files_used || 0} files - ${imported.connections || 0} connections - imported ${fmtTime(imported.imported_at)}`
      : "Select one Lantopolog Export folder";
  }

  function renderImportState() {
    const label = el.importState.querySelector("span");
    if (state.importStatus.imported_at) {
      el.importState.className = "service-state is-live";
      label.textContent = `LanTopoLog · ${state.importStatus.nodes || 0} nodes`;
    } else {
      el.importState.className = "service-state";
      label.textContent = "No export imported";
    }
  }

  function filteredNodes() {
    const query = state.search.toLowerCase();
    return state.nodes.filter((node) => {
      const category = isManualSource(node) ? "manual" : isInfrastructure(node) ? "infrastructure" : "endpoint";
      const searchable = [node.name, node.address, node.mac_address, node.node_type,
        ...Object.values(node.metadata || {})].join(" ").toLowerCase();
      return (state.filter === "all" || state.filter === category) && (!query || searchable.includes(query));
    });
  }

  function renderInventory() {
    el.inventory.replaceChildren();
    const nodes = filteredNodes().sort((a, b) => a.name.localeCompare(b.name));
    if (!nodes.length) {
      const empty = document.createElement("div");
      empty.className = "inventory-empty";
      empty.textContent = "No matching nodes";
      el.inventory.append(empty);
      return;
    }
    for (const node of nodes) {
      const item = document.createElement("div");
      item.className = `inventory-item${state.selectedNodeIds.has(node.id) ? " is-selected" : ""}`;
      const select = document.createElement("button");
      select.type = "button";
      select.className = "inventory-select";
      select.innerHTML = `<i class="node-status status-${nodeStatus(node)}" title="${statusLabel(node)}"></i>`
        + `<span class="inventory-copy"><strong>${escapeHtml(node.name)}</strong>`
        + `<span>${escapeHtml(node.address || node.mac_address || node.node_type)}</span></span>`;
      select.addEventListener("click", (event) => selectNode(node.id, event.ctrlKey || event.metaKey || event.shiftKey));
      const focus = document.createElement("button");
      focus.type = "button";
      focus.className = "inventory-focus";
      focus.setAttribute("data-focus-node-id", node.id);
      focus.title = `Focus ${node.name || node.address || "device"}`;
      focus.setAttribute("aria-label", focus.title);
      focus.textContent = "⌖";
      focus.addEventListener("click", () => focusNode(node.id));
      item.append(select, focus);
      el.inventory.append(item);
    }
  }

  function renderVlanDecorations() {
    el.vlanLegend.replaceChildren();
    const visibleNodes = currentVisibleTopology().nodes;
    const decorations = visibleNodes
      .map((node) => vlanNodeDecoration(node, state.interfaces, state.vlans, {maxSegments: 4094}))
      .filter(Boolean);
    const hasAssignments = decorations.length > 0;
    const visible = state.vlanView && hasAssignments;
    el.vlanLegend.hidden = !visible;
    if (!visible) return;

    const entries = new Map();
    for (const decoration of decorations) {
      for (const segment of decoration.segments) {
        const entry = entries.get(segment.id) || {...segment, count: 0};
        entries.set(segment.id, {...entry, count: entry.count + 1});
      }
    }
    const sortedEntries = [...entries.values()]
      .sort((left, right) => left.id.localeCompare(right.id, undefined, {numeric: true}));
    for (const entry of sortedEntries) {
      const item = document.createElement("span");
      item.className = "vlan-legend-item";
      const swatch = document.createElement("i");
      swatch.style.setProperty("--vlan-color", entry.color);
      const label = document.createElement("span");
      label.textContent = `${entry.title.replace(" - ", " · ")} (${entry.count})`;
      item.append(swatch, label);
      el.vlanLegend.append(item);
    }
    const multiple = decorations.filter((decoration) => decoration.ids.length > 1);
    if (multiple.length) {
      const item = document.createElement("span");
      item.className = "vlan-legend-item";
      const swatch = document.createElement("i");
      swatch.className = "is-multiple";
      swatch.style.setProperty("--vlan-background", multiple[0].background);
      const label = document.createElement("span");
      label.textContent = `Multiple VLANs (${multiple.length})`;
      item.append(swatch, label);
      el.vlanLegend.append(item);
    }
  }

  function appendHeatmapGradient(defs, band) {
    const gradient = document.createElementNS(SVG_NS, "radialGradient");
    gradient.setAttribute("id", `topology-heatmap-gradient-${band.level}`);
    gradient.setAttribute("cx", "50%");
    gradient.setAttribute("cy", "50%");
    gradient.setAttribute("r", "50%");
    const stops = band.vivid
      ? [["0%", "0.82"], ["38%", "0.34"], ["72%", "0.12"], ["100%", "0"]]
      : [["0%", "0.9"], ["42%", "0.38"], ["100%", "0"]];
    for (const [offset, stopOpacity] of stops) {
      const stop = document.createElementNS(SVG_NS, "stop");
      stop.setAttribute("offset", offset);
      stop.setAttribute("stop-color", band.color);
      stop.setAttribute("stop-opacity", stopOpacity);
      gradient.append(stop);
    }
    defs.append(gradient);
  }

  function renderHeatmapOverlay() {
    el.heatmapLayer.replaceChildren();
    if (!state.heatmapEnabled) {
      el.heatmapLayer.hidden = true;
      return;
    }
    el.heatmapLayer.hidden = false;
    const defs = document.createElementNS(SVG_NS, "defs");
    const filter = document.createElementNS(SVG_NS, "filter");
    filter.setAttribute("id", "topology-heatmap-soften");
    filter.setAttribute("x", "-60%");
    filter.setAttribute("y", "-60%");
    filter.setAttribute("width", "220%");
    filter.setAttribute("height", "220%");
    const blur = document.createElementNS(SVG_NS, "feGaussianBlur");
    blur.setAttribute("stdDeviation", "24");
    filter.append(blur);
    defs.append(filter);
    for (const band of window.TopologyUtils.getHeatmapLossBands()) {
      appendHeatmapGradient(defs, band);
    }
    const healthyGroup = document.createElementNS(SVG_NS, "g");
    healthyGroup.setAttribute("class", "heatmap-sources-healthy");
    const lossGroup = document.createElementNS(SVG_NS, "g");
    lossGroup.setAttribute("class", "heatmap-sources-loss");
    lossGroup.setAttribute("filter", "url(#topology-heatmap-soften)");
    for (const node of currentVisibleTopology().nodes) {
      const source = heatmapSource(node.health, state.heatmapWindow);
      if (!source) continue;
      if (!heatmapOverlayVisible(source, node.health, {
        showHealthy: state.heatmapShowHealthy,
        showCritical: state.heatmapShowCritical,
      })) continue;
      if (!heatmapGlowVisible(node.health, state.heatmapWindow, state.heatmapMinLoss)) continue;
      const circle = document.createElementNS(SVG_NS, "circle");
      circle.setAttribute("cx", String(node.x * STAGE_WIDTH));
      circle.setAttribute("cy", String(node.y * STAGE_HEIGHT));
      circle.setAttribute("r", String(source.radius));
      circle.setAttribute("fill", `url(#topology-heatmap-gradient-${source.level})`);
      circle.setAttribute("opacity", String(source.opacity));
      (source.vivid ? healthyGroup : lossGroup).append(circle);
    }
    el.heatmapLayer.append(defs, healthyGroup, lossGroup);
  }

  function renderGraph() {
    const visible = currentVisibleTopology();
    el.empty.hidden = visible.nodes.length > 0;
    if (!visible.nodes.length && state.nodes.length) {
      el.emptyTitle.textContent = "No devices visible";
      el.emptyMessage.textContent = "Open Display and enable at least one device category.";
      el.emptyImport.hidden = true;
    } else if (!state.nodes.length) {
      el.emptyTitle.textContent = "No topology imported yet";
      el.emptyMessage.innerHTML = "Select the Lantopolog <strong>Export</strong> folder once. All useful CSV and XML files are imported together.";
      el.emptyImport.hidden = false;
    }
    el.edgeLayer.replaceChildren();
    for (const edge of visible.edges) {
      const source = nodeById(edge.source_id);
      const target = nodeById(edge.target_id);
      if (!source || !target) continue;
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.dataset.edgeId = edge.id;
      path.setAttribute("class", `topology-edge${edge.id === state.selectedEdgeId ? " is-selected" : ""}${edge.kind === "manual" ? " is-inferred" : ""}`);
      path.setAttribute("role", "button");
      path.setAttribute("aria-label", `${source.name} connected to ${target.name}`);
      path.addEventListener("click", (event) => { event.stopPropagation(); selectEdge(edge.id); });
      el.edgeLayer.append(path);
    }
    el.nodeLayer.replaceChildren();
    for (const node of visible.nodes) {
      const button = document.createElement("button");
      const shape = SHAPES.includes(node.node_shape) ? node.node_shape : "icon";
      const icon = resolveIconType(node);
      const vlanDecoration = state.vlanView ? vlanNodeDecoration(node, state.interfaces, state.vlans) : null;
      button.type = "button";
      button.className = `node-card node-shape-${shape} status-${nodeStatus(node)}`
        + `${state.selectedNodeIds.has(node.id) ? " is-selected" : ""}`
        + `${state.connectSourceId === node.id ? " is-connecting" : ""}`
        + `${vlanDecoration ? " has-vlan" : ""}`;
      button.dataset.nodeId = node.id;
      button.style.left = `${node.x * 100}%`;
      button.style.top = `${node.y * 100}%`;
      if (vlanDecoration) button.style.setProperty("--node-vlan-color", vlanDecoration.color);
      button.setAttribute("aria-label", nodeAriaLabel(node));
      const displayLabel = nodeDisplayLabel(node, state.labelMode);
      const symbolMarkup = shape === "text" ? "" : `<span class="node-symbol">${topologyIcon(icon)}</span>`;
      button.innerHTML = `${symbolMarkup}<span class="node-copy">`
        + `<strong>${escapeHtml(displayLabel.primary)}</strong>${displayLabel.secondary ? `<span>${escapeHtml(displayLabel.secondary)}</span>` : ""}</span>${vlanDecorationMarkup(vlanDecoration)}`;
      button.addEventListener("pointerdown", startNodeDrag);
      button.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          handleNodeChoice(node.id);
        }
      });
      el.nodeLayer.append(button);
      const children = unmanagedGroupChildren(state.nodes, state.edges, node.id);
      if (children.length) {
        const collapse = document.createElement("button");
        const isCollapsed = state.collapsedUnmanagedGroups.has(node.id);
        collapse.type = "button";
        collapse.className = "collapse-toggle";
        collapse.dataset.collapseGroupId = node.id;
        collapse.style.left = `${node.x * 100}%`;
        collapse.style.top = `${node.y * 100}%`;
        collapse.textContent = isCollapsed ? `+${children.length}` : "−";
        collapse.title = `${isCollapsed ? "Show" : "Hide"} ${children.length} devices below this inferred switch`;
        collapse.setAttribute("aria-label", collapse.title);
        collapse.setAttribute("aria-expanded", String(!isCollapsed));
        collapse.addEventListener("click", () => toggleUnmanagedGroup(node.id));
        el.nodeLayer.append(collapse);
      }
    }
    updateGraphPositions();
    renderHeatmapOverlay();
  }

  function updateGraphPositions() {
    el.nodeLayer.querySelectorAll("[data-node-id]").forEach((card) => {
      const node = nodeById(card.dataset.nodeId);
      if (!node) return;
      card.style.left = `${node.x * 100}%`;
      card.style.top = `${node.y * 100}%`;
    });
    el.nodeLayer.querySelectorAll("[data-collapse-group-id]").forEach((control) => {
      const node = nodeById(control.dataset.collapseGroupId);
      if (!node) return;
      control.style.left = `${node.x * 100}%`;
      control.style.top = `${node.y * 100}%`;
    });
    el.edgeLayer.querySelectorAll("[data-edge-id]").forEach((path) => {
      const edge = edgeById(path.dataset.edgeId);
      const source = edge && nodeById(edge.source_id);
      const target = edge && nodeById(edge.target_id);
      if (!source || !target) return;
      path.setAttribute("d", orthogonalEdgePath(
        source, target, {width: STAGE_WIDTH, height: STAGE_HEIGHT},
      ));
    });
    renderVlanDecorations();
    renderHeatmapOverlay();
  }

  function renderMetadata(container, metadata) {
    const entries = Object.entries(metadata || {}).filter(([, value]) => value !== "" && value != null);
    if (!entries.length) return;
    const title = document.createElement("h3");
    title.className = "section-title";
    title.textContent = "Imported details";
    container.append(title);
    const list = document.createElement("dl");
    list.className = "detail-list metadata-list";
    for (const [key, value] of entries) {
      const row = document.createElement("div");
      row.className = "detail-row";
      const term = document.createElement("dt");
      const detail = document.createElement("dd");
      term.textContent = key;
      detail.textContent = Array.isArray(value) ? value.join(", ") : String(value);
      row.append(term, detail);
      list.append(row);
    }
    container.append(list);
  }

  function syncSelection() {
    el.nodeLayer.querySelectorAll("[data-node-id]").forEach((card) => card.classList.toggle("is-selected", state.selectedNodeIds.has(card.dataset.nodeId)));
    renderInventory();
    updateModeHint();
  }

  function selectNode(id, additive = false) {
    if (additive) {
      const next = new Set(state.selectedNodeIds);
      if (next.has(id)) next.delete(id); else next.add(id);
      setNodeSelection(next);
      return;
    }
    state.selectedNodeIds = new Set([id]);
    state.selectedNodeId = id;
    state.selectedEdgeId = null;
    state.panelMode = "node";
    renderGraph();
    renderNodeDetails(id);
  }

  function setNodeSelection(ids) {
    state.selectedNodeIds = new Set(ids);
    state.selectedEdgeId = null;
    if (state.selectedNodeIds.size === 1) {
      const [id] = state.selectedNodeIds;
      state.selectedNodeId = id;
      state.panelMode = "node";
      renderNodeDetails(id);
    } else {
      state.selectedNodeId = null;
      state.panelMode = null;
      hidePanel(false);
    }
    renderGraph();
  }

  function selectEdge(id) {
    state.selectedEdgeId = id;
    state.selectedNodeIds = new Set();
    state.selectedNodeId = null;
    state.panelMode = "edge";
    renderInventory();
    renderGraph();
    renderEdgeDetails(id);
  }

  function hidePanel(clearMode = true) {
    if (clearMode) {
      state.selectedNodeId = null;
      state.selectedEdgeId = null;
      state.panelMode = null;
    }
    el.detail.hidden = true;
    el.detailContent.replaceChildren();
  }

  function clearSelection() {
    state.selectedNodeIds = new Set();
    state.selectedNodeId = null;
    state.selectedEdgeId = null;
    state.panelMode = null;
    hidePanel(false);
    renderInventory();
    renderGraph();
    updateModeHint();
  }

  function reconcileSelectionWithVisibleTopology() {
    const previousPanelMode = state.panelMode;
    const selection = reconcileVisibleSelection({
      selectedNodeIds: state.selectedNodeIds,
      selectedNodeId: state.selectedNodeId,
      selectedEdgeId: state.selectedEdgeId,
      panelMode: state.panelMode,
    }, currentVisibleTopology());
    state.selectedNodeIds = selection.selectedNodeIds;
    state.selectedNodeId = selection.selectedNodeId;
    state.selectedEdgeId = selection.selectedEdgeId;
    state.panelMode = selection.panelMode;
    if (previousPanelMode && !selection.panelMode) hidePanel(false);
  }

  function syncDisplayControls() {
    document.querySelectorAll("[data-display-type]").forEach((checkbox) => {
      const type = checkbox.dataset.displayType;
      checkbox.checked = !state.hiddenTypes.has(type)
        && !(type === "other" && state.hiddenTypes.has("unknown"));
    });
    el.hideManual.checked = state.hideManual;
    el.hideNoIp.checked = state.hideNoIp;
    el.labelMode.value = state.labelMode;
  }

  function applyDisplayOptions() {
    saveDisplayPreferences();
    reconcileSelectionWithVisibleTopology();
    renderInventory();
    renderGraph();
    syncDisplayControls();
  }

  function toggleUnmanagedGroup(groupId) {
    const next = new Set(state.collapsedUnmanagedGroups);
    if (next.has(groupId)) next.delete(groupId); else next.add(groupId);
    state.collapsedUnmanagedGroups = next;
    reconcileSelectionWithVisibleTopology();
    renderGraph();
  }

  function collapseUnmanagedGroups() {
    state.collapsedUnmanagedGroups = new Set(state.nodes
      .filter((node) => unmanagedGroupChildren(state.nodes, state.edges, node.id).length)
      .map(({id}) => id));
    reconcileSelectionWithVisibleTopology();
    renderGraph();
    fitGraph();
  }

  function expandUnmanagedGroups() {
    state.collapsedUnmanagedGroups = new Set();
    reconcileSelectionWithVisibleTopology();
    renderGraph();
    fitGraph();
  }

  function revealNode(id) {
    const node = nodeById(id);
    if (!node) return null;
    const hiddenTypes = new Set(state.hiddenTypes);
    hiddenTypes.delete(node.node_type);
    if (["other", "unknown"].includes(node.node_type)) {
      hiddenTypes.delete("other");
      hiddenTypes.delete("unknown");
    }
    state.hiddenTypes = hiddenTypes;
    if (isManualSource(node)) state.hideManual = false;
    const collapsed = new Set(state.collapsedUnmanagedGroups);
    collapsed.forEach((groupId) => {
      if (unmanagedGroupChildren(state.nodes, state.edges, groupId).includes(id)) collapsed.delete(groupId);
    });
    state.collapsedUnmanagedGroups = collapsed;
    saveDisplayPreferences();
    syncDisplayControls();
    return node;
  }

  function focusNode(id) {
    const node = revealNode(id);
    if (!node) return;
    selectNode(id);
    const rect = el.viewport.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    state.transform = focusTransform(
      node,
      {width: rect.width, height: rect.height},
      {width: STAGE_WIDTH, height: STAGE_HEIGHT},
      Math.max(0.8, Math.min(1.35, state.transform.scale || 1.15)),
    );
    state.fitted = true;
    applyTransform();
  }

  function focusFirstSearchResult() {
    const [node] = filteredNodes().sort((left, right) => left.name.localeCompare(right.name));
    if (node) focusNode(node.id);
    else toast("No matching device to focus", true);
  }

  function renderNodeDetails(id) {
    const node = nodeById(id);
    if (!node) return;
    const connections = state.edges.filter((edge) => edge.source_id === id || edge.target_id === id);
    const ports = state.interfaces.filter((item) => item.device_id === id);
    const health = node.health;
    const online = nodeStatus(node) === "online";
    const windowLabels = {"5m": "5 min", "15m": "15 min", "1h": "1 hour", "12h": "12 hours", "24h": "24 hours"};
    const lossRows = ["5m", "15m", "1h", "12h", "24h"].map((windowName) => {
      const value = healthWindowValue(health, windowName);
      const displayed = value.hasData ? `${value.loss.toFixed(1)}%${value.partial ? " (partial)" : ""}` : "—";
      const selected = state.heatmapEnabled && state.heatmapWindow === windowName ? " health-window-selected" : "";
      return `<div class="detail-row${selected}"><dt>${windowLabels[windowName]}</dt><dd>${displayed}</dd></div>`;
    }).join("");
    el.detail.hidden = false;
    el.detailContent.innerHTML = `<div class="panel-header"><div><h2>${escapeHtml(node.name)}</h2>`
      + `<p>${escapeHtml(node.address || node.mac_address || "No address")}</p></div>`
      + '<button class="close-panel" type="button" aria-label="Close panel">x</button></div>'
      + `<div class="panel-body"><div class="status-banner"><i class="status-${nodeStatus(node)}"></i><div><strong>${statusLabel(node)}</strong>`
      + `<span>${node.liveness_checked_at ? `Checked ${fmtTime(node.liveness_checked_at)}${node.liveness_latency_ms != null ? ` - ${node.liveness_latency_ms} ms` : ""}` : state.siteId !== "local" && !state.livenessAvailable ? statusLabel(node) : "No ICMP check yet"}</span></div></div>`
      + `<dl class="detail-list"><div class="detail-row"><dt>Type</dt><dd>${escapeHtml(node.node_type)}</dd></div>`
      + `<div class="detail-row"><dt>Visual</dt><dd>${escapeHtml(resolveIconType(node))} / ${escapeHtml(node.node_shape || "icon")}</dd></div>`
      + `<div class="detail-row"><dt>MAC</dt><dd>${escapeHtml(node.mac_address || "-")}</dd></div>`
      + `<div class="detail-row"><dt>Source</dt><dd>${node.source === "lantopolog" ? "Lantopolog export" : "Manual"}</dd></div>`
      + `<div class="detail-row"><dt>Known ports</dt><dd>${ports.length}</dd></div></dl>`
      + '<h3 class="section-title">Connectivity</h3><dl class="detail-list connectivity-list">'
      + `<div class="detail-row"><dt>Status</dt><dd>${health?.online === true ? "Online" : health?.online === false ? "Offline" : "Not checked"}</dd></div>`
      + `<div class="detail-row"><dt>Current RTT</dt><dd>${online && health?.current_rtt_ms != null ? `${Number(health.current_rtt_ms).toFixed(1)} ms` : "—"}</dd></div>`
      + `${!online && health?.last_rtt_ms != null ? `<div class="detail-row"><dt>Last RTT</dt><dd>${Number(health.last_rtt_ms).toFixed(1)} ms</dd></div>` : ""}`
      + `<div class="detail-row"><dt>Last response</dt><dd>${relativeTime(health?.last_success_at)}</dd></div>`
      + `<div class="detail-row"><dt>Monitoring</dt><dd class="monitoring-state">${health?.monitoring_state ? escapeHtml(health.monitoring_state) : "—"}</dd></div></dl>`
      + `<h3 class="section-title">Packet loss</h3><dl class="detail-list packet-loss-list">${lossRows}</dl>`
      + `${node.notes ? `<h3 class="section-title">Notes</h3><div class="source-callout">${escapeHtml(node.notes)}</div>` : ""}`
      + `<h3 class="section-title">Connections</h3><div class="connection-list">${connections.length
        ? connections.map((edge) => { const other = nodeById(edge.source_id === id ? edge.target_id : edge.source_id); return `<button class="connection-item" type="button" data-edge-id="${edge.id}">${escapeHtml(other?.name || "Unknown")}${edge.label ? ` - ${escapeHtml(edge.label)}` : ""}</button>`; }).join("")
        : '<span class="inventory-empty">No connections</span>'}</div>`
      + '<div class="panel-actions"><button id="edit-node" class="button button-secondary" type="button">Edit</button>'
      + '<button id="delete-node" class="button button-danger wide" type="button">Delete node</button></div></div>';
    renderMetadata(el.detailContent.querySelector(".panel-body"), node.metadata);
    el.detailContent.querySelector(".close-panel").addEventListener("click", () => hidePanel());
    el.detailContent.querySelectorAll("[data-edge-id]").forEach((button) => button.addEventListener("click", () => selectEdge(button.dataset.edgeId)));
    document.getElementById("edit-node").addEventListener("click", () => renderNodeEditor(node));
    document.getElementById("delete-node").addEventListener("click", () => deleteNode(node));
  }

  function renderEdgeDetails(id) {
    const edge = edgeById(id);
    if (!edge) return;
    const source = nodeById(edge.source_id);
    const target = nodeById(edge.target_id);
    el.detail.hidden = false;
    el.detailContent.innerHTML = `<div class="panel-header"><div><h2>Network connection</h2><p>${escapeHtml(edge.kind)}</p></div>`
      + '<button class="close-panel" type="button" aria-label="Close panel">x</button></div>'
      + `<div class="panel-body"><dl class="detail-list"><div class="detail-row"><dt>From</dt><dd>${escapeHtml(source?.name || "Unknown")}</dd></div>`
      + `<div class="detail-row"><dt>Port</dt><dd>${escapeHtml(edge.source_interface || "-")}</dd></div>`
      + `<div class="detail-row"><dt>To</dt><dd>${escapeHtml(target?.name || "Unknown")}</dd></div>`
      + `<div class="detail-row"><dt>Port</dt><dd>${escapeHtml(edge.target_interface || "-")}</dd></div>`
      + `<div class="detail-row"><dt>Evidence</dt><dd>${escapeHtml(edge.evidence || "User-created")}</dd></div></dl>`
      + '<div class="panel-actions"><button id="delete-edge" class="button button-danger wide" type="button">Delete connection</button></div></div>';
    renderMetadata(el.detailContent.querySelector(".panel-body"), edge.metadata);
    el.detailContent.querySelector(".close-panel").addEventListener("click", () => hidePanel());
    document.getElementById("delete-edge").addEventListener("click", async () => {
      if (!confirm(`Delete the connection between ${source?.name} and ${target?.name}?`)) return;
      try {
        await api(`/api/edges/${edge.id}`, {method: "DELETE"});
        clearSelection();
        await loadTopology(false);
        toast("Connection deleted");
      } catch (error) { toast(error.message, true); }
    });
  }

  function optionMarkup(values, selected, labels = {}) {
    return values.map((value) => `<option value="${value}"${selected === value ? " selected" : ""}>${labels[value] || value}</option>`).join("");
  }

  function renderNodeEditor(node = null, error = "") {
    const current = node || {
      name: "", address: "", notes: "", x: 0.5, y: 0.5, node_type: "unknown",
      icon_type: "auto", node_shape: "icon", mac_address: "", locked: true, metadata: {},
    };
    const metadata = current.metadata || {};
    const canEditVlans = !node || isManualSource(current);
    const vlanFields = canEditVlans
      ? `<div class="form-row"><label><span>Access VLAN</span><input id="edit-vlan" type="text" inputmode="numeric" maxlength="4" value="${escapeHtml(metadata.VLAN || "")}" placeholder="20"></label>`
        + `<label><span>Tagged VLANs</span><input id="edit-vlans" type="text" inputmode="numeric" maxlength="120" value="${escapeHtml(metadata.VLANs || "")}" placeholder="20, 30"></label></div>`
        + '<p class="form-help">Use VLAN IDs from 1-4094. Separate tagged VLANs with commas.</p>'
      : "";
    state.panelMode = "editor";
    el.detail.hidden = false;
    el.detailContent.innerHTML = `<div class="panel-header"><div><h2>${node ? "Edit node" : "Add node"}</h2><p>Topology inventory</p></div>`
      + '<button class="close-panel" type="button" aria-label="Close editor">x</button></div>'
      + `<div class="panel-body"><form id="node-form" class="node-form">${error ? `<div class="form-error">${escapeHtml(error)}</div>` : ""}`
      + `<label><span>Display name</span><input id="edit-name" type="text" required maxlength="100" value="${escapeHtml(current.name)}"></label>`
      + `<div class="form-row"><label><span>IP or hostname</span><input id="edit-address" type="text" maxlength="253" value="${escapeHtml(current.address || "")}"></label>`
      + `<label><span>Device type</span><select id="edit-type">${optionMarkup(["unknown", "router", "switch", "access-point", "server", "workstation", "printer", "phone", "other"], current.node_type)}</select></label></div>`
      + `<div class="form-row"><label><span>Icon</span><select id="edit-icon">${optionMarkup(ICON_TYPES, current.icon_type || "auto", {auto: "Automatic from type", "access-point": "Access point"})}</select></label>`
      + `<label><span>Shape</span><select id="edit-shape">${optionMarkup(SHAPES, current.node_shape || "icon", {icon: "Icon + label", card: "Info card", circle: "Circle", text: "Text label"})}</select></label></div>`
      + vlanFields
      + '<p class="form-help">Text label nodes show only their display name and remain draggable and selectable.</p>'
      + `<label><span>MAC address</span><input id="edit-mac" type="text" maxlength="32" value="${escapeHtml(current.mac_address || "")}"></label>`
      + `<label><span>Notes</span><textarea id="edit-notes" maxlength="2000">${escapeHtml(current.notes || "")}</textarea></label>`
      + `<label class="toggle"><span>Preserve edits on re-import</span><input id="edit-locked" type="checkbox" ${current.locked ? "checked" : ""}></label>`
      + `<button class="button button-primary" type="submit">${node ? "Save node" : "Add node"}</button></form></div>`;
    el.detailContent.querySelector(".close-panel").addEventListener("click", () => node ? selectNode(node.id) : clearSelection());
    document.getElementById("node-form").addEventListener("submit", (event) => saveNodeEditor(event, current));
  }

  async function saveNodeEditor(event, current) {
    event.preventDefault();
    const data = {
      name: document.getElementById("edit-name").value.trim(),
      address: document.getElementById("edit-address").value.trim(),
      notes: document.getElementById("edit-notes").value.trim(),
      x: current.x, y: current.y,
      node_type: document.getElementById("edit-type").value,
      icon_type: document.getElementById("edit-icon").value,
      node_shape: document.getElementById("edit-shape").value,
      mac_address: document.getElementById("edit-mac").value.trim(),
      locked: document.getElementById("edit-locked").checked,
    };
    try {
      const accessVlanInput = document.getElementById("edit-vlan");
      if (accessVlanInput) {
        const metadata = {...(current.metadata || {})};
        const accessVlans = normalizeManualVlanIds(accessVlanInput.value);
        const taggedVlans = normalizeManualVlanIds(document.getElementById("edit-vlans").value);
        if (accessVlans.ids.length > 1) throw new Error("Access VLAN must contain one VLAN ID");
        if (accessVlans.value) metadata.VLAN = accessVlans.value; else delete metadata.VLAN;
        if (taggedVlans.value) metadata.VLANs = taggedVlans.value; else delete metadata.VLANs;
        data.metadata = metadata;
      }
      const saved = current.id
        ? await api(`/api/devices/${current.id}`, {method: "PUT", body: JSON.stringify(data)})
        : await api("/api/devices", {method: "POST", body: JSON.stringify(data)});
      await loadTopology(false);
      selectNode(saved.id);
      toast(`${saved.name} saved`);
    } catch (error) { renderNodeEditor(current.id ? {...current, ...data} : null, error.message); }
  }

  async function deleteNode(node) {
    if (!confirm(`Delete ${node.name} and all of its connections?`)) return;
    try {
      await api(`/api/devices/${node.id}`, {method: "DELETE"});
      clearSelection();
      await loadTopology(false);
      toast(`${node.name} deleted`);
    } catch (error) { toast(error.message, true); }
  }

  async function clearAllNodes() {
    if (!state.nodes.length) {
      toast("There are no nodes to clear.", true);
      return;
    }
    if (!confirm("Delete every current node and its connections?")) return;
    try {
      const result = await api("/api/devices", {method: "DELETE"});
      clearSelection();
      await loadTopology(false);
      toast(result.deleted ? `Cleared ${result.deleted} nodes` : "Topology cleared");
    } catch (error) { toast(error.message, true); }
  }

  function handleNodeChoice(id) {
    if (!state.connecting) { selectNode(id); return; }
    if (!state.connectSourceId) {
      state.connectSourceId = id;
      el.modeHint.textContent = `Choose the node to connect to ${nodeById(id).name}`;
      renderGraph();
      return;
    }
    if (state.connectSourceId === id) { toast("Choose a different destination node", true); return; }
    createConnection(state.connectSourceId, id);
  }

  async function createConnection(sourceId, targetId) {
    try {
      await api("/api/edges", {method: "POST", body: JSON.stringify({source_id: sourceId, target_id: targetId, label: ""})});
      cancelConnect();
      await loadTopology(false);
      toast("Connection added");
    } catch (error) { toast(error.message, true); }
  }

  function cancelConnect() {
    state.connecting = false;
    state.connectSourceId = null;
    el.connectButton.setAttribute("aria-pressed", "false");
    el.connectButton.textContent = "Connect";
    el.viewport.classList.remove("is-connecting");
    updateModeHint();
    renderGraph();
  }

  function stagePoint(clientX, clientY) {
    const rect = el.viewport.getBoundingClientRect();
    return viewportToWorld(clientX, clientY, rect, state.transform, {width: STAGE_WIDTH, height: STAGE_HEIGHT});
  }

  function startNodeDrag(event) {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const card = event.currentTarget;
    const node = nodeById(card.dataset.nodeId);
    const originalSelection = new Set(state.selectedNodeIds);
    const additive = event.ctrlKey || event.metaKey || event.shiftKey;
    const dragIds = originalSelection.has(node.id)
      ? [...originalSelection]
      : additive ? [...originalSelection, node.id] : [node.id];
    state.dragging = {
      pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, moved: false,
      originalNodes: state.nodes.map((item) => ({...item})), originalSelection, dragIds, additive,
    };
    card.setPointerCapture(event.pointerId);

    const move = (moveEvent) => {
      const drag = state.dragging;
      if (!drag || moveEvent.pointerId !== drag.pointerId) return;
      if (Math.abs(moveEvent.clientX - drag.startX) + Math.abs(moveEvent.clientY - drag.startY) > 4) drag.moved = true;
      if (!drag.moved) return;
      if (!drag.selectionApplied) {
        state.selectedNodeIds = new Set(drag.dragIds);
        state.selectedNodeId = drag.dragIds.length === 1 ? drag.dragIds[0] : null;
        state.selectedEdgeId = null;
        state.panelMode = drag.dragIds.length === 1 ? "node" : null;
        if (drag.dragIds.length > 1) hidePanel(false);
        drag.selectionApplied = true;
        syncSelection();
      }
      const deltaX = (moveEvent.clientX - drag.startX) / state.transform.scale / STAGE_WIDTH;
      const deltaY = (moveEvent.clientY - drag.startY) / state.transform.scale / STAGE_HEIGHT;
      const positions = moveSelectedNodes(drag.originalNodes, drag.dragIds, deltaX, deltaY, {
        snap: state.snapToGrid, stepX: GRID_PIXELS / STAGE_WIDTH, stepY: GRID_PIXELS / STAGE_HEIGHT,
        minX: WORLD_MIN, maxX: WORLD_MAX, minY: WORLD_MIN, maxY: WORLD_MAX,
      });
      const byId = new Map(positions.map((position) => [position.id, position]));
      state.nodes = drag.originalNodes.map((item) => byId.has(item.id) ? {...item, ...byId.get(item.id), locked: true} : item);
      updateGraphPositions();
    };

    const finish = async (upEvent) => {
      card.removeEventListener("pointermove", move);
      card.removeEventListener("pointerup", finish);
      card.removeEventListener("pointercancel", finish);
      try { card.releasePointerCapture(upEvent.pointerId); } catch (_) { /* Already released. */ }
      const drag = state.dragging;
      state.dragging = null;
      if (!drag?.moved) {
        if (state.connecting) handleNodeChoice(node.id);
        else selectNode(node.id, drag?.additive);
        return;
      }
      const positions = state.nodes
        .filter((item) => drag.dragIds.includes(item.id))
        .map((item) => ({id: item.id, x: item.x, y: item.y}));
      try {
        const saved = await api("/api/devices/positions", {method: "PUT", body: JSON.stringify({positions})});
        const byId = new Map(saved.map((item) => [item.id, item]));
        state.nodes = state.nodes.map((item) => byId.get(item.id) || item);
        renderGraph();
        if (saved.length > 1) toast(`Moved ${saved.length} nodes`);
      } catch (error) {
        toast(error.message, true);
        await loadTopology(false);
      }
    };
    card.addEventListener("pointermove", move);
    card.addEventListener("pointerup", finish);
    card.addEventListener("pointercancel", finish);
  }

  function applyTransform() {
    const rect = el.viewport.getBoundingClientRect();
    if (rect.width && rect.height) {
      const visibleNodes = currentVisibleTopology().nodes;
      const nodeX = visibleNodes.map((node) => node.x * STAGE_WIDTH);
      const nodeY = visibleNodes.map((node) => node.y * STAGE_HEIGHT);
      state.transform = constrainPanToBounds(
        state.transform,
        {width: rect.width, height: rect.height},
        {
          left: Math.min(0, ...nodeX) - 90,
          top: Math.min(0, ...nodeY) - 70,
          right: Math.max(STAGE_WIDTH, ...nodeX) + 90,
          bottom: Math.max(STAGE_HEIGHT, ...nodeY) + 70,
        },
      );
    }
    el.stage.style.transform = `translate(${state.transform.x}px, ${state.transform.y}px) scale(${state.transform.scale})`;
    el.zoomLabel.textContent = `${Math.round(state.transform.scale * 100)}%`;
    el.viewport.style.setProperty("--grid-scale", state.transform.scale);
    el.viewport.style.setProperty("--grid-x", `${state.transform.x}px`);
    el.viewport.style.setProperty("--grid-y", `${state.transform.y}px`);
  }

  function fitGraph() {
    const rect = el.viewport.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    state.transform = fitTransform(
      currentVisibleTopology().nodes,
      {width: rect.width, height: rect.height},
      {width: STAGE_WIDTH, height: STAGE_HEIGHT},
    );
    state.fitted = true;
    applyTransform();
  }

  function zoom(factor) {
    state.transform = {...state.transform, scale: zoomScale(state.transform.scale, factor)};
    applyTransform();
  }

  async function saveLayout(positions, message) {
    try {
      await api("/api/devices/positions", {method: "PUT", body: JSON.stringify({positions})});
      await loadTopology(false);
      fitGraph();
      toast(message);
    } catch (error) { toast(error.message, true); }
  }

  function closeArrangeMenu() {
    el.arrangeOptions.removeAttribute("open");
  }

  async function saveCustomArrangement() {
    if (!state.nodes.length) return;
    const positions = state.nodes.map(({id, x, y}) => ({id, x, y}));
    try {
      await api("/api/layouts/custom", {method: "PUT", body: JSON.stringify({positions})});
      toast(`Saved the current arrangement for ${positions.length} devices`);
    } catch (error) { toast(error.message, true); }
    finally { closeArrangeMenu(); }
  }

  async function restoreCustomArrangement() {
    try {
      const saved = await api("/api/layouts/custom");
      if (!saved.exists) {
        toast("No custom arrangement has been saved yet", true);
        return;
      }
      const currentIds = new Set(state.nodes.map(({id}) => id));
      const positions = saved.positions.filter(({id}) => currentIds.has(id));
      if (!positions.length) {
        toast("The saved arrangement does not match the current topology", true);
        return;
      }
      const skipped = saved.positions.length - positions.length;
      await saveLayout(
        positions,
        `Restored ${positions.length} saved device position${positions.length === 1 ? "" : "s"}${skipped ? ` (${skipped} unavailable)` : ""}`,
      );
    } catch (error) { toast(error.message, true); }
    finally { closeArrangeMenu(); }
  }

  async function arrangeSubnetLayout() {
    const {nodes} = currentVisibleTopology();
    if (!nodes.length) {
      toast("No visible devices to arrange", true);
      return;
    }
    await saveLayout(arrangeNodesBySubnet(nodes), "Arranged visible devices into subnet groups");
    closeArrangeMenu();
  }

  async function arrangeSwitchLayout() {
    const {nodes, edges} = currentVisibleTopology();
    if (!nodes.length) {
      toast("No visible devices to arrange", true);
      return;
    }
    await saveLayout(arrangeNodesBySwitch(nodes, edges), "Arranged visible devices around their switches");
    closeArrangeMenu();
  }

  async function arrangeVlanLayout() {
    const {nodes} = currentVisibleTopology();
    if (!nodes.length) {
      toast("No visible devices to arrange", true);
      return;
    }
    const groups = vlanGroups(nodes, state.interfaces, state.vlans);
    if (!groups.some((group) => group.key !== "unassigned")) {
      toast("No VLAN assignments are available in this topology", true);
      return;
    }
    const positions = arrangeNodesByVlan(nodes, state.interfaces, state.vlans);
    try {
      await api("/api/devices/positions", {method: "PUT", body: JSON.stringify({positions})});
      state.vlanView = true;
      syncVlanViewControl();
      try { localStorage.setItem("topology-vlan-view", "true"); } catch (_) { /* Preference remains session-only. */ }
      await loadTopology(false);
      fitGraph();
      toast(`Arranged topology into ${groups.length} VLAN section${groups.length === 1 ? "" : "s"}`);
    } catch (error) { toast(error.message, true); }
    finally { closeArrangeMenu(); }
  }

  async function arrangeHierarchyLayout() {
    const {nodes, edges} = currentVisibleTopology();
    if (!nodes.length) {
      toast("No visible devices to arrange", true);
      return;
    }
    await saveLayout(
      arrangeNodesHierarchically(nodes, edges, {
        snap: state.snapToGrid,
        stepX: GRID_PIXELS / STAGE_WIDTH,
        stepY: GRID_PIXELS / STAGE_HEIGHT,
      }),
      `Arranged visible devices by connection level${state.snapToGrid ? " on the grid" : ""}`,
    );
    closeArrangeMenu();
  }

  function setTool(tool) {
    state.tool = tool;
    el.panTool.classList.toggle("is-active", tool === "pan");
    el.boxSelectTool.classList.toggle("is-active", tool === "select");
    el.panTool.setAttribute("aria-pressed", String(tool === "pan"));
    el.boxSelectTool.setAttribute("aria-pressed", String(tool === "select"));
    el.viewport.classList.toggle("is-box-selecting", tool === "select");
    updateModeHint();
  }

  function toggleSnapGrid() {
    state.snapToGrid = !state.snapToGrid;
    el.snapGrid.setAttribute("aria-pressed", String(state.snapToGrid));
    el.snapGrid.textContent = `Snap${state.snapToGrid ? " ✓" : ""}`;
    try { localStorage.setItem("topology-snap-grid", String(state.snapToGrid)); } catch (_) { /* Preference remains session-only. */ }
  }

  function syncInventoryControl() {
    el.workspace.classList.toggle("is-inventory-collapsed", state.inventoryCollapsed);
    el.inventoryToggle.setAttribute("aria-expanded", String(!state.inventoryCollapsed));
    const action = state.inventoryCollapsed ? "Expand inventory" : "Collapse inventory";
    el.inventoryToggle.setAttribute("aria-label", action);
    el.inventoryToggle.title = action;
    el.inventoryToggle.textContent = state.inventoryCollapsed ? "›" : "‹";
  }

  function toggleInventory() {
    state.inventoryCollapsed = !state.inventoryCollapsed;
    syncInventoryControl();
    try {
      localStorage.setItem("topology-inventory-collapsed", String(state.inventoryCollapsed));
    } catch (_) { /* Preference remains session-only. */ }
    requestAnimationFrame(fitGraph);
  }

  function syncVlanViewControl() {
    el.vlanView.setAttribute("aria-pressed", String(state.vlanView));
    el.vlanView.textContent = `VLAN${state.vlanView ? " ✓" : ""}`;
  }

  function toggleVlanView() {
    state.vlanView = !state.vlanView;
    syncVlanViewControl();
    try { localStorage.setItem("topology-vlan-view", String(state.vlanView)); } catch (_) { /* Preference remains session-only. */ }
    renderGraph();
  }

  function syncHeatmapControls() {
    const enabled = state.heatmapEnabled;
    el.heatmapToggle.checked = enabled;
    el.heatmapWindow.disabled = !enabled;
    el.heatmapWindow.value = state.heatmapWindow;
    el.heatmapShowHealthy.disabled = !enabled;
    el.heatmapShowCritical.disabled = !enabled;
    el.heatmapShowHealthy.checked = state.heatmapShowHealthy;
    el.heatmapShowCritical.checked = state.heatmapShowCritical;
    el.heatmapMinLoss.disabled = !enabled;
    el.heatmapMinLossSlider.disabled = !enabled;
    el.heatmapMinLoss.value = state.heatmapMinLoss > 0 ? String(state.heatmapMinLoss) : "";
    el.heatmapMinLossSlider.value = String(state.heatmapMinLoss > 0 ? state.heatmapMinLoss : 0.1);
    el.heatmapMenuSummary.textContent = enabled
      ? `Loss ✓${state.heatmapMinLoss > 0 ? ` ≥${state.heatmapMinLoss}%` : ""} ▾`
      : "Loss ▾";
  }

  function applyHeatmapMinLossChange(rawValue) {
    state.heatmapMinLoss = normalizeHeatmapMinLoss(rawValue);
    syncHeatmapControls();
    try { localStorage.setItem("topology-health-min-loss", String(state.heatmapMinLoss)); } catch (_) { /* Session-only. */ }
    renderGraph();
  }

  function onHeatmapToggleChange() {
    state.heatmapEnabled = el.heatmapToggle.checked;
    syncHeatmapControls();
    try { localStorage.setItem("topology-health-heatmap", String(state.heatmapEnabled)); } catch (_) { /* Session-only. */ }
    renderGraph();
    if (state.panelMode === "node" && state.selectedNodeId) renderNodeDetails(state.selectedNodeId);
  }

  function onHeatmapLayerToggleChange() {
    state.heatmapShowHealthy = el.heatmapShowHealthy.checked;
    state.heatmapShowCritical = el.heatmapShowCritical.checked;
    try {
      localStorage.setItem("topology-health-heatmap-show-healthy", String(state.heatmapShowHealthy));
      localStorage.setItem("topology-health-heatmap-show-critical", String(state.heatmapShowCritical));
    } catch (_) { /* Session-only. */ }
    renderGraph();
  }

  function selectHeatmapWindow() {
    state.heatmapWindow = el.heatmapWindow.value;
    try { localStorage.setItem("topology-health-window", state.heatmapWindow); } catch (_) { /* Session-only. */ }
    renderGraph();
    if (state.panelMode === "node" && state.selectedNodeId) renderNodeDetails(state.selectedNodeId);
  }

  function updateModeHint() {
    if (state.connecting) return;
    if (state.selectedNodeIds.size > 1) {
      el.modeHint.textContent = `${state.selectedNodeIds.size} nodes selected - drag any selected node to move the group`;
    } else if (state.tool === "select") {
      el.modeHint.textContent = "Drag a box around nodes - Ctrl/Shift adds to the selection";
    } else {
      el.modeHint.textContent = "Pan the canvas or drag nodes to arrange them - Shift-drag selects a box";
    }
  }

  function selectedExportPath(file) {
    const parts = String(file.webkitRelativePath || file.name).replaceAll("\\", "/").split("/");
    const name = parts.at(-1).toLowerCase();
    return parts.some((part) => part.toLowerCase() === "tmp") ? `Tmp/${name}` : name;
  }

  async function importFolder(fileList) {
    const requestSite = state.siteId;
    const selected = [...fileList].filter((file) => EXPORT_FILES.has(file.name.toLowerCase()));
    if (!selected.length) { toast("That folder does not contain a recognized Lantopolog export", true); return; }
    el.importButton.disabled = true;
    el.importButton.textContent = "Importing...";
    try {
      const files = await Promise.all(selected.map(async (file) => ({name: file.name, path: selectedExportPath(file), content: await file.text()})));
      const result = await api("/api/import/lantopolog", {method: "POST", body: JSON.stringify({files}), siteId: requestSite});
      if (state.siteId !== requestSite) return;
      state.fittedForNodes = false;
      await loadTopology(false);
      state.fitted = false;
      fitGraph();
      toast(`Imported ${result.summary.nodes} nodes and ${result.summary.connections} connections`);
    } catch (error) { toast(`Import failed: ${error.message}`, true); }
    finally {
      el.importButton.disabled = false;
      el.importButton.textContent = "Import";
      el.importFolder.value = "";
    }
  }

  function beginViewportGesture(event) {
    if (event.button !== 0 || event.target.closest(".node-card")
      || event.target.closest(".collapse-toggle") || event.target.closest(".topology-edge")) return;
    const useMarquee = state.tool === "select" || event.shiftKey;
    if (useMarquee) {
      const rect = el.viewport.getBoundingClientRect();
      state.marquee = {
        id: event.pointerId, startClientX: event.clientX, startClientY: event.clientY,
        start: stagePoint(event.clientX, event.clientY),
        left: event.clientX - rect.left, top: event.clientY - rect.top,
        additive: shouldAddMarqueeSelection(state.tool, event),
      };
      el.selectionBox.hidden = false;
      Object.assign(el.selectionBox.style, {left: `${state.marquee.left}px`, top: `${state.marquee.top}px`, width: "0px", height: "0px"});
    } else {
      state.panning = {id: event.pointerId, x: event.clientX, y: event.clientY, ox: state.transform.x, oy: state.transform.y};
      el.viewport.classList.add("is-panning");
    }
    el.viewport.setPointerCapture(event.pointerId);
  }

  function moveViewportGesture(event) {
    if (state.panning && event.pointerId === state.panning.id) {
      state.transform = {...state.transform, x: state.panning.ox + event.clientX - state.panning.x, y: state.panning.oy + event.clientY - state.panning.y};
      applyTransform();
      return;
    }
    if (!state.marquee || event.pointerId !== state.marquee.id) return;
    const rect = el.viewport.getBoundingClientRect();
    const currentLeft = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
    const currentTop = Math.max(0, Math.min(rect.height, event.clientY - rect.top));
    Object.assign(el.selectionBox.style, {
      left: `${Math.min(state.marquee.left, currentLeft)}px`,
      top: `${Math.min(state.marquee.top, currentTop)}px`,
      width: `${Math.abs(currentLeft - state.marquee.left)}px`,
      height: `${Math.abs(currentTop - state.marquee.top)}px`,
    });
    state.marquee.current = stagePoint(event.clientX, event.clientY);
  }

  function finishViewportGesture(event) {
    if (state.panning && event.pointerId === state.panning.id) {
      state.panning = null;
      el.viewport.classList.remove("is-panning");
    } else if (state.marquee && event.pointerId === state.marquee.id) {
      const marquee = state.marquee;
      state.marquee = null;
      el.selectionBox.hidden = true;
      const moved = Math.abs(event.clientX - marquee.startClientX) + Math.abs(event.clientY - marquee.startClientY) > 4;
      if (!moved) {
        if (!marquee.additive) clearSelection();
      } else {
        const current = marquee.current || stagePoint(event.clientX, event.clientY);
        const selected = nodesInsideBox(currentVisibleTopology().nodes, {
          x1: marquee.start.x, y1: marquee.start.y, x2: current.x, y2: current.y,
        });
        setNodeSelection(marquee.additive ? new Set([...state.selectedNodeIds, ...selected]) : new Set(selected));
      }
    } else return;
    try { el.viewport.releasePointerCapture(event.pointerId); } catch (_) { /* Already released. */ }
  }

  function cancelViewportGesture(event) {
    const cancelled = cancelViewportGestures(state.panning, state.marquee, event.pointerId);
    state.panning = cancelled.panning;
    state.marquee = cancelled.marquee;
    if (cancelled.cancelledPan) {
      el.viewport.classList.remove("is-panning");
    }
    if (cancelled.cancelledMarquee) {
      el.selectionBox.hidden = true;
    }
    if (!cancelled.cancelledPan && !cancelled.cancelledMarquee) return;
    try { el.viewport.releasePointerCapture(event.pointerId); } catch (_) { /* Already released. */ }
  }

  async function saveTopologyPdf() {
    const button = document.getElementById("print-topology");
    const siteId = state.siteId;
    const visible = currentVisibleTopology();
    if (!visible.nodes.length) {
      toast("There are no visible devices to save.", true);
      return;
    }
    button.disabled = true;
    button.textContent = "Saving PDF...";
    try {
      const response = await fetch("/api/exports/topology.pdf", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Status-Visualizer-Request": "1",
          "X-Status-Visualizer-Site": siteId,
        },
        body: JSON.stringify({
          node_ids: visible.nodes.map((node) => node.id),
          label_mode: state.labelMode,
          vlan_view: state.vlanView,
          theme: state.theme,
        }),
      });
      if (state.siteId !== siteId) return;
      if (!response.ok) {
        let message = `${response.status} ${response.statusText}`;
        try { message = (await response.json()).detail || message; }
        catch (_) { /* Keep the safe status fallback. */ }
        throw new Error(message);
      }
      const blob = await response.blob();
      if (state.siteId !== siteId) return;
      if (!blob.type.startsWith("application/pdf")) throw new Error("The export did not return a PDF");
      const objectUrl = URL.createObjectURL(blob);
      const download = document.createElement("a");
      const stamp = new Date().toISOString().replaceAll(/[:.]/g, "-");
      download.href = objectUrl;
      download.download = `status-visualizer-topology-${stamp}.pdf`;
      document.body.append(download);
      download.click();
      download.remove();
      setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
      toast(`Saved a vector PDF with ${visible.nodes.length} visible devices.`);
    } catch (error) {
      if (state.siteId === siteId) toast(`Could not save PDF: ${error.message}`, true);
    } finally {
      button.disabled = false;
      button.textContent = "Save diagram as PDF";
    }
  }

  function closeContainingMenu(control) {
    control.closest("details")?.removeAttribute("open");
  }

  function closeAllMenus() {
    document.querySelectorAll(".toolbar-menu[open]").forEach((menu) => menu.removeAttribute("open"));
  }

  el.importButton.addEventListener("click", () => el.importFolder.click());
  el.checkLiveness.addEventListener("click", () => {
    closeContainingMenu(el.checkLiveness);
    checkLiveness();
  });
  el.siteSelector.addEventListener("change", (event) => selectSite(event.target.value));
  el.mqttClientsButton.addEventListener("click", () => {
    el.mqttClientsPanel.hidden = !el.mqttClientsPanel.hidden;
    el.mqttClientsButton.setAttribute("aria-expanded", String(!el.mqttClientsPanel.hidden));
  });
  document.getElementById("close-mqtt-clients").addEventListener("click", () => {
    el.mqttClientsPanel.hidden = true;
    el.mqttClientsButton.setAttribute("aria-expanded", "false");
  });
  el.themeToggle.addEventListener("click", () => {
    toggleTheme();
    closeContainingMenu(el.themeToggle);
  });
  document.getElementById("empty-import").addEventListener("click", () => el.importFolder.click());
  el.importFolder.addEventListener("change", () => importFolder(el.importFolder.files));
  document.getElementById("add-button").addEventListener("click", (event) => {
    closeContainingMenu(event.currentTarget);
    renderNodeEditor();
  });
  document.getElementById("clear-nodes").addEventListener("click", (event) => {
    closeContainingMenu(event.currentTarget);
    clearAllNodes();
  });
  el.connectButton.addEventListener("click", () => {
    if (state.connecting) { cancelConnect(); return; }
    state.connecting = true;
    state.connectSourceId = null;
    el.connectButton.setAttribute("aria-pressed", "true");
    el.connectButton.textContent = "Cancel";
    el.viewport.classList.add("is-connecting");
    el.modeHint.textContent = "Choose the first node to connect";
  });
  el.panTool.addEventListener("click", () => setTool("pan"));
  el.boxSelectTool.addEventListener("click", () => setTool("select"));
  el.snapGrid.addEventListener("click", toggleSnapGrid);
  el.vlanView.addEventListener("click", toggleVlanView);
  el.heatmapToggle.addEventListener("change", onHeatmapToggleChange);
  el.heatmapShowHealthy.addEventListener("change", onHeatmapLayerToggleChange);
  el.heatmapShowCritical.addEventListener("change", onHeatmapLayerToggleChange);
  el.heatmapWindow.addEventListener("change", selectHeatmapWindow);
  el.heatmapMinLossSlider.addEventListener("input", () => {
    applyHeatmapMinLossChange(el.heatmapMinLossSlider.value);
  });
  el.heatmapMinLoss.addEventListener("change", () => {
    applyHeatmapMinLossChange(el.heatmapMinLoss.value);
  });
  el.heatmapMinLoss.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      applyHeatmapMinLossChange(el.heatmapMinLoss.value);
    }
  });
  el.arrangeHierarchy.addEventListener("click", arrangeHierarchyLayout);
  el.arrangeSwitch.addEventListener("click", arrangeSwitchLayout);
  el.arrangeVlan.addEventListener("click", arrangeVlanLayout);
  el.arrangeSubnet.addEventListener("click", arrangeSubnetLayout);
  el.saveCustomArrangement.addEventListener("click", saveCustomArrangement);
  el.restoreCustomArrangement.addEventListener("click", restoreCustomArrangement);
  el.inventoryToggle.addEventListener("click", toggleInventory);
  document.getElementById("collapse-unmanaged").addEventListener("click", collapseUnmanagedGroups);
  document.getElementById("expand-unmanaged").addEventListener("click", expandUnmanagedGroups);
  document.getElementById("print-topology").addEventListener("click", (event) => {
    closeContainingMenu(event.currentTarget);
    saveTopologyPdf();
  });
  document.getElementById("zoom-in").addEventListener("click", () => zoom(1.2));
  document.getElementById("zoom-out").addEventListener("click", () => zoom(0.83));
  document.getElementById("zoom-fit").addEventListener("click", fitGraph);
  el.search.addEventListener("input", (event) => { state.search = event.target.value; renderInventory(); });
  el.search.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); focusFirstSearchResult(); }
  });
  el.focusSearch.addEventListener("click", focusFirstSearchResult);
  document.querySelectorAll(".toolbar-menu").forEach((menu) => menu.addEventListener("toggle", () => {
    if (!menu.open) return;
    document.querySelectorAll(".toolbar-menu[open]").forEach((other) => {
      if (other !== menu) other.removeAttribute("open");
    });
  }));
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".toolbar-menu")) closeAllMenus();
  });
  document.querySelectorAll("[data-display-type]").forEach((checkbox) => checkbox.addEventListener("change", () => {
    const next = new Set(state.hiddenTypes);
    const types = checkbox.dataset.displayType === "other" ? ["other", "unknown"] : [checkbox.dataset.displayType];
    types.forEach((type) => { if (checkbox.checked) next.delete(type); else next.add(type); });
    state.hiddenTypes = next;
    applyDisplayOptions();
  }));
  el.hideManual.addEventListener("change", () => {
    state.hideManual = el.hideManual.checked;
    applyDisplayOptions();
  });
  el.hideNoIp.addEventListener("change", () => {
    state.hideNoIp = el.hideNoIp.checked;
    applyDisplayOptions();
  });
  el.labelMode.addEventListener("change", () => {
    state.labelMode = el.labelMode.value;
    applyDisplayOptions();
  });
  document.querySelectorAll(".filter-button").forEach((button) => button.addEventListener("click", () => {
    state.filter = button.dataset.filter;
    document.querySelectorAll(".filter-button").forEach((item) => item.classList.toggle("is-active", item === button));
    renderInventory();
  }));
  el.viewport.addEventListener("wheel", (event) => { event.preventDefault(); zoom(event.deltaY < 0 ? 1.12 : 0.89); }, {passive: false});
  el.viewport.addEventListener("pointerdown", beginViewportGesture);
  el.viewport.addEventListener("pointermove", moveViewportGesture);
  el.viewport.addEventListener("pointerup", finishViewportGesture);
  el.viewport.addEventListener("pointercancel", cancelViewportGesture);
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (document.querySelector(".toolbar-menu[open]")) {
      closeAllMenus();
      return;
    }
    if (!state.connecting) clearSelection();
  });
  new ResizeObserver(() => state.fitted ? applyTransform() : fitGraph()).observe(el.viewport);
  window.addEventListener("load", fitGraph, {once: true});
  el.snapGrid.setAttribute("aria-pressed", String(state.snapToGrid));
  el.snapGrid.textContent = `Snap${state.snapToGrid ? " ✓" : ""}`;
  syncVlanViewControl();
  syncHeatmapControls();
  applyTheme(state.theme);
  syncDisplayControls();
  syncInventoryControl();
  setTool("pan");
  refreshSitesAndClients(true).finally(async () => {
    await loadTopology();
    await loadLiveness(false);
  });
  setInterval(() => loadLiveness(false), 10000);
  setInterval(refreshSitesAndClients, 10000);
})();
