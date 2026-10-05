from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
STYLES = (ROOT / "app" / "static" / "styles.css").read_text(encoding="utf-8")


def test_ui_performs_one_folder_selection_and_one_import_request() -> None:
    assert 'id="import-folder"' in INDEX_HTML
    assert "webkitdirectory" in INDEX_HTML
    assert 'id="import-button"' in INDEX_HTML
    assert 'api("/api/import/lantopolog"' in APP_JS
    assert "Promise.all" in APP_JS


def test_ui_exposes_a_clear_all_nodes_action() -> None:
    assert 'id="clear-nodes"' in INDEX_HTML
    assert '>Clear topology</button>' in INDEX_HTML
    assert 'class="menu-action menu-action-danger"' in INDEX_HTML
    assert "clearAllNodes" in APP_JS
    assert 'api("/api/devices", {method: "DELETE"})' in APP_JS


def test_obsolete_discovery_and_monitoring_controls_are_gone() -> None:
    combined = INDEX_HTML + APP_JS
    assert "Run discovery" not in combined
    assert "/api/discovery" not in combined
    assert "Unmatched monitoring" not in combined
    assert "SNMP credential" not in combined


def test_import_metadata_is_rendered_as_text_not_raw_html() -> None:
    assert "renderMetadata" in APP_JS
    assert "textContent" in APP_JS
    assert "dangerouslySetInnerHTML" not in APP_JS


def test_editor_has_bounded_canvas_selection_and_grid_controls() -> None:
    assert 'id="box-select-tool"' in INDEX_HTML
    assert 'id="pan-tool"' in INDEX_HTML
    assert 'id="snap-grid"' in INDEX_HTML
    assert 'id="selection-box"' in INDEX_HTML
    assert "--workspace-height" in STYLES
    assert "selectedNodeIds" in APP_JS
    assert "/api/devices/positions" in APP_JS


def test_topology_can_show_and_arrange_vlan_membership() -> None:
    assert 'id="vlan-view"' in INDEX_HTML
    assert 'id="arrange-vlan"' in INDEX_HTML
    assert 'id="vlan-layer"' in INDEX_HTML
    assert 'id="vlan-legend"' in INDEX_HTML
    assert "arrangeNodesByVlan" in APP_JS
    assert "nodeVlanIds" in APP_JS
    assert "vlanNodeDecoration" in APP_JS
    assert "vlan-zone" not in STYLES
    assert "vlan-stripe" in STYLES
    assert "vlan-badge" in STYLES


def test_vlan_view_colors_nodes_without_rendering_overlapping_zones() -> None:
    assert "vlanZones" not in APP_JS
    assert "vlan-zone" not in APP_JS
    assert ".vlan-zone" not in STYLES
    assert "vlanNodeDecoration" in APP_JS
    assert "vlan-stripe" in APP_JS
    assert ".vlan-stripe" in STYLES
    assert 'id="vlan-legend"' in INDEX_HTML
    assert "vlan-legend-item" in APP_JS
    assert "vlan-badge" in APP_JS


def test_toolbar_can_arrange_topology_as_a_hierarchy() -> None:
    assert 'id="arrange-hierarchy"' in INDEX_HTML
    assert '>Arrange hierarchy</button>' in INDEX_HTML
    assert "arrangeNodesHierarchically" in APP_JS


def test_toolbar_exposes_explicit_switch_and_subnet_layout_actions() -> None:
    assert 'id="arrange-switch"' in INDEX_HTML
    assert '>Arrange by switch</button>' in INDEX_HTML
    assert 'id="arrange-subnet"' in INDEX_HTML
    assert '>Arrange by subnet</button>' in INDEX_HTML
    assert "arrangeNodesBySwitch" in APP_JS
    assert "arrangeNodesBySubnet" in APP_JS


def test_arrangement_actions_are_grouped_with_custom_save_and_restore() -> None:
    assert 'id="arrange-options"' in INDEX_HTML
    assert 'aria-label="Arrangement options"' in INDEX_HTML
    assert '<summary class="text-button">Arrange ▾</summary>' in INDEX_HTML
    for action_id in (
        "arrange-hierarchy",
        "arrange-switch",
        "arrange-vlan",
        "arrange-subnet",
        "save-custom-arrangement",
        "restore-custom-arrangement",
    ):
        assert f'id="{action_id}"' in INDEX_HTML
    assert "saveCustomArrangement" in APP_JS
    assert "restoreCustomArrangement" in APP_JS


def test_inventory_has_an_accessible_collapse_control() -> None:
    assert 'id="inventory-panel"' in INDEX_HTML
    assert 'id="toggle-inventory"' in INDEX_HTML
    assert 'aria-controls="inventory-panel"' in INDEX_HTML
    assert 'aria-expanded="true"' in INDEX_HTML
    assert "toggleInventory" in APP_JS
    assert "is-inventory-collapsed" in STYLES


def test_display_options_control_filters_categories_and_selects_node_labels() -> None:
    assert 'id="display-options"' in INDEX_HTML
    assert 'aria-label="Display options"' in INDEX_HTML
    for node_type in (
        "router", "switch", "access-point", "server", "workstation", "printer", "phone", "other"
    ):
        assert f'data-display-type="{node_type}"' in INDEX_HTML
    assert 'id="hide-manual"' in INDEX_HTML
    assert 'id="label-mode"' in INDEX_HTML
    assert '<option value="hostname"' in INDEX_HTML
    assert '<option value="ip"' in INDEX_HTML
    assert '<option value="both"' in INDEX_HTML
    assert "hiddenTypes" in APP_JS
    assert "hideManual" in APP_JS
    assert 'id="hide-no-ip"' in INDEX_HTML
    assert "hideNoIp" in APP_JS
    assert "labelMode" in APP_JS


def test_unmanaged_switch_groups_can_be_collapsed_and_expanded() -> None:
    assert 'id="collapse-unmanaged"' in INDEX_HTML
    assert 'id="expand-unmanaged"' in INDEX_HTML
    assert "collapsedUnmanagedGroups" in APP_JS
    assert "collapseUnmanagedGroups" in APP_JS
    assert "expandUnmanagedGroups" in APP_JS


def test_inventory_items_offer_an_explicit_focus_action() -> None:
    assert "inventory-focus" in APP_JS
    assert 'data-focus-node-id' in APP_JS
    assert "focusNode" in APP_JS


def test_topology_has_a_direct_vector_pdf_export() -> None:
    assert 'id="print-topology"' in INDEX_HTML
    assert '>Save diagram as PDF</button>' in INDEX_HTML
    assert 'id="export-options"' in INDEX_HTML
    assert "saveTopologyPdf" in APP_JS
    assert '"/api/exports/topology.pdf"' in APP_JS
    assert "node_ids" in APP_JS
    assert "label_mode" in APP_JS
    assert "vlan_view" in APP_JS
    assert "theme: state.theme" in APP_JS
    assert '"X-Status-Visualizer-Request": "1"' in APP_JS
    assert "response.blob()" in APP_JS
    assert "URL.createObjectURL" in APP_JS
    assert ".download" in APP_JS


def test_light_and_dark_mode_are_accessible_and_persisted() -> None:
    assert '<html lang="en" data-theme="dark">' in INDEX_HTML
    assert 'id="theme-toggle"' in INDEX_HTML
    assert 'aria-pressed="false"' in INDEX_HTML
    assert "readThemePreference" in APP_JS
    assert "toggleTheme" in APP_JS
    assert 'localStorage.setItem("topology-theme"' in APP_JS
    assert "document.documentElement.dataset.theme" in APP_JS
    assert '[data-theme="light"]' in STYLES
    assert "color-scheme: light" in STYLES


def test_text_labels_have_theme_specific_surface_contrast() -> None:
    assert "--text-label-panel:" in STYLES
    assert "--text-label-border:" in STYLES
    assert "--text-label-text-shadow:" in STYLES
    assert "border: 1px solid var(--text-label-border)" in STYLES
    assert "background: var(--text-label-panel)" in STYLES
    assert "text-shadow: var(--text-label-text-shadow)" in STYLES
    assert ':root[data-theme="light"]' in STYLES
    assert "--text-label-text-shadow: none" in STYLES
    generic_status_rule = (
        ".node-card.status-online,.node-card.status-offline,.node-card.status-unknown "
        "{ background: var(--node-panel); }"
    )
    text_label_status_rule = (
        ".node-card.node-shape-text.status-online,.node-card.node-shape-text.status-offline,"
        ".node-card.node-shape-text.status-unknown { background: var(--text-label-panel); }"
    )
    assert generic_status_rule in STYLES
    assert text_label_status_rule in STYLES
    assert STYLES.index(text_label_status_rule) > STYLES.index(generic_status_rule)


def test_pdf_export_does_not_print_or_clone_the_browser_page() -> None:
    combined = APP_JS + INDEX_HTML
    assert "window.print" not in combined
    assert "printPreview" not in combined
    assert 'addEventListener("beforeprint"' not in combined
    assert 'addEventListener("afterprint"' not in combined
    assert 'id="print-heading"' not in INDEX_HTML


def test_pdf_export_reports_conversion_or_download_failures() -> None:
    assert "try {" in APP_JS
    assert "catch" in APP_JS
    assert "Could not save PDF" in APP_JS
    assert "application/pdf" in APP_JS


def test_hidden_topology_state_is_explained_and_selection_is_reconciled() -> None:
    assert 'id="graph-empty-title"' in INDEX_HTML
    assert 'id="graph-empty-message"' in INDEX_HTML
    assert "No devices visible" in APP_JS
    assert "reconcileVisibleSelection" in APP_JS


def test_connections_render_as_orthogonal_svg_paths() -> None:
    assert "orthogonalEdgePath" in APP_JS
    assert 'createElementNS("http://www.w3.org/2000/svg", "path")' in APP_JS
    assert 'setAttribute("d"' in APP_JS
    assert 'createElementNS("http://www.w3.org/2000/svg", "line")' not in APP_JS
    assert "stroke-linejoin: round" in STYLES


def test_nodes_support_selectable_network_icons_and_shapes() -> None:
    assert 'src="/static/topology-utils.js"' in INDEX_HTML
    assert "topologyIcon" in APP_JS
    assert 'id="edit-icon"' in APP_JS
    assert 'id="edit-shape"' in APP_JS
    assert 'id="edit-vlan"' in APP_JS
    assert 'id="edit-vlans"' in APP_JS
    assert "normalizeManualVlanIds" in APP_JS
    assert 'shape === "text" ? ""' in APP_JS
    assert "Use VLAN IDs from 1-4094" in APP_JS
    assert "Text label" in APP_JS
    assert "node-symbol" in STYLES
    assert "node-shape-text" in STYLES


def test_ui_exposes_current_liveness_without_monitoring_history() -> None:
    assert 'id="check-liveness"' in INDEX_HTML
    assert 'api("/api/liveness/check"' in APP_JS
    assert 'api("/api/liveness"' in APP_JS
    assert '"X-Status-Visualizer-Request": "1"' in APP_JS
    assert "liveness_state" in APP_JS
    assert "history" not in (INDEX_HTML + APP_JS).lower()


def test_pointer_cancellation_aborts_marquee_without_changing_selection() -> None:
    assert 'addEventListener("pointercancel", cancelViewportGesture)' in APP_JS
    assert "function cancelViewportGesture" in APP_JS


def test_obsolete_monitoring_visual_states_are_removed() -> None:
    combined = APP_JS + INDEX_HTML + STYLES
    assert "status-degraded" not in combined
    assert "is-running" not in combined
    assert "--degraded" not in combined


def test_dashboard_exposes_site_selection_and_pending_mqtt_clients() -> None:
    assert 'id="site-selector"' in INDEX_HTML
    assert 'id="mqtt-clients-button"' in INDEX_HTML
    assert 'id="mqtt-clients-panel"' in INDEX_HTML
    assert 'id="mqtt-clients-list"' in INDEX_HTML
    assert 'api("/api/sites"' in APP_JS
    assert 'api("/api/mqtt/clients"' in APP_JS
    assert 'api(`/api/mqtt/clients/${clientId}`' in APP_JS
    assert "captureMqttClientEditorState" in APP_JS
    assert 'method: "PATCH"' in APP_JS
    assert 'updateMqttClient(clientId, "approved"' in APP_JS
    assert 'updateMqttClient(clientId, "blocked"' in APP_JS


def test_header_and_toolbar_actions_have_clear_hierarchy() -> None:
    assert 'id="app-options"' in INDEX_HTML
    assert ">Manage sites " in INDEX_HTML
    assert 'id="liveness-status"' in INDEX_HTML
    assert 'class="toolbar-group-label">Edit</span>' in INDEX_HTML
    assert 'class="toolbar-group-label">View</span>' in INDEX_HTML
    assert 'id="add-options"' in INDEX_HTML
    assert INDEX_HTML.index('id="connect-button"') > INDEX_HTML.index('class="graph-toolbar"')
    assert INDEX_HTML.index('id="clear-nodes"') < INDEX_HTML.index('class="stats-bar"')
    assert "closeAllMenus" in APP_JS


def test_topology_requests_are_scoped_to_the_selected_site_per_tab() -> None:
    assert 'sessionStorage.getItem("status-visualizer-site")' in APP_JS
    assert 'sessionStorage.setItem("status-visualizer-site", state.siteId)' in APP_JS
    assert '"X-Status-Visualizer-Site": requestSite' in APP_JS
    assert '"X-Status-Visualizer-Site": siteId' in APP_JS
    assert "if (state.siteId !== requestSite) return;" in APP_JS


def test_remote_sites_do_not_run_or_imply_central_icmp_checks() -> None:
    assert 'state.siteId !== "local"' in APP_JS
    assert '"Remote status unavailable"' in APP_JS
    assert '"Remote status stale"' in APP_JS
    assert "Remote status checked" in APP_JS
    assert 'el.checkLiveness.disabled = remote' in APP_JS
    assert "syncSiteControls();" in APP_JS
    assert "const requestSite = state.siteId;" in APP_JS


def test_topology_has_persisted_packet_loss_heatmap_controls() -> None:
    assert 'id="heatmap-toggle"' in INDEX_HTML
    assert 'id="heatmap-window"' in INDEX_HTML
    assert 'id="heatmap-show-healthy"' in INDEX_HTML
    assert 'id="heatmap-show-critical"' in INDEX_HTML
    assert 'id="heatmap-options"' in INDEX_HTML
    for window in ("5m", "15m", "1h", "12h", "24h"):
        assert f'<option value="{window}"' in INDEX_HTML
    assert 'localStorage.setItem("topology-health-heatmap"' in APP_JS
    assert "topology-health-min-loss" in APP_JS
    assert 'id="heatmap-min-loss"' in INDEX_HTML
    assert 'localStorage.setItem("topology-health-window"' in APP_JS
    assert "renderHeatmapOverlay" in APP_JS
    assert "heatmapSource" in APP_JS
    assert 'id="heatmap-layer"' in INDEX_HTML
    assert "renderGraph();" in APP_JS


def test_device_details_include_connectivity_and_packet_loss_without_a_graph() -> None:
    assert 'class="section-title">Connectivity' in APP_JS
    assert 'class="section-title">Packet loss' in APP_JS
    assert "current_rtt_ms" in APP_JS
    assert "last_rtt_ms" in APP_JS
    assert "last_success_at" in APP_JS
    assert "monitoring_state" in APP_JS
    assert "health-window-selected" in APP_JS
    assert "health-history-graph" not in (APP_JS + INDEX_HTML + STYLES)


def test_heatmap_uses_canvas_overlay_without_node_border_accents() -> None:
    assert 'id="heatmap-layer"' in INDEX_HTML
    assert ".heatmap-layer" in STYLES
    assert 'src="/static/heatmap-bands.js"' in INDEX_HTML
    assert "renderHeatmapOverlay" in APP_JS
    assert "heatmapSource" in APP_JS
    assert "health-heatmap-accent" not in APP_JS
    assert ".health-heatmap-accent" not in STYLES
    assert "has-vlan" in APP_JS
    assert "status-offline" in STYLES


def test_client_polling_refreshes_only_the_current_site_when_import_changes() -> None:
    assert "setInterval(refreshSitesAndClients, 10000)" in APP_JS
    assert "selectedImportMarker" in APP_JS
    assert "await loadTopology(false)" in APP_JS


def test_async_site_actions_discard_stale_results_after_site_switch() -> None:
    assert "if (state.siteId !== requestSite) return;" in APP_JS
    assert "if (state.siteId !== siteId) return;" in APP_JS
    assert 'maxlength="100"' in APP_JS
