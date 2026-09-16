(function topologyUtilities(root, factory) {
  const utilities = factory();
  if (typeof module === "object" && module.exports) module.exports = utilities;
  else root.TopologyUtils = utilities;
}(typeof globalThis !== "undefined" ? globalThis : this, () => {
  "use strict";

  const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, value));
  const clean = (value) => Number(value.toFixed(6));

  function normalizeBox(box) {
    return {
      x1: Math.min(box.x1, box.x2),
      y1: Math.min(box.y1, box.y2),
      x2: Math.max(box.x1, box.x2),
      y2: Math.max(box.y1, box.y2),
    };
  }

  function nodesInsideBox(nodes, box) {
    const normalized = normalizeBox(box);
    return nodes
      .filter((node) => node.x >= normalized.x1 && node.x <= normalized.x2
        && node.y >= normalized.y1 && node.y <= normalized.y2)
      .map((node) => node.id);
  }

  function shouldAddMarqueeSelection(tool, modifiers = {}) {
    return tool === "select" && Boolean(
      modifiers.ctrlKey || modifiers.metaKey || modifiers.shiftKey,
    );
  }

  function cancelViewportGestures(panning, marquee, pointerId) {
    const cancelledPan = Boolean(panning && panning.id === pointerId);
    const cancelledMarquee = Boolean(marquee && marquee.id === pointerId);
    return {
      panning: cancelledPan ? null : panning,
      marquee: cancelledMarquee ? null : marquee,
      cancelledPan,
      cancelledMarquee,
    };
  }

  function viewportToWorld(clientX, clientY, viewport, transform, stage) {
    return {
      x: clean((clientX - viewport.left - transform.x) / transform.scale / stage.width),
      y: clean((clientY - viewport.top - transform.y) / transform.scale / stage.height),
    };
  }

  function constrainPanToBounds(transform, viewport, bounds, overscrollRatio = 0.75) {
    const overscrollX = viewport.width * overscrollRatio;
    const overscrollY = viewport.height * overscrollRatio;
    const minimumX = viewport.width - bounds.right * transform.scale - overscrollX;
    const maximumX = overscrollX - bounds.left * transform.scale;
    const minimumY = viewport.height - bounds.bottom * transform.scale - overscrollY;
    const maximumY = overscrollY - bounds.top * transform.scale;
    return {
      ...transform,
      x: clamp(transform.x, Math.min(minimumX, maximumX), Math.max(minimumX, maximumX)),
      y: clamp(transform.y, Math.min(minimumY, maximumY), Math.max(minimumY, maximumY)),
    };
  }

  function fitTransform(nodes, viewport, stage, options = {}) {
    const padding = options.padding ?? 36;
    const minimumScale = options.minimumScale ?? 0.01;
    const maximumScale = options.maximumScale ?? 1.2;
    const nodeX = nodes.map((node) => node.x * stage.width);
    const nodeY = nodes.map((node) => node.y * stage.height);
    const left = nodes.length ? Math.min(...nodeX) - 90 : 0;
    const right = nodes.length ? Math.max(...nodeX) + 90 : stage.width;
    const top = nodes.length ? Math.min(...nodeY) - 70 : 0;
    const bottom = nodes.length ? Math.max(...nodeY) + 70 : stage.height;
    const contentWidth = Math.max(360, right - left);
    const contentHeight = Math.max(260, bottom - top);
    const scale = Math.max(minimumScale, Math.min(
      (viewport.width - padding) / contentWidth,
      (viewport.height - padding) / contentHeight,
      maximumScale,
    ));
    return {
      scale,
      x: (viewport.width - contentWidth * scale) / 2 - left * scale,
      y: (viewport.height - contentHeight * scale) / 2 - top * scale,
    };
  }

  function zoomScale(currentScale, factor, minimumScale = 0.01, maximumScale = 2) {
    return Math.max(minimumScale, Math.min(maximumScale, currentScale * factor));
  }

  function moveSelectedNodes(nodes, selectedIds, deltaX, deltaY, options = {}) {
    const selected = new Set(selectedIds);
    const moving = nodes.filter((node) => selected.has(node.id));
    if (!moving.length) return [];
    let dx = deltaX;
    let dy = deltaY;
    if (options.snap) {
      const anchor = moving[0];
      dx = Math.round((anchor.x + dx) / options.stepX) * options.stepX - anchor.x;
      dy = Math.round((anchor.y + dy) / options.stepY) * options.stepY - anchor.y;
    }
    if (Number.isFinite(options.minX) && Number.isFinite(options.maxX)) {
      const minNodeX = Math.min(...moving.map((node) => node.x));
      const maxNodeX = Math.max(...moving.map((node) => node.x));
      dx = clamp(dx, options.minX - minNodeX, options.maxX - maxNodeX);
    }
    if (Number.isFinite(options.minY) && Number.isFinite(options.maxY)) {
      const minNodeY = Math.min(...moving.map((node) => node.y));
      const maxNodeY = Math.max(...moving.map((node) => node.y));
      dy = clamp(dy, options.minY - minNodeY, options.maxY - maxNodeY);
    }
    return moving.map((node) => ({id: node.id, x: clean(node.x + dx), y: clean(node.y + dy)}));
  }

  function resolveIconType(node) {
    const explicit = String(node.icon_type || "auto");
    if (explicit !== "auto") return explicit;
    const inferred = String(node.node_type || "unknown");
    return inferred === "unknown" ? "other" : inferred;
  }

  const HIERARCHY_TYPE_PRIORITY = new Map([
    ["router", 0], ["switch", 1], ["access-point", 2], ["server", 3],
    ["workstation", 4], ["printer", 4], ["phone", 4], ["other", 5],
  ]);
  //HORIZONTAL_SPACING: distance between devices on the same row.
  //BRANCH_GAP: additional space between different parent branches.
  //COMPONENT_GAP: space between disconnected networks.
  //VERTICAL_SPACING: distance between hierarchy levels.
  const HIERARCHY_HORIZONTAL_SPACING = 0.075;
  const HIERARCHY_BRANCH_GAP = 0.025;
  const HIERARCHY_COMPONENT_GAP = 0.16;
  const HIERARCHY_VERTICAL_SPACING = 0.15;

  function snapHierarchySpacing(value, step, minimumUnits) {
    if (!Number.isFinite(step) || step <= 0) return value;
    return Math.max(minimumUnits, Math.round(value / step)) * step;
  }

  function resolveHierarchySpacing(options = {}) {
    const rawHorizontalSpacing = Number.isFinite(options.horizontalSpacing)
      && options.horizontalSpacing > 0
      ? options.horizontalSpacing : HIERARCHY_HORIZONTAL_SPACING;
    const rawBranchGap = Number.isFinite(options.branchGap) && options.branchGap >= 0
      ? options.branchGap : HIERARCHY_BRANCH_GAP;
    if (!options.snap) {
      return {horizontalSpacing: rawHorizontalSpacing, branchGap: rawBranchGap};
    }
    const stepX = Number(options.stepX) || 22 / 1800;
    return {
      horizontalSpacing: snapHierarchySpacing(rawHorizontalSpacing, stepX, 1),
      branchGap: snapHierarchySpacing(rawBranchGap, stepX, 0),
    };
  }

  function compareHierarchyNodes(left, right, adjacency) {
    const leftPriority = HIERARCHY_TYPE_PRIORITY.get(left.node_type) ?? 6;
    const rightPriority = HIERARCHY_TYPE_PRIORITY.get(right.node_type) ?? 6;
    return leftPriority - rightPriority
      || (adjacency.get(right.id)?.size || 0) - (adjacency.get(left.id)?.size || 0)
      || String(left.name || left.id).localeCompare(
        String(right.name || right.id), undefined, {numeric: true},
      )
      || String(left.id).localeCompare(String(right.id), undefined, {numeric: true});
  }

  function hierarchyComponents(nodes, adjacency) {
    const nodeById = new Map(nodes.map((node) => [node.id, node]));
    const unseen = new Set(nodeById.keys());
    const components = [];
    const sortedIds = [...unseen].sort((left, right) => String(left).localeCompare(
      String(right), undefined, {numeric: true},
    ));
    sortedIds.forEach((seed) => {
      if (!unseen.has(seed)) return;
      const ids = [];
      const queue = [seed];
      unseen.delete(seed);
      for (let index = 0; index < queue.length; index += 1) {
        const id = queue[index];
        ids.push(id);
        [...(adjacency.get(id) || [])]
          .sort((left, right) => String(left).localeCompare(String(right), undefined, {numeric: true}))
          .forEach((neighbor) => {
            if (!unseen.has(neighbor)) return;
            unseen.delete(neighbor);
            queue.push(neighbor);
          });
      }
      components.push(ids.map((id) => nodeById.get(id)));
    });
    return components;
  }

  function hierarchyTree(component, adjacency) {
    const ordered = [...component].sort((left, right) => compareHierarchyNodes(left, right, adjacency));
    const root = ordered[0];
    const nodeById = new Map(component.map((node) => [node.id, node]));
    const children = new Map(component.map((node) => [node.id, []]));
    const depth = new Map([[root.id, 0]]);
    const parent = new Map([[root.id, null]]);
    const visited = new Set([root.id]);
    const queue = [root.id];
    for (let index = 0; index < queue.length; index += 1) {
      const parentId = queue[index];
      const neighbors = [...(adjacency.get(parentId) || [])]
        .filter((id) => nodeById.has(id) && !visited.has(id))
        .map((id) => nodeById.get(id))
        .sort((left, right) => compareHierarchyNodes(left, right, adjacency));
      neighbors.forEach((child) => {
        visited.add(child.id);
        children.get(parentId).push(child.id);
        depth.set(child.id, depth.get(parentId) + 1);
        parent.set(child.id, parentId);
        queue.push(child.id);
      });
    }
    return {rootId: root.id, children, depth, parent};
  }

  function hierarchyComponentLayout(tree, options = {}) {
    const {horizontalSpacing, branchGap} = resolveHierarchySpacing(options);
    const positions = new Map();
    let leafCursor = horizontalSpacing / 2;
    let previousLeafId = null;

    function placeBranch(id) {
      const childIds = tree.children.get(id) || [];
      if (!childIds.length) {
        if (previousLeafId !== null
          && tree.parent.get(previousLeafId) !== tree.parent.get(id)) {
          leafCursor += branchGap;
        }
        const x = leafCursor;
        leafCursor += horizontalSpacing;
        previousLeafId = id;
        positions.set(id, {x, y: 0.08 + tree.depth.get(id) * HIERARCHY_VERTICAL_SPACING});
        return {left: x, right: x};
      }

      const childSpans = [];
      childIds.forEach((childId) => {
        childSpans.push(placeBranch(childId));
      });
      const span = {
        left: childSpans[0].left,
        right: childSpans[childSpans.length - 1].right,
      };
      positions.set(id, {
        x: (span.left + span.right) / 2,
        y: 0.08 + tree.depth.get(id) * HIERARCHY_VERTICAL_SPACING,
      });
      return span;
    }

    placeBranch(tree.rootId);
    const componentWidth = Math.max(
      horizontalSpacing,
      leafCursor - horizontalSpacing / 2,
    );
    const deepest = Math.max(...tree.depth.values());
    const deepestXs = [...tree.depth.entries()]
      .filter(([, depth]) => depth === deepest)
      .map(([id]) => positions.get(id).x);
    const deepestCenter = (Math.min(...deepestXs) + Math.max(...deepestXs)) / 2;
    const centerOffset = componentWidth / 2 - deepestCenter;
    positions.forEach((position, id) => positions.set(id, {
      ...position,
      x: position.x + centerOffset,
    }));
    return {positions, width: componentWidth};
  }

  function hierarchyForestLayout(trees, options = {}) {
    const components = trees.map((tree) => hierarchyComponentLayout(tree, options));
    const totalWidth = components.reduce((total, component) => total + component.width, 0)
      + HIERARCHY_COMPONENT_GAP * Math.max(0, components.length - 1);
    let componentStart = 0.5 - totalWidth / 2;
    const positions = [];
    components.forEach((component) => {
      component.positions.forEach((position, id) => {
        positions.push({
          id,
          x: clean(componentStart + position.x),
          y: clean(position.y),
        });
      });
      componentStart += component.width + HIERARCHY_COMPONENT_GAP;
    });
    return positions;
  }

  function arrangeNodesHierarchically(nodes, edges, options = {}) {
    if (!nodes.length) return [];
    const nodeById = new Map(nodes.map((node) => [node.id, node]));
    const adjacency = new Map(nodes.map((node) => [node.id, new Set()]));
    edges.forEach((edge) => {
      if (!nodeById.has(edge.source_id) || !nodeById.has(edge.target_id)
        || edge.source_id === edge.target_id) return;
      adjacency.get(edge.source_id).add(edge.target_id);
      adjacency.get(edge.target_id).add(edge.source_id);
    });
    const trees = hierarchyComponents(nodes, adjacency)
      .map((component) => hierarchyTree(component, adjacency));
    const positions = hierarchyForestLayout(trees, options);
    if (!options.snap) return positions;
    const stepX = Number(options.stepX) || 22 / 1800;
    const stepY = Number(options.stepY) || 22 / 1100;
    return positions.map((position) => ({
      ...position,
      x: Math.round(position.x / stepX) * stepX,
      y: Math.round(position.y / stepY) * stepY,
    }));
  }

  const NODE_HORIZONTAL_SPACING = 0.105;
  const NODE_VERTICAL_SPACING = 0.13;
  const GROUP_HORIZONTAL_GAP = 0.18;

  function compareNodes(left, right) {
    return String(left.name || left.address || left.id).localeCompare(
      String(right.name || right.address || right.id), undefined, {numeric: true},
    ) || String(left.id).localeCompare(String(right.id), undefined, {numeric: true});
  }

  function subnetKey(node) {
    const address = String(node.address || "").trim();
    const match = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(address);
    if (!match) return "Unknown / no IP";
    const octets = match.slice(1).map(Number);
    if (octets.some((octet) => octet < 0 || octet > 255)) return "Unknown / no IP";
    return `${octets[0]}.${octets[1]}.${octets[2]}.0/24`;
  }

  function compareSubnetKeys(left, right) {
    if (left === "Unknown / no IP") return 1;
    if (right === "Unknown / no IP") return -1;
    const leftOctets = left.split(/[./]/).slice(0, 4).map(Number);
    const rightOctets = right.split(/[./]/).slice(0, 4).map(Number);
    for (let index = 0; index < 4; index += 1) {
      if (leftOctets[index] !== rightOctets[index]) return leftOctets[index] - rightOctets[index];
    }
    return 0;
  }

  function subnetGroups(nodes) {
    const grouped = new Map();
    [...nodes].sort(compareNodes).forEach((node) => {
      const key = subnetKey(node);
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(node);
    });
    return [...grouped.entries()]
      .sort(([left], [right]) => compareSubnetKeys(left, right))
      .map(([key, members]) => ({key, label: key, nodeIds: members.map(({id}) => id)}));
  }

  function arrangeGroupedNodes(nodes, groups, anchorFirst = false) {
    if (!nodes.length) return [];
    const nodeById = new Map(nodes.map((node) => [node.id, node]));
    const layouts = groups.map((group) => {
      const members = group.nodeIds.map((id) => nodeById.get(id)).filter(Boolean);
      const anchorCount = anchorFirst && group.switchId ? 1 : 0;
      const remainingCount = Math.max(0, members.length - anchorCount);
      const columns = Math.max(1, Math.min(5, Math.ceil(Math.sqrt(Math.max(1, remainingCount) * 1.5))));
      const rows = Math.max(1, Math.ceil(Math.max(1, remainingCount) / columns));
      const width = Math.max(0.18, (columns - 1) * NODE_HORIZONTAL_SPACING + 0.12);
      const height = (anchorCount ? 0.28 : 0.1) + (rows - 1) * NODE_VERTICAL_SPACING + 0.08;
      return {group, members, columns, width, height};
    });
    const groupColumns = Math.min(4, Math.ceil(Math.sqrt(layouts.length * 1.6)));
    const groupRows = Math.ceil(layouts.length / groupColumns);
    const columnWidths = Array.from({length: groupColumns}, (_, column) => Math.max(
      ...layouts.filter((_, index) => index % groupColumns === column).map(({width}) => width),
    ));
    const rowHeights = Array.from({length: groupRows}, (_, row) => Math.max(
      ...layouts.slice(row * groupColumns, (row + 1) * groupColumns).map(({height}) => height),
    ));
    const totalWidth = columnWidths.reduce((sum, width) => sum + width, 0)
      + GROUP_HORIZONTAL_GAP * Math.max(0, groupColumns - 1);
    const columnLefts = [];
    let columnLeft = 0.5 - totalWidth / 2;
    columnWidths.forEach((width) => {
      columnLefts.push(columnLeft);
      columnLeft += width + GROUP_HORIZONTAL_GAP;
    });
    const rowTops = [];
    let rowTop = 0.04;
    rowHeights.forEach((height) => {
      rowTops.push(rowTop);
      rowTop += height + 0.12;
    });
    const positions = [];
    layouts.forEach(({group, members, columns}, groupIndex) => {
      const column = groupIndex % groupColumns;
      const row = Math.floor(groupIndex / groupColumns);
      const centerX = columnLefts[column] + columnWidths[column] / 2;
      const top = rowTops[row];
      const startIndex = anchorFirst && group.switchId ? 1 : 0;
      if (startIndex) positions.push({id: members[0].id, x: clean(centerX), y: clean(top + 0.1)});
      members.slice(startIndex).forEach((node, index) => {
        const memberCount = members.length - startIndex;
        const memberRow = Math.floor(index / columns);
        const itemsInRow = Math.min(columns, memberCount - memberRow * columns);
        const memberRowStart = centerX - ((itemsInRow - 1) * NODE_HORIZONTAL_SPACING) / 2;
        positions.push({
          id: node.id,
          x: clean(memberRowStart + (index % columns) * NODE_HORIZONTAL_SPACING),
          y: clean(top + (startIndex ? 0.28 : 0.1) + memberRow * NODE_VERTICAL_SPACING),
        });
      });
    });
    return positions;
  }

  function arrangeNodesBySubnet(nodes) {
    return arrangeGroupedNodes(nodes, subnetGroups(nodes));
  }

  function isInferredSwitch(node) {
    return node?.node_type === "switch"
      && String(node.metadata?.["Synthetic Role"] || "") === "shared-port-fanout";
  }

  function graphAdjacency(nodes, edges) {
    const ids = new Set(nodes.map(({id}) => id));
    const adjacency = new Map(nodes.map(({id}) => [id, new Set()]));
    edges.forEach((edge) => {
      if (!ids.has(edge.source_id) || !ids.has(edge.target_id) || edge.source_id === edge.target_id) return;
      adjacency.get(edge.source_id).add(edge.target_id);
      adjacency.get(edge.target_id).add(edge.source_id);
    });
    return adjacency;
  }

  function switchGroups(nodes, edges) {
    const nodeById = new Map(nodes.map((node) => [node.id, node]));
    const adjacency = graphAdjacency(nodes, edges);
    const switches = nodes.filter((node) => node.node_type === "switch").sort((left, right) => {
      return Number(isInferredSwitch(left)) - Number(isInferredSwitch(right)) || compareNodes(left, right);
    });
    const owners = new Map();
    [...switches].sort((left, right) => {
      return Number(isInferredSwitch(right)) - Number(isInferredSwitch(left)) || compareNodes(left, right);
    }).forEach((networkSwitch) => {
      [...(adjacency.get(networkSwitch.id) || [])]
        .map((id) => nodeById.get(id))
        .filter((node) => node && node.node_type !== "switch")
        .sort(compareNodes)
        .forEach((node) => { if (!owners.has(node.id)) owners.set(node.id, networkSwitch.id); });
    });
    const groups = switches.map((networkSwitch) => ({
      key: `switch:${networkSwitch.id}`,
      switchId: networkSwitch.id,
      label: networkSwitch.name || networkSwitch.address || networkSwitch.id,
      nodeIds: [networkSwitch.id, ...nodes.filter((node) => owners.get(node.id) === networkSwitch.id)
        .sort(compareNodes).map(({id}) => id)],
    }));
    const assigned = new Set(groups.flatMap(({nodeIds}) => nodeIds));
    const orphanIds = nodes.filter(({id}) => !assigned.has(id)).sort(compareNodes).map(({id}) => id);
    if (orphanIds.length) groups.push({key: "unassigned", switchId: null, label: "Unassigned", nodeIds: orphanIds});
    return groups;
  }

  function arrangeNodesBySwitch(nodes, edges) {
    return arrangeGroupedNodes(nodes, switchGroups(nodes, edges), true);
  }

  function unmanagedGroupChildren(nodes, edges, groupId) {
    const nodeById = new Map(nodes.map((node) => [node.id, node]));
    const group = nodeById.get(groupId);
    if (!isInferredSwitch(group)) return [];
    const adjacency = graphAdjacency(nodes, edges);
    return [...(adjacency.get(groupId) || [])]
      .filter((id) => {
        const node = nodeById.get(id);
        return node && node.node_type !== "switch" && (adjacency.get(id)?.size || 0) === 1;
      })
      .map((id) => nodeById.get(id))
      .sort(compareNodes)
      .map(({id}) => id);
  }

  function visibleTopology(nodes, edges, options = {}) {
    const hiddenTypes = options.hiddenTypes || new Set();
    const collapsedGroupIds = options.collapsedGroupIds || new Set();
    const collapsedChildren = new Set();
    collapsedGroupIds.forEach((id) => {
      unmanagedGroupChildren(nodes, edges, id).forEach((childId) => collapsedChildren.add(childId));
    });
    const visibleNodes = nodes.filter((node) => !hiddenTypes.has(node.node_type)
      && !(options.hideManual && ["local", "manual"].includes(node.source))
      && !collapsedChildren.has(node.id));
    const visibleIds = new Set(visibleNodes.map(({id}) => id));
    return {
      nodes: visibleNodes,
      edges: edges.filter((edge) => visibleIds.has(edge.source_id) && visibleIds.has(edge.target_id)),
    };
  }

  function reconcileVisibleSelection(selection, topology) {
    const visibleNodeIds = new Set(topology.nodes.map(({id}) => id));
    const visibleEdgeIds = new Set(topology.edges.map(({id}) => id));
    const selectedNodeIds = new Set(
      [...(selection.selectedNodeIds || [])].filter((id) => visibleNodeIds.has(id)),
    );
    const selectedNodeId = visibleNodeIds.has(selection.selectedNodeId)
      ? selection.selectedNodeId : null;
    const selectedEdgeId = visibleEdgeIds.has(selection.selectedEdgeId)
      ? selection.selectedEdgeId : null;
    const panelMode = selection.panelMode === "node" && selectedNodeId
      ? "node" : selection.panelMode === "edge" && selectedEdgeId ? "edge" : null;
    return {selectedNodeIds, selectedNodeId, selectedEdgeId, panelMode};
  }

  function nodeDisplayLabel(node, mode = "both") {
    const name = String(node.name || "").trim();
    const address = String(node.address || "").trim();
    const fallback = name || address || String(node.id || node.node_type || "Unknown device");
    if (mode === "hostname") return Object.freeze({primary: name || address || fallback, secondary: ""});
    if (mode === "ip") return Object.freeze({primary: address || name || fallback, secondary: ""});
    return Object.freeze({primary: name || address || fallback, secondary: name && address && name !== address ? address : ""});
  }

  function focusTransform(node, viewport, stage, scale = 1.25) {
    return {
      x: viewport.width / 2 - node.x * stage.width * scale,
      y: viewport.height / 2 - node.y * stage.height * scale,
      scale,
    };
  }

  function orthogonalEdgePath(source, target, stage) {
    const sourceX = clean(source.x * stage.width);
    const sourceY = clean(source.y * stage.height);
    const targetX = clean(target.x * stage.width);
    const targetY = clean(target.y * stage.height);
    if (sourceX === targetX && sourceY === targetY) return `M ${sourceX} ${sourceY}`;
    if (sourceX === targetX) return `M ${sourceX} ${sourceY} V ${targetY}`;
    if (sourceY === targetY) return `M ${sourceX} ${sourceY} H ${targetX}`;
    const branchY = clean((sourceY + targetY) / 2);
    return `M ${sourceX} ${sourceY} V ${branchY} H ${targetX} V ${targetY}`;
  }

  const VLAN_COLORS = [
    "#38bdf8", "#34d399", "#fbbf24", "#fb7185", "#a78bfa", "#22d3ee",
    "#f97316", "#4ade80", "#60a5fa", "#e879f9", "#facc15", "#2dd4bf",
  ];

  function compareVlanIds(left, right) {
    return Number(left) - Number(right) || String(left).localeCompare(String(right));
  }

  function vlanIdsFromValue(value) {
    const matches = String(value ?? "").match(/\d{1,4}/g) || [];
    return [...new Set(matches.filter((item) => Number(item) >= 1 && Number(item) <= 4094))]
      .sort(compareVlanIds);
  }

  function isVlanMetadataKey(key) {
    const normalized = String(key).toLowerCase().replaceAll(/[_-]+/g, " ").trim();
    return normalized === "vlan" || normalized === "vlans" || normalized === "vlan id"
      || normalized === "pvid" || normalized === "pvid vlan"
      || normalized === "tagged vlan" || normalized === "untagged vlan";
  }

  function nodeVlanIds(node, interfaces = [], vlans = []) {
    const ids = new Set();
    Object.entries(node.metadata || {}).forEach(([key, value]) => {
      if (isVlanMetadataKey(key)) vlanIdsFromValue(value).forEach((id) => ids.add(id));
    });
    interfaces
      .filter((item) => item.device_id === node.id)
      .forEach((item) => vlanIdsFromValue(item.vlan).forEach((id) => ids.add(id)));
    if (node.source === "lantopolog" && node.address) {
      const switchKey = node.external_id || `switch:${node.address}`;
      vlans
        .filter((item) => item.switch_key === switchKey)
        .forEach((item) => vlanIdsFromValue(item.vlan_id).forEach((id) => ids.add(id)));
    }
    return [...ids].sort(compareVlanIds);
  }

  function vlanColor(vlanId) {
    const numericId = Number(vlanId);
    const index = Number.isFinite(numericId)
      ? numericId % VLAN_COLORS.length
      : [...String(vlanId)].reduce((total, character) => total + character.charCodeAt(0), 0) % VLAN_COLORS.length;
    return VLAN_COLORS[index];
  }

  function vlanNameMap(vlans) {
    const names = new Map();
    vlans.forEach((item) => {
      const [id] = vlanIdsFromValue(item.vlan_id);
      const name = String(item.name || "").trim();
      if (id && name && !names.has(id)) names.set(id, name);
    });
    return names;
  }

  function vlanNodeDecoration(node, interfaces = [], vlans = [], options = {}) {
    const ids = nodeVlanIds(node, interfaces, vlans);
    if (!ids.length) return null;
    const maxSegments = Math.max(1, Number(options.maxSegments || 4));
    const names = vlanNameMap(vlans);
    const items = ids.map((id) => {
      const name = names.get(id) || "";
      return {
        id,
        label: `VLAN ${id}`,
        title: name ? `VLAN ${id} - ${name}` : `VLAN ${id}`,
        color: vlanColor(id),
      };
    });
    const segments = items.slice(0, maxSegments);
    const background = segments.length === 1
      ? segments[0].color
      : `linear-gradient(90deg, ${segments.map((item, index) => {
        const left = clean(index * 100 / segments.length);
        const right = clean((index + 1) * 100 / segments.length);
        return `${item.color} ${left}%, ${item.color} ${right}%`;
      }).join(", ")})`;
    return {
      ids,
      label: ids.length === 1 ? `VLAN ${ids[0]}` : `${ids.length} VLANs`,
      title: items.map((item) => item.title).join(", "),
      color: items[0].color,
      background,
      segments,
      extraCount: Math.max(0, ids.length - maxSegments),
    };
  }

  function vlanGroups(nodes, interfaces = [], vlans = []) {
    const names = vlanNameMap(vlans);
    const byKey = new Map();
    nodes.forEach((node) => {
      const ids = nodeVlanIds(node, interfaces, vlans);
      const key = ids.length === 1 ? `vlan:${ids[0]}` : ids.length > 1 ? "shared" : "unassigned";
      if (!byKey.has(key)) {
        const vlanId = ids.length === 1 ? ids[0] : "";
        const vlanName = names.get(vlanId);
        byKey.set(key, {
          key,
          vlanId,
          label: vlanId
            ? `VLAN ${vlanId}${vlanName ? ` · ${vlanName}` : ""}`
            : key === "shared" ? "Shared / trunk" : "Unassigned",
          color: vlanId ? vlanColor(vlanId) : key === "shared" ? "#a78bfa" : "#64748b",
          nodeIds: [],
        });
      }
      byKey.get(key).nodeIds.push(node.id);
    });
    return [...byKey.values()].sort((left, right) => {
      if (left.vlanId && right.vlanId) return compareVlanIds(left.vlanId, right.vlanId);
      if (left.vlanId) return -1;
      if (right.vlanId) return 1;
      return left.key === "shared" ? -1 : right.key === "shared" ? 1 : 0;
    });
  }

  function arrangeNodesByVlan(nodes, interfaces = [], vlans = []) {
    const groups = vlanGroups(nodes, interfaces, vlans);
    if (!groups.length) return [];
    const outerLeft = 0.04;
    const outerTop = 0.06;
    const availableWidth = 0.92;
    const availableHeight = 0.88;
    const groupColumns = Math.min(4, Math.ceil(Math.sqrt(groups.length * 1.6)));
    const groupRows = Math.ceil(groups.length / groupColumns);
    const groupWidth = availableWidth / groupColumns;
    const groupHeight = availableHeight / groupRows;
    const nodeById = new Map(nodes.map((node) => [node.id, node]));
    const positions = [];
    groups.forEach((group, groupIndex) => {
      const members = group.nodeIds.map((id) => nodeById.get(id)).filter(Boolean);
      const memberColumns = Math.max(1, Math.ceil(Math.sqrt(members.length * (groupWidth / groupHeight))));
      const memberRows = Math.ceil(members.length / memberColumns);
      const groupColumn = groupIndex % groupColumns;
      const groupRow = Math.floor(groupIndex / groupColumns);
      members.forEach((node, memberIndex) => {
        positions.push({
          id: node.id,
          x: clean(outerLeft + groupColumn * groupWidth
            + ((memberIndex % memberColumns) + 1) * groupWidth / (memberColumns + 1)),
          y: clean(outerTop + groupRow * groupHeight
            + (Math.floor(memberIndex / memberColumns) + 1) * groupHeight / (memberRows + 1)),
        });
      });
    });
    return positions;
  }

  const iconBodies = {
    router: '<ellipse cx="32" cy="22" rx="24" ry="10"/><path d="M8 22v14c0 6 11 10 24 10s24-4 24-10V22"/><path class="accent" d="M20 20h9l-4-4m19 8h-9l4 4M32 13v9l4-4m-8 17v-9l-4 4"/>',
    switch: '<path d="M7 20l10-9h40l-10 9z"/><rect x="7" y="20" width="40" height="24" rx="3"/><path d="M47 20l10-9v23L47 44z"/><path class="accent" d="M14 29h5m4 0h5m4 0h5m4 0h3M14 36h5m4 0h5m4 0h5m4 0h3"/>',
    "access-point": '<path d="M22 45h20l-4-8H26z"/><rect x="27" y="25" width="10" height="14" rx="3"/><path class="accent" d="M20 29c0-7 5-12 12-12s12 5 12 12M13 26C13 16 21 8 32 8s19 8 19 18"/>',
    server: '<path d="M16 5h30l7 7v42H16z"/><path d="M46 5v9h7"/><path class="accent" d="M23 18h21M23 29h21M23 40h21"/><circle class="light" cx="24" cy="48" r="2"/><circle class="light" cx="31" cy="48" r="2"/>',
    workstation: '<rect x="10" y="8" width="44" height="33" rx="4"/><path d="M26 42h12l3 8H23zM18 52h28"/><path class="accent" d="M16 14h32v21H16z"/>',
    printer: '<path d="M18 7h28v15H18z"/><rect x="10" y="20" width="44" height="25" rx="5"/><path d="M18 36h28v20H18z"/><path class="accent" d="M23 42h18m-18 6h14"/><circle class="light" cx="47" cy="28" r="2"/>',
    phone: '<path d="M20 7h24l5 49H15z"/><rect class="accent" x="22" y="14" width="20" height="27" rx="2"/><circle class="light" cx="32" cy="49" r="3"/>',
    other: '<path d="M32 6l23 13v26L32 58 9 45V19z"/><path class="accent" d="M9 19l23 14 23-14M32 33v25"/>',
  };

  function topologyIcon(type) {
    const safeType = Object.hasOwn(iconBodies, type) ? type : "other";
    const body = iconBodies[safeType];
    return `<svg class="topology-icon topology-icon-${safeType}" viewBox="0 0 64 64" aria-hidden="true">${body}</svg>`;
  }

  return {
    arrangeNodesHierarchically,
    arrangeNodesBySubnet,
    arrangeNodesBySwitch,
    arrangeNodesByVlan,
    cancelViewportGestures,
    constrainPanToBounds,
    fitTransform,
    focusTransform,
    moveSelectedNodes,
    nodeDisplayLabel,
    nodeVlanIds,
    nodesInsideBox,
    normalizeBox,
    orthogonalEdgePath,
    reconcileVisibleSelection,
    resolveIconType,
    subnetGroups,
    subnetKey,
    switchGroups,
    shouldAddMarqueeSelection,
    topologyIcon,
    unmanagedGroupChildren,
    vlanGroups,
    vlanNodeDecoration,
    viewportToWorld,
    visibleTopology,
    zoomScale,
  };
}));
