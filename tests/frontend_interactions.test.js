"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  arrangeNodesHierarchically,
  arrangeNodesBySubnet,
  arrangeNodesBySwitch,
  arrangeNodesByVlan,
  cancelViewportGestures,
  constrainPanToBounds,
  fitTransform,
  focusTransform,
  healthWindowValue,
  heatmapDecoration,
  heatmapGlowVisible,
  heatmapOverlayVisible,
  heatmapSource,
  moveSelectedNodes,
  nodeDisplayLabel,
  nodeVlanIds,
  normalizeManualVlanIds,
  nodesInsideBox,
  orthogonalEdgePath,
  reconcileVisibleSelection,
  shouldAddMarqueeSelection,
  subnetKey,
  switchGroups,
  resolveIconType,
  topologyIcon,
  unmanagedGroupChildren,
  visibleTopology,
  vlanGroups,
  vlanNodeDecoration,
  viewportToWorld,
  zoomScale,
} = require("../app/static/topology-utils.js");

test("packet-loss heatmap uses loss severity on the canvas and keeps offline detail semantics", () => {
  assert.equal(heatmapSource({online: false, loss: {"5m": 100}}, "5m").level, "critical");
  assert.deepEqual(heatmapDecoration({online: false, loss: {"5m": 100}}, "5m"), {
    level: "offline", loss: 100, hasData: true, partial: false,
  });
  assert.equal(heatmapSource({online: true, loss: {"5m": 0}}, "5m").level, "healthy");
  assert.equal(heatmapSource({
    online: true, state: "online", monitoring_state: "normal", loss: {"5m": null},
  }, "5m").level, "healthy");
  assert.deepEqual(heatmapDecoration({online: true, loss: {"5m": 0}}, "5m").level, "healthy");
  assert.equal(heatmapSource({online: true, loss: {"5m": 1.2}}, "5m").level, "minor");
  assert.equal(heatmapSource({online: true, loss: {"5m": 4}}, "5m").level, "warning");
  assert.equal(heatmapSource({online: true, loss: {"5m": 8}}, "5m").level, "poor");
  assert.equal(heatmapSource({online: true, loss: {"5m": 18}}, "5m").level, "bad");
  assert.equal(heatmapSource({online: true, loss: {"5m": 40}}, "5m").level, "critical");
});

test("heatmap layer toggles hide only zero-loss healthy and full critical/offline devices", () => {
  const highLoss = heatmapSource({online: true, loss: {"5m": 56.6}}, "5m");
  assert.equal(highLoss.level, "critical");
  assert.equal(
    heatmapOverlayVisible(highLoss, {online: true}, {showHealthy: true, showCritical: false}),
    true,
  );
  const totalLoss = heatmapSource({online: true, loss: {"5m": 100}}, "5m");
  assert.equal(
    heatmapOverlayVisible(totalLoss, {online: true}, {showHealthy: true, showCritical: false}),
    false,
  );
  const offline = heatmapSource({online: false, loss: {"5m": 56.6}}, "5m");
  assert.equal(
    heatmapOverlayVisible(offline, {online: false}, {showHealthy: true, showCritical: false}),
    false,
  );
  const healthy = heatmapSource({online: true, loss: {"5m": 0}}, "5m");
  assert.equal(
    heatmapOverlayVisible(healthy, {online: true}, {showHealthy: false, showCritical: true}),
    false,
  );
});

test("packet-loss heatmap treats missing history as no data and marks short observations partial", () => {
  assert.deepEqual(heatmapDecoration(null, "24h"), {
    level: "no-data", loss: null, hasData: false, partial: false,
  });
  assert.deepEqual(heatmapDecoration({online: true, loss: {"24h": null}}, "24h").level, "no-data");
  assert.equal(heatmapDecoration({
    online: true, loss: {"24h": 0}, observed_seconds: {"24h": 180},
  }, "24h").partial, true);
});

test("health window values expose loss, RTT average, and observation coverage without mutation", () => {
  const health = Object.freeze({
    loss: Object.freeze({"15m": 0.8}),
    rtt_avg_ms: Object.freeze({"15m": 4.1}),
    observed_seconds: Object.freeze({"15m": 600}),
  });
  assert.deepEqual(healthWindowValue(health, "15m"), {
    loss: 0.8, rttAverageMs: 4.1, observedSeconds: 600, hasData: true, partial: true,
  });
  assert.equal(Object.isFrozen(healthWindowValue(health, "15m")), true);
  assert.equal(health.loss["15m"], 0.8);
});

function positionsById(positions) {
  return new Map(positions.map((position) => [position.id, position]));
}

test("cancelling a viewport gesture resets only the matching gesture", () => {
  const panning = {id: 7, x: 10};
  const marquee = {id: 8, start: {x: 0, y: 0}};

  assert.deepEqual(cancelViewportGestures(panning, marquee, 8), {
    panning,
    marquee: null,
    cancelledPan: false,
    cancelledMarquee: true,
  });
  assert.deepEqual(cancelViewportGestures(panning, marquee, 99), {
    panning,
    marquee,
    cancelledPan: false,
    cancelledMarquee: false,
  });
});

test("Shift-drag in Pan mode replaces selection so an empty box clears it", () => {
  assert.equal(shouldAddMarqueeSelection("pan", {shiftKey: true}), false);
  assert.equal(shouldAddMarqueeSelection("select", {shiftKey: true}), true);
  assert.equal(shouldAddMarqueeSelection("select", {ctrlKey: true}), true);
  assert.equal(shouldAddMarqueeSelection("pan", {ctrlKey: true}), false);
});

test("panning leaves workspace around content but cannot lose it entirely", () => {
  assert.deepEqual(
    constrainPanToBounds(
      {x: 5000, y: -5000, scale: 0.5},
      {width: 1000, height: 600},
      {left: 0, top: 0, right: 1800, bottom: 1100},
    ),
    {x: 750, y: -400, scale: 0.5},
  );
});

test("Fit includes nodes across the full persisted workspace", () => {
  const transform = fitTransform(
    [{x: -10, y: -10}, {x: 11, y: 11}],
    {width: 1000, height: 600},
    {width: 1800, height: 1100},
  );
  const first = {x: -10 * 1800 * transform.scale + transform.x, y: -10 * 1100 * transform.scale + transform.y};
  const last = {x: 11 * 1800 * transform.scale + transform.x, y: 11 * 1100 * transform.scale + transform.y};

  assert.ok(transform.scale < 0.025);
  assert.ok(first.x >= 0 && first.y >= 0);
  assert.ok(last.x <= 1000 && last.y <= 600);
});

test("zooming out after an extreme Fit never increases the scale", () => {
  assert.equal(zoomScale(0.02, 0.83), 0.0166);
});

test("viewport coordinates remain continuous beyond the original layout rectangle", () => {
  assert.deepEqual(
    viewportToWorld(2050, -150, {left: 0, top: 0}, {x: 100, y: 70, scale: 1}, {width: 1800, height: 1100}),
    {x: 1.083333, y: -0.2},
  );
});

test("marquee selects nodes whose centers are inside the normalized box", () => {
  const nodes = [
    {id: "a", x: 0.2, y: 0.3},
    {id: "b", x: 0.5, y: 0.5},
    {id: "c", x: 0.9, y: 0.9},
  ];

  assert.deepEqual(nodesInsideBox(nodes, {x1: 0.1, y1: 0.2, x2: 0.6, y2: 0.6}), ["a", "b"]);
});

test("reverse-direction marquee produces the same selection", () => {
  const nodes = [{id: "a", x: 0.2, y: 0.3}, {id: "b", x: 0.8, y: 0.8}];
  assert.deepEqual(nodesInsideBox(nodes, {x1: 0.6, y1: 0.6, x2: 0.1, y2: 0.2}), ["a"]);
});

test("group movement preserves spacing and snaps the group anchor", () => {
  const nodes = [
    {id: "a", x: 0.2, y: 0.2},
    {id: "b", x: 0.35, y: 0.4},
    {id: "c", x: 0.8, y: 0.8},
  ];

  const moved = moveSelectedNodes(nodes, ["a", "b"], 0.123, 0.087, {
    snap: true,
    stepX: 0.1,
    stepY: 0.1,
    minX: 0.02,
    maxX: 0.98,
    minY: 0.03,
    maxY: 0.97,
  });

  assert.deepEqual(moved, [
    {id: "a", x: 0.3, y: 0.3},
    {id: "b", x: 0.45, y: 0.5},
  ]);
});

test("group movement can cross the original stage edge without changing spacing", () => {
  const moved = moveSelectedNodes(
    [{id: "a", x: 0.8, y: 0.8}, {id: "b", x: 0.95, y: 0.9}],
    ["a", "b"],
    0.5,
    0.5,
    {snap: false},
  );

  assert.deepEqual(moved, [{id: "a", x: 1.3, y: 1.3}, {id: "b", x: 1.45, y: 1.4}]);
});

test("automatic icons follow node type while explicit choices win", () => {
  assert.equal(resolveIconType({node_type: "switch", icon_type: "auto"}), "switch");
  assert.equal(resolveIconType({node_type: "switch", icon_type: "printer"}), "printer");
  assert.equal(resolveIconType({node_type: "unknown", icon_type: "auto"}), "other");
});

test("SVG icon markup canonicalizes untrusted icon names", () => {
  const markup = topologyIcon('printer" onload="alert(1)');
  assert.match(markup, /topology-icon-other/);
  assert.doesNotMatch(markup, /onload/);
});

test("VLAN membership combines endpoint metadata and interface PVIDs", () => {
  const node = {id: "endpoint", address: "192.168.1.20", metadata: {VLAN: "20", VLANs: "20, 30"}};
  const interfaces = [
    {device_id: "endpoint", vlan: "10"},
    {device_id: "someone-else", vlan: "99"},
  ];

  assert.deepEqual(nodeVlanIds(node, interfaces, []), ["10", "20", "30"]);
});

test("manual VLAN input is validated, deduplicated, sorted, and canonicalized", () => {
  assert.deepEqual(normalizeManualVlanIds("30, 10,20, 10"), {
    ids: ["10", "20", "30"],
    value: "10, 20, 30",
  });
  assert.deepEqual(normalizeManualVlanIds(""), {ids: [], value: ""});
  assert.throws(() => normalizeManualVlanIds("10, guest"), /whole numbers/);
  assert.throws(() => normalizeManualVlanIds("0, 20"), /between 1 and 4094/);
  assert.throws(() => normalizeManualVlanIds("4095"), /between 1 and 4094/);
});

test("managed infrastructure inherits every VLAN configured on that switch", () => {
  const node = {id: "core", source: "lantopolog", address: "192.168.1.2", metadata: {}};
  const vlans = [
    {vlan_id: "1", name: "Default", switch_key: "switch:192.168.1.2"},
    {vlan_id: "40", name: "Voice", switch_key: "switch:192.168.1.2"},
    {vlan_id: "90", name: "Guest", switch_key: "switch:192.168.1.3"},
  ];

  assert.deepEqual(nodeVlanIds(node, [], vlans), ["1", "40"]);
});

test("VLAN groups separate access nodes from shared and unassigned equipment", () => {
  const nodes = [
    {id: "accounting", metadata: {VLAN: "20"}},
    {id: "trunk", metadata: {VLANs: "20, 30"}},
    {id: "unknown", metadata: {}},
  ];
  const groups = vlanGroups(nodes, [], [{vlan_id: "20", name: "Accounting"}]);

  assert.deepEqual(groups.map(({key, label, nodeIds}) => ({key, label, nodeIds})), [
    {key: "vlan:20", label: "VLAN 20 · Accounting", nodeIds: ["accounting"]},
    {key: "shared", label: "Shared / trunk", nodeIds: ["trunk"]},
    {key: "unassigned", label: "Unassigned", nodeIds: ["unknown"]},
  ]);
});

test("arranging by VLAN keeps every node and separates group centers", () => {
  const nodes = [
    {id: "a", metadata: {VLAN: "10"}},
    {id: "b", metadata: {VLAN: "10"}},
    {id: "c", metadata: {VLAN: "20"}},
    {id: "d", metadata: {}},
  ];
  const positions = arrangeNodesByVlan(nodes, [], []);
  const byId = new Map(positions.map((position) => [position.id, position]));

  assert.deepEqual([...byId.keys()].sort(), ["a", "b", "c", "d"]);
  assert.ok(Math.abs(byId.get("a").x - byId.get("b").x) < 0.25);
  assert.ok(Math.abs(byId.get("a").x - byId.get("c").x) > 0.25);
  assert.ok(positions.every(({x, y}) => x >= 0.04 && x <= 0.96 && y >= 0.06 && y <= 0.94));
});

test("unassigned nodes have no VLAN accent", () => {
  assert.equal(vlanNodeDecoration({id: "unknown", metadata: {}}, [], []), null);
});

test("single-VLAN node decoration uses one deterministic accent color", () => {
  const decoration = vlanNodeDecoration(
    {id: "endpoint", metadata: {VLAN: "20"}},
    [],
    [{vlan_id: "20", name: "Accounting"}],
  );

  assert.equal(decoration.label, "VLAN 20");
  assert.equal(decoration.title, "VLAN 20 - Accounting");
  assert.equal(decoration.segments.length, 1);
  assert.equal(decoration.extraCount, 0);
  assert.match(decoration.segments[0].color, /^#[0-9a-f]{6}$/i);
  assert.equal(decoration.segments[0].id, "20");
  assert.equal(decoration.background, decoration.segments[0].color);
});

test("multi-VLAN node decoration keeps colors segmented and exposes every VLAN in the title", () => {
  const decoration = vlanNodeDecoration(
    {id: "trunk", metadata: {VLANs: "10, 20, 30, 40, 50"}},
    [],
    [
      {vlan_id: "10", name: "Default"},
      {vlan_id: "20", name: "Accounting"},
      {vlan_id: "30", name: "Voice"},
      {vlan_id: "40", name: "Guest"},
      {vlan_id: "50", name: "Camera"},
    ],
  );

  assert.equal(decoration.label, "5 VLANs");
  assert.equal(decoration.segments.length, 4);
  assert.equal(decoration.extraCount, 1);
  assert.deepEqual(decoration.segments.map(({id}) => id), ["10", "20", "30", "40"]);
  assert.equal(new Set(decoration.segments.map(({color}) => color)).size, 4);
  assert.match(decoration.background, /^linear-gradient\(90deg, /);
  decoration.segments.forEach(({color}, index) => {
    const colorPosition = decoration.background.indexOf(color);
    const previousPosition = index ? decoration.background.indexOf(decoration.segments[index - 1].color) : -1;
    assert.ok(colorPosition > previousPosition);
  });
  assert.match(decoration.title, /VLAN 10 - Default/);
  assert.match(decoration.title, /VLAN 50 - Camera/);
  assert.deepEqual(
    vlanNodeDecoration(
      {id: "trunk", metadata: {VLANs: "50, 30, 10, 40, 20"}},
      [],
      [
        {vlan_id: "50", name: "Camera"},
        {vlan_id: "40", name: "Guest"},
        {vlan_id: "30", name: "Voice"},
        {vlan_id: "20", name: "Accounting"},
        {vlan_id: "10", name: "Default"},
      ],
    ),
    decoration,
  );
});

test("hierarchical arrangement places each BFS generation one row below its parent", () => {
  const nodes = [
    {id: "root", node_type: "router"},
    {id: "left", node_type: "switch"},
    {id: "right", node_type: "switch"},
    {id: "left-child", node_type: "workstation"},
    {id: "right-child", node_type: "printer"},
  ];
  const edges = [
    {source_id: "root", target_id: "left"},
    {source_id: "root", target_id: "right"},
    {source_id: "left", target_id: "left-child"},
    {source_id: "right", target_id: "right-child"},
  ];
  const byId = positionsById(arrangeNodesHierarchically(nodes, edges));

  assert.ok(byId.get("root").y < byId.get("left").y);
  assert.equal(byId.get("left").y, byId.get("right").y);
  assert.ok(byId.get("left").y < byId.get("left-child").y);
  assert.equal(byId.get("left-child").y, byId.get("right-child").y);
});

test("bottom-up hierarchy centers every parent over its descendant leaf span", () => {
  const nodes = [
    {id: "root", node_type: "router"},
    {id: "left", node_type: "switch"},
    {id: "right", node_type: "switch"},
    {id: "left-1", node_type: "workstation"},
    {id: "left-2", node_type: "workstation"},
    {id: "left-3", node_type: "printer"},
    {id: "right-1", node_type: "server"},
  ];
  const edges = [
    {source_id: "root", target_id: "left"},
    {source_id: "root", target_id: "right"},
    {source_id: "left", target_id: "left-1"},
    {source_id: "left", target_id: "left-2"},
    {source_id: "left", target_id: "left-3"},
    {source_id: "right", target_id: "right-1"},
  ];
  const byId = positionsById(arrangeNodesHierarchically(nodes, edges));
  const spanCenter = (ids) => {
    const xs = ids.map((id) => byId.get(id).x);
    return (Math.min(...xs) + Math.max(...xs)) / 2;
  };
  const centeredAt = (nodeId, ids) => (
    Math.abs(byId.get(nodeId).x - spanCenter(ids)) < 1e-8
  );

  assert.ok(centeredAt("left", ["left-1", "left-2", "left-3"]));
  assert.ok(Math.abs(byId.get("right").x - byId.get("right-1").x) < 1e-8);
  assert.ok(centeredAt("root", ["left-1", "left-2", "left-3", "right-1"]));
});

test("branch gaps separate parent groups without inflating sibling spacing", () => {
  const nodes = [
    {id: "root", node_type: "router"},
    {id: "left", node_type: "switch"},
    {id: "right", node_type: "switch"},
    {id: "left-1"}, {id: "left-2"}, {id: "right-1"}, {id: "right-2"},
  ];
  const edges = [
    {source_id: "root", target_id: "left"},
    {source_id: "root", target_id: "right"},
    {source_id: "left", target_id: "left-1"},
    {source_id: "left", target_id: "left-2"},
    {source_id: "right", target_id: "right-1"},
    {source_id: "right", target_id: "right-2"},
  ];
  const byId = positionsById(arrangeNodesHierarchically(nodes, edges, {
    horizontalSpacing: 0.04,
    branchGap: 0.12,
  }));
  const leftSiblingGap = byId.get("left-2").x - byId.get("left-1").x;
  const branchBoundaryGap = byId.get("right-1").x - byId.get("left-2").x;
  const rightSiblingGap = byId.get("right-2").x - byId.get("right-1").x;

  assert.ok(Math.abs(leftSiblingGap - 0.04) < 1e-8);
  assert.ok(Math.abs(branchBoundaryGap - 0.16) < 1e-8);
  assert.ok(Math.abs(rightSiblingGap - 0.04) < 1e-8);
});

test("snap-to-grid keeps sibling spacing stable when branch gap changes", () => {
  const nodes = [
    {id: "root", node_type: "router"},
    {id: "left", node_type: "switch"},
    {id: "right", node_type: "switch"},
    {id: "left-1"}, {id: "left-2"}, {id: "left-3"}, {id: "right-1"},
  ];
  const edges = [
    {source_id: "root", target_id: "left"},
    {source_id: "root", target_id: "right"},
    {source_id: "left", target_id: "left-1"},
    {source_id: "left", target_id: "left-2"},
    {source_id: "left", target_id: "left-3"},
    {source_id: "right", target_id: "right-1"},
  ];
  const withNarrowBranchGap = positionsById(arrangeNodesHierarchically(nodes, edges, {
    horizontalSpacing: 0.05,
    branchGap: 0.01,
    snap: true,
    stepX: 22 / 1800,
    stepY: 22 / 1100,
  }));
  const withWideBranchGap = positionsById(arrangeNodesHierarchically(nodes, edges, {
    horizontalSpacing: 0.05,
    branchGap: 0.12,
    snap: true,
    stepX: 22 / 1800,
    stepY: 22 / 1100,
  }));
  const siblingGaps = (byId) => [
    byId.get("left-2").x - byId.get("left-1").x,
    byId.get("left-3").x - byId.get("left-2").x,
  ];
  const approximatelyEqual = (left, right) => Math.abs(left - right) < 1e-8;
  const narrowSiblingGaps = siblingGaps(withNarrowBranchGap);
  const wideSiblingGaps = siblingGaps(withWideBranchGap);
  const narrowBranchBoundary = withNarrowBranchGap.get("right-1").x - withNarrowBranchGap.get("left-3").x;
  const wideBranchBoundary = withWideBranchGap.get("right-1").x - withWideBranchGap.get("left-3").x;

  assert.ok(narrowSiblingGaps.every((gap, index) => approximatelyEqual(gap, wideSiblingGaps[index])));
  assert.ok(wideBranchBoundary > narrowBranchBoundary);
});

test("bottom-up hierarchy centers the deepest leaf span and is input-order independent", () => {
  const nodes = [
    {id: "root", node_type: "router"},
    {id: "distribution-a", node_type: "switch"},
    {id: "distribution-b", node_type: "switch"},
    {id: "a-1"}, {id: "a-2"}, {id: "a-3"}, {id: "b-1"},
  ];
  const edges = [
    {source_id: "root", target_id: "distribution-a"},
    {source_id: "root", target_id: "distribution-b"},
    {source_id: "distribution-a", target_id: "a-1"},
    {source_id: "distribution-a", target_id: "a-2"},
    {source_id: "distribution-a", target_id: "a-3"},
    {source_id: "distribution-b", target_id: "b-1"},
  ];
  const canonical = arrangeNodesHierarchically(nodes, edges)
    .sort((left, right) => left.id.localeCompare(right.id));
  const reordered = arrangeNodesHierarchically([...nodes].reverse(), [...edges].reverse())
    .sort((left, right) => left.id.localeCompare(right.id));
  const deepestY = Math.max(...canonical.map(({y}) => y));
  const deepestXs = canonical.filter(({y}) => y === deepestY).map(({x}) => x);

  assert.ok(Math.abs((Math.min(...deepestXs) + Math.max(...deepestXs)) / 2 - 0.5) < 1e-8);
  assert.deepEqual(reordered, canonical);
});

test("hierarchical arrangement snaps generated positions to the active 22px grid", () => {
  const nodes = [
    {id: "root", node_type: "router"},
    {id: "left", node_type: "switch"},
    {id: "right", node_type: "switch"},
    {id: "leaf", node_type: "workstation"},
  ];
  const edges = [
    {source_id: "root", target_id: "left"},
    {source_id: "root", target_id: "right"},
    {source_id: "right", target_id: "leaf"},
  ];
  const positions = arrangeNodesHierarchically(nodes, edges, {
    snap: true,
    stepX: 22 / 1800,
    stepY: 22 / 1100,
  });
  const isWholeGridStep = (normalized, pixels) => (
    Math.abs((normalized * pixels / 22) - Math.round(normalized * pixels / 22)) < 1e-8
  );

  assert.ok(positions.every(({x}) => isWholeGridStep(x, 1800)));
  assert.ok(positions.every(({y}) => isWholeGridStep(y, 1100)));
});

test("hierarchical arrangement keeps disconnected components in separate horizontal bands", () => {
  const nodes = [
    {id: "alpha-root"}, {id: "alpha-child"},
    {id: "beta-root"}, {id: "beta-child"},
    {id: "isolated"},
  ];
  const edges = [
    {source_id: "alpha-root", target_id: "alpha-child"},
    {source_id: "beta-root", target_id: "beta-child"},
  ];
  const positions = arrangeNodesHierarchically(nodes, edges);
  const byId = positionsById(positions);
  const componentRanges = [
    ["alpha-root", "alpha-child"],
    ["beta-root", "beta-child"],
    ["isolated"],
  ].map((ids) => {
    const xs = ids.map((id) => byId.get(id).x);
    return {left: Math.min(...xs), right: Math.max(...xs)};
  });

  assert.deepEqual([...byId.keys()].sort(), nodes.map(({id}) => id).sort());
  assert.equal(new Set(positions.map(({x, y}) => `${x},${y}`)).size, nodes.length);
  assert.ok(componentRanges[0].right < componentRanges[1].left);
  assert.ok(componentRanges[1].right < componentRanges[2].left);
});

test("hierarchical arrangement terminates cycles and multiple-parent graphs deterministically", () => {
  const nodes = [{id: "a"}, {id: "b"}, {id: "c"}, {id: "d"}];
  const edges = [
    {source_id: "a", target_id: "b"},
    {source_id: "a", target_id: "c"},
    {source_id: "b", target_id: "d"},
    {source_id: "c", target_id: "d"},
    {source_id: "d", target_id: "a"},
  ];
  const canonical = arrangeNodesHierarchically(nodes, edges)
    .sort((left, right) => left.id.localeCompare(right.id));
  const reordered = arrangeNodesHierarchically([...nodes].reverse(), [...edges].reverse())
    .sort((left, right) => left.id.localeCompare(right.id));

  assert.equal(canonical.length, nodes.length);
  assert.deepEqual(reordered, canonical);
  assert.equal(new Set(canonical.map(({id}) => id)).size, nodes.length);
});

test("hierarchical arrangement reserves a full card width for wide sibling rows", () => {
  const root = {id: "root", node_type: "router"};
  const children = Array.from({length: 18}, (_, index) => ({id: `child-${String(index).padStart(2, "0")}`}));
  const positions = arrangeNodesHierarchically(
    [root, ...children],
    children.map((child) => ({source_id: root.id, target_id: child.id})),
    {horizontalSpacing: 0.1, branchGap: 0.03},
  );
  const childPositions = positions
    .filter(({id}) => id.startsWith("child-"))
    .sort((left, right) => left.x - right.x);

  // A 164px card plus 16px breathing room occupies 0.1 of the 1800px stage.
  assert.equal(childPositions.length, children.length);
  assert.ok(childPositions.every(({y}) => y === childPositions[0].y));
  assert.ok(childPositions.slice(1).every((position, index) => (
    position.x - childPositions[index].x >= 0.1 - 1e-8
  )));
});

test("hierarchical positions stay finite with useful normalized vertical bounds", () => {
  const nodes = Array.from({length: 40}, (_, index) => ({id: `node-${String(index).padStart(2, "0")}`}));
  const edges = nodes.slice(1).map((node, index) => ({
    source_id: nodes[Math.floor(index / 3)].id,
    target_id: node.id,
  }));
  const positions = arrangeNodesHierarchically(nodes, edges);

  assert.equal(positions.length, nodes.length);
  assert.ok(positions.every(({x, y}) => Number.isFinite(x) && Number.isFinite(y)));
  assert.ok(positions.every(({y}) => y >= 0.06 && y <= 0.94));
});

test("subnet keys group IPv4 addresses by /24 and use one unknown bucket", () => {
  assert.equal(subnetKey({address: "192.168.42.17"}), "192.168.42.0/24");
  assert.equal(subnetKey({address: "192.168.42.254"}), "192.168.42.0/24");
  assert.equal(subnetKey({address: "10.7.9.1"}), "10.7.9.0/24");
  assert.equal(subnetKey({address: ""}), "Unknown / no IP");
  assert.equal(subnetKey({address: "printer.local"}), "Unknown / no IP");
  assert.equal(subnetKey({address: "999.7.9.1"}), "Unknown / no IP");
});

test("subnet arrangement retains every node and separates subnet clusters", () => {
  const nodes = [
    {id: "a1", address: "192.168.10.4"},
    {id: "a2", address: "192.168.10.22"},
    {id: "b1", address: "192.168.20.8"},
    {id: "b2", address: "192.168.20.19"},
    {id: "unknown", address: ""},
  ];
  const byId = positionsById(arrangeNodesBySubnet(nodes));
  const center = (ids) => ids.reduce((sum, id) => sum + byId.get(id).x, 0) / ids.length;

  assert.deepEqual([...byId.keys()].sort(), nodes.map(({id}) => id).sort());
  assert.ok(Math.abs(center(["a1", "a2"]) - center(["b1", "b2"])) > 0.2);
  assert.ok(Math.abs(center(["b1", "b2"]) - byId.get("unknown").x) > 0.2);
  assert.ok([...byId.values()].every(({x, y}) => Number.isFinite(x) && Number.isFinite(y)));
});

test("switch groups use managed and inferred switch anchors with their attached endpoints", () => {
  const nodes = [
    {id: "managed", node_type: "switch", source: "lantopolog", metadata: {}},
    {id: "managed-client", node_type: "workstation", metadata: {}},
    {id: "fanout", node_type: "switch", metadata: {"Synthetic Role": "shared-port-fanout"}},
    {id: "fanout-client-1", node_type: "workstation", metadata: {}},
    {id: "fanout-client-2", node_type: "printer", metadata: {}},
  ];
  const edges = [
    {source_id: "managed", target_id: "managed-client"},
    {source_id: "managed", target_id: "fanout"},
    {source_id: "fanout", target_id: "fanout-client-1"},
    {source_id: "fanout", target_id: "fanout-client-2"},
  ];
  const groups = new Map(switchGroups(nodes, edges).map((group) => [group.switchId, group.nodeIds]));

  assert.deepEqual(groups.get("managed"), ["managed", "managed-client"]);
  assert.deepEqual(groups.get("fanout"), ["fanout", "fanout-client-1", "fanout-client-2"]);
});

test("switch arrangement retains nodes and keeps endpoints nearer their switch than other switches", () => {
  const nodes = [
    {id: "switch-a", node_type: "switch"},
    {id: "a-client", node_type: "workstation"},
    {id: "switch-b", node_type: "switch"},
    {id: "b-client", node_type: "printer"},
    {id: "orphan", node_type: "server"},
  ];
  const edges = [
    {source_id: "switch-a", target_id: "a-client"},
    {source_id: "switch-b", target_id: "b-client"},
  ];
  const byId = positionsById(arrangeNodesBySwitch(nodes, edges));
  const distance = (leftId, rightId) => {
    const left = byId.get(leftId);
    const right = byId.get(rightId);
    return Math.hypot(left.x - right.x, left.y - right.y);
  };

  assert.deepEqual([...byId.keys()].sort(), nodes.map(({id}) => id).sort());
  assert.ok(distance("switch-a", "a-client") < distance("switch-b", "a-client"));
  assert.ok(distance("switch-b", "b-client") < distance("switch-a", "b-client"));
  assert.ok([...byId.values()].every(({x, y}) => Number.isFinite(x) && Number.isFinite(y)));
});

test("unmanaged group children include only leaf endpoints below a shared-port fanout", () => {
  const nodes = [
    {id: "core", node_type: "switch", metadata: {}},
    {id: "fanout", node_type: "switch", metadata: {"Synthetic Role": "shared-port-fanout"}},
    {id: "leaf-pc", node_type: "workstation", metadata: {}},
    {id: "leaf-printer", node_type: "printer", metadata: {}},
    {id: "nested-switch", node_type: "switch", metadata: {}},
    {id: "non-leaf", node_type: "workstation", metadata: {}},
    {id: "other", node_type: "server", metadata: {}},
  ];
  const edges = [
    {source_id: "core", target_id: "fanout"},
    {source_id: "fanout", target_id: "leaf-pc"},
    {source_id: "fanout", target_id: "leaf-printer"},
    {source_id: "fanout", target_id: "nested-switch"},
    {source_id: "fanout", target_id: "non-leaf"},
    {source_id: "non-leaf", target_id: "other"},
  ];

  assert.deepEqual(unmanagedGroupChildren(nodes, edges, "fanout"), ["leaf-pc", "leaf-printer"]);
  assert.deepEqual(unmanagedGroupChildren(nodes, edges, "core"), []);
});

test("visible topology hides categories and collapsed unmanaged children without dangling edges", () => {
  const nodes = [
    {id: "core", node_type: "switch", metadata: {}},
    {id: "fanout", node_type: "switch", metadata: {"Synthetic Role": "shared-port-fanout"}},
    {id: "pc", node_type: "workstation", metadata: {}},
    {id: "printer", node_type: "printer", metadata: {}},
    {id: "server", node_type: "server", metadata: {}},
    {id: "manual-phone", node_type: "phone", source: "manual", metadata: {}},
  ];
  const edges = [
    {id: "uplink", source_id: "core", target_id: "fanout"},
    {id: "pc-link", source_id: "fanout", target_id: "pc"},
    {id: "printer-link", source_id: "fanout", target_id: "printer"},
    {id: "server-link", source_id: "core", target_id: "server"},
    {id: "manual-link", source_id: "core", target_id: "manual-phone"},
  ];
  const result = visibleTopology(nodes, edges, {
    hiddenTypes: new Set(["printer"]),
    hideManual: true,
    collapsedGroupIds: new Set(["fanout"]),
  });

  assert.deepEqual(result.nodes.map(({id}) => id), ["core", "fanout", "server"]);
  assert.deepEqual(result.edges.map(({id}) => id), ["uplink", "server-link"]);
  const visibleIds = new Set(result.nodes.map(({id}) => id));
  assert.ok(result.edges.every((edge) => visibleIds.has(edge.source_id) && visibleIds.has(edge.target_id)));
});

test("heatmap glow threshold skips low-loss halos without hiding devices", () => {
  const health = {online: true, loss: {"5m": 2.5}};
  assert.equal(heatmapGlowVisible(health, "5m", 0), true);
  assert.equal(heatmapGlowVisible(health, "5m", 5), false);
  assert.equal(heatmapGlowVisible({online: true, loss: {"5m": null}}, "5m", 5), true);
  const nodes = [
    {id: "low", node_type: "workstation", health: {online: true, loss: {"5m": 2.5}}},
    {id: "high", node_type: "workstation", health: {online: true, loss: {"5m": 12.4}}},
  ];
  assert.deepEqual(visibleTopology(nodes, []).nodes.map(({id}) => id), ["low", "high"]);
});

test("visible topology can hide devices without an IP address", () => {
  const nodes = [
    {id: "with-ip", node_type: "workstation", address: "192.168.1.10"},
    {id: "no-ip", node_type: "workstation", address: ""},
    {id: "mac-only", node_type: "other", address: null},
    {id: "fanout", node_type: "switch", address: "", metadata: {"Synthetic Role": "shared-port-fanout"}},
  ];
  const edges = [
    {id: "link", source_id: "with-ip", target_id: "no-ip"},
    {id: "fanout-link", source_id: "fanout", target_id: "with-ip"},
  ];
  const result = visibleTopology(nodes, edges, {hideNoIp: true});
  assert.deepEqual(result.nodes.map(({id}) => id), ["with-ip", "fanout"]);
  assert.deepEqual(result.edges.map(({id}) => id), ["fanout-link"]);
});

test("device display labels support hostname, IP, and both modes with useful fallbacks", () => {
  const complete = {name: "front-desk", address: "192.168.30.15"};
  assert.deepEqual(nodeDisplayLabel(complete, "hostname"), {primary: "front-desk", secondary: ""});
  assert.deepEqual(nodeDisplayLabel(complete, "ip"), {primary: "192.168.30.15", secondary: ""});
  assert.deepEqual(nodeDisplayLabel(complete, "both"), {
    primary: "front-desk",
    secondary: "192.168.30.15",
  });
  assert.deepEqual(nodeDisplayLabel({name: "", address: "192.168.30.16"}, "hostname"), {
    primary: "192.168.30.16",
    secondary: "",
  });
  assert.deepEqual(nodeDisplayLabel({name: "printer", address: ""}, "ip"), {
    primary: "printer",
    secondary: "",
  });
  assert.deepEqual(nodeDisplayLabel({name: "", address: "", id: "device-17"}, "both"), {
    primary: "device-17",
    secondary: "",
  });
  assert.deepEqual(nodeDisplayLabel({name: "10.0.0.4", address: "10.0.0.4"}, "both"), {
    primary: "10.0.0.4",
    secondary: "",
  });
});

test("text-label nodes keep only annotation copy in their display label", () => {
  const label = nodeDisplayLabel({name: "PCI scope", address: "192.168.40.5", node_shape: "text"}, "both");

  assert.deepEqual(label, {primary: "PCI scope", secondary: ""});
});

test("device display labels return immutable raw text for the renderer to escape", () => {
  const label = nodeDisplayLabel(
    {name: '<img src=x onerror="alert(1)">', address: "10.0.0.4"},
    "both",
  );

  assert.deepEqual(label, {
    primary: '<img src=x onerror="alert(1)">',
    secondary: "10.0.0.4",
  });
  assert.equal(Object.isFrozen(label), true);
});

test("focus transform centers a node without mutating it", () => {
  const node = Object.freeze({id: "target", x: 0.72, y: 0.31});
  const viewport = Object.freeze({width: 1200, height: 700});
  const stage = Object.freeze({width: 1800, height: 1100});

  const transform = focusTransform(node, viewport, stage, 1.25);

  assert.deepEqual(transform, {
    x: 600 - (0.72 * 1800 * 1.25),
    y: 350 - (0.31 * 1100 * 1.25),
    scale: 1.25,
  });
  assert.deepEqual(node, {id: "target", x: 0.72, y: 0.31});
  assert.ok(Object.values(transform).every(Number.isFinite));
  assert.deepEqual(focusTransform(node, viewport, stage, 1.25), transform);
});

test("visibility reconciliation clears selected nodes and edges that are no longer rendered", () => {
  const selection = Object.freeze({
    selectedNodeIds: new Set(["visible", "hidden"]),
    selectedNodeId: "hidden",
    selectedEdgeId: "hidden-edge",
    panelMode: "edge",
  });
  const topology = Object.freeze({
    nodes: [{id: "visible"}],
    edges: [{id: "visible-edge", source_id: "visible", target_id: "visible"}],
  });

  const result = reconcileVisibleSelection(selection, topology);

  assert.deepEqual([...result.selectedNodeIds], ["visible"]);
  assert.equal(result.selectedNodeId, null);
  assert.equal(result.selectedEdgeId, null);
  assert.equal(result.panelMode, null);
  assert.deepEqual([...selection.selectedNodeIds], ["visible", "hidden"]);
});

test("visibility reconciliation preserves a still-visible node detail selection", () => {
  const result = reconcileVisibleSelection({
    selectedNodeIds: new Set(["visible"]),
    selectedNodeId: "visible",
    selectedEdgeId: null,
    panelMode: "node",
  }, {nodes: [{id: "visible"}], edges: []});

  assert.deepEqual([...result.selectedNodeIds], ["visible"]);
  assert.equal(result.selectedNodeId, "visible");
  assert.equal(result.panelMode, "node");
});

test("orthogonal edge routing uses a shared horizontal bus between topology levels", () => {
  const source = Object.freeze({id: "parent", x: 0.2, y: 0.1});
  const target = Object.freeze({id: "child", x: 0.8, y: 0.5});

  assert.equal(
    orthogonalEdgePath(source, target, {width: 1000, height: 800}),
    "M 200 80 V 240 H 800 V 400",
  );
  assert.deepEqual(source, {id: "parent", x: 0.2, y: 0.1});
  assert.deepEqual(target, {id: "child", x: 0.8, y: 0.5});
});

test("orthogonal edge routing avoids redundant bends for aligned devices", () => {
  const stage = {width: 1000, height: 800};

  assert.equal(
    orthogonalEdgePath({x: 0.2, y: 0.3}, {x: 0.8, y: 0.3}, stage),
    "M 200 240 H 800",
  );
  assert.equal(
    orthogonalEdgePath({x: 0.4, y: 0.1}, {x: 0.4, y: 0.5}, stage),
    "M 400 80 V 400",
  );
});
