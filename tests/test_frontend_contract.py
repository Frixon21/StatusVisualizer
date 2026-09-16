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
    assert '>Clear nodes</button>' in INDEX_HTML
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
    assert '<summary class="text-button">Arrange</summary>' in INDEX_HTML
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
    assert '>Save diagram PDF</button>' in INDEX_HTML
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
    assert "Unable to save the diagram PDF" in APP_JS
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
    assert "node-symbol" in STYLES


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
