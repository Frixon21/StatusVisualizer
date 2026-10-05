# Lantopolog Topology Visualizer

A self-hosted topology dashboard for Windows or Linux that imports a complete Lantopolog 2 export in one action. It does not discover the network, query SNMP, or synchronize a separate monitoring API. It performs bounded ICMP checks only for the current liveness display.

## Import a topology

1. In Lantopolog, generate its normal export.
2. Open the dashboard and choose **Import Lantopolog**.
3. Select the Lantopolog `Export` folder once.

The browser reads only recognized Lantopolog export filenames and sends them to the local app in one request. The import is validated and committed to SQLite as one transaction, so a malformed export cannot partially replace the current topology. Re-importing the same folder updates the imported topology without duplicating it; manually created nodes remain.

## Editing the topology

- The topology editor has a bounded height; the inventory scrolls independently.
- The topology canvas extends beyond its original layout rectangle: pan to reach additional space, and use **Fit** to recover every placed node.
- Choose **Box select**, then drag around nodes to select them. In **Pan** mode, hold Shift and drag for a temporary box selection; an empty Shift-drag clears the selection. Drag any selected node to move the group.
- **Snap grid** aligns group movement to the visible grid without changing spacing between selected nodes. It also grid-aligns positions produced by **Arrange hierarchy** when the toggle is on.
- Open a node and choose **Edit** to select its network icon and display shape. Visual overrides marked for preservation survive later imports.

## VLAN view

- **VLAN view** colors each device directly instead of drawing overlapping regions. A device with one resolved VLAN receives a solid accent stripe and badge; a device with several VLANs receives a segmented stripe with one color per VLAN and an **N VLANs** badge. Hover the badge to see the full VLAN list.
- Membership is combined from endpoint VLAN metadata, interface PVIDs, and VLANs configured on managed switches. VLAN IDs found on endpoints are still shown even when `vlan_list.csv` does not name them.
- Devices without usable VLAN evidence remain neutral. The legend lists each VLAN and how many visible devices participate in it, plus a count of devices that belong to multiple VLANs.
- **Arrange by VLAN** saves a separated VLAN layout to the existing topology positions. The overlay can be turned off without undoing the layout, and **Arrange by subnet** remains available.

## Hierarchical layout

- **Arrange hierarchy** builds a top-down view from the imported connections. Routers are preferred as roots, followed by high-connectivity switches and other infrastructure.
- The layout is calculated from the leaves upward. The deepest row is centered first, each parent is centered over its descendant leaf span, and sibling branches keep their own horizontal space. Wide rows expand beyond the original stage instead of compressing device cards together; **Fit** scales the expanded hierarchy into view. Cycles, multiple-parent links, disconnected islands, and isolated nodes are retained in a deterministic spanning forest.
- Automatic layouts and saved-layout actions are grouped under **Arrange**. Use **Save current** to snapshot a manually refined layout, try any automatic layout, and use **Restore saved** to return to the snapshot. The one saved arrangement is stored in SQLite and survives an app restart.

## Navigating large topologies

- Open **Arrange** to choose hierarchy, switch, VLAN, or subnet layouts. **Arrange by switch** creates compact switch-centered groups, including the inferred shared-port fan-out nodes. **Arrange by subnet** builds real IPv4 `/24` groups and keeps devices without a valid IP in an explicit unknown group.
- Inferred unmanaged-switch groups have a small `−` control above the switch. Collapse it to hide only its leaf devices, or use **Display → Collapse unmanaged groups** and **Expand all groups** for the whole topology. Hidden children and their links are removed together, so collapsed views never leave dangling connections.
- Search the inventory and press Enter or choose **Focus** to reveal, select, and center the first match. Each inventory row also has its own focus target. Focusing a filtered or collapsed device automatically reveals the relevant category and unmanaged group. Use the chevron beside **Inventory** to collapse it into a narrow left rail and give the topology more room.
- **Display** can hide routers, switches, access points, servers, workstations, printers, phones, other/unknown devices, or manually added devices. It can also show node labels as hostname, IP address, or both. Display preferences are stored in the browser and do not delete or modify inventory data.
- Use the **Light mode / Dark mode** control in the header to switch the entire dashboard theme. The choice is stored in the browser and restored on the next visit.
- **Save diagram PDF** downloads a standalone, single-page vector diagram instead of printing the browser page. The export uses the currently selected light or dark theme; light exports use a pure white, ink-friendly page instead of the dashboard's tinted canvas. It crops to the visible topology, preserves each device's icon/card/circle choice and matching device glyph, and keeps labels, connection routing, and enabled VLAN accents sharp at any zoom level. It omits transient liveness dots and their ping legend along with the inventory, toolbars, dotted grid, selection controls, and browser headers. Hidden categories and collapsed devices remain excluded.
- Connections use orthogonal routing: parent/child links share horizontal branch lines and turn through rounded right angles instead of crossing the canvas as long diagonals. The routes update immediately when nodes move and remain selectable for connection details.

The importer uses every useful top-level export product:

- `sw_list.csv` — infrastructure identity, management IP, MAC, model, serial, SNMP version, location, and description.
- `complist.csv` — endpoint identity, switch/port attachment, VLAN, vendor, user/domain/custom values, and any populated hardware or OS inventory fields.
- `complist2.csv` — compact endpoint fallback when `complist.csv` is absent.
- `port_list.csv` — sectioned per-switch port state, speed, duplex, STP, alias, and VLAN data.
- `vlan_list.csv` — VLAN names and tagged/untagged port ranges.
- `sw_conn.csv` — switch-to-switch connections, with mirrored rows collapsed.
- `top_map.xml` — Lantopolog/draw.io positions and fallback infrastructure links.

Top-level files win over `Tmp` copies. `Tmp` files are used only as compatibility fallbacks. Missing port rows are synthesized from endpoint and connection records, MAC-only endpoints are retained, placeholder values such as `demo` or `xx` never become fake IP nodes, and all non-empty imported fields remain available in the node/connection detail panel.

When multiple real endpoint identities appear behind the same nonzero managed-switch port, the importer adds one deterministic **Inferred switch/bridge** node between that port and the endpoints. This preserves Lantopolog's fan-out instead of drawing misleading direct links. The node is explicitly marked as inferred because the downstream bridge may be an unmanaged physical switch, a virtual switch, or another transparent device. Synthetic ports such as `00` remain direct attachments.

## Current liveness

The app checks saved, visible nodes every 30 seconds and exposes **Check status** for an immediate refresh. It launches at most 12 ICMP checks at once, with a one-second per-host timeout. Only literal private or link-local IP addresses are eligible; blank values, hostnames, URLs, public addresses, and inferred nodes without an address remain **Not checked**. **No ping reply** means the node did not answer ICMP and may also indicate that its firewall blocks ping.

Remote MQTT sites are checked by their LanTopoLog MQTT service from inside the customer LAN. The dashboard consumes the retained site status message, keeps **Online**, **No ping reply**, and **Not checked** semantics, and shows the remote status as stale/unavailable when the service has not reported recently. Central **Check status** stays local-only.

Only the latest state, check time, and measured round-trip duration are stored on each node. There is no liveness history or discovery behavior.

## Run from source

Python 3.11 or newer is required.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe run.py
```

Open [http://localhost:8092](http://localhost:8092). Source runs and the portable EXE both store the database next to the app in a `data\` folder (`data\status.db`).

When running from source on this machine, open [http://127.0.0.1:8092/dev/heatmap](http://127.0.0.1:8092/dev/heatmap) to tune packet-loss heatmap colors with sliders. **Save** writes `app/static/heatmap-bands.json` and `heatmap-bands.js`, which are included in dist builds. The tuner page and its API are omitted from the frozen EXE and stripped from Linux dashboard copies.

The dev tuner does **not** work when `StatusVisualizer.exe` from `dist-status-visualizer\Dashboard` is bound to port 8092. Stop that process (Task Manager or close the dashboard window), then start the app with `run.py` from this repo. Confirm with [http://127.0.0.1:8092/api/health](http://127.0.0.1:8092/api/health): `"heatmap_dev_tools": true` means the tuner is available.

When `StatusVisualizer.exe` is double-clicked from a folder (for example `dist-status-visualizer\Dashboard`), it uses that folder's `data\` directory so the whole pack stays portable.

Options:

```text
--host       Bind address (default 127.0.0.1)
--port       HTTP port (default 8092)
--data-dir   Directory containing status.db
```

`STATUS_VISUALIZER_DATA_DIR` can also set the data directory.

## Archived Docker / Linux installers

Legacy Docker and Expo launcher files were moved under `to_delete\`. Use the deployment package below for Windows or Linux.

## Editing behavior

- Drag nodes or open **Arrange** to apply hierarchy, switch, VLAN, or subnet layouts. **Save current** and **Restore saved** preserve one custom arrangement independently of those defaults.
- Add manual nodes and connections alongside imported data.
- Manual nodes can be assigned an access VLAN and tagged VLAN IDs. Choose the **Text label** shape to add draggable diagram annotations without a device icon or container.
- Editing an imported node with **Preserve edits on re-import** enabled keeps its display name, type, icon, shape, notes, and position on later imports while refreshing source metadata, IP, MAC, ports, links, and VLANs.
- Imported node IDs and connection IDs are deterministic, so re-import is stable.

## Input safety

The backend accepts at most 20 uploaded entries from the UI model. The parser additionally enforces recognized filenames, 64 files maximum, 5 MiB per file, and 20 MiB total. CSV is parsed with quote-aware semicolon handling. XML is parsed only for the bounded mxGraph structure; file paths and parser internals are not exposed in API error responses. Imported values are rendered as text or HTML-escaped by the UI.

## Standalone Windows build and startup

```powershell
.\scripts\build.ps1
```

The result is `dist\StatusVisualizer.exe`. From an elevated PowerShell window, `scripts\install.ps1` installs it under Program Files, stores portable topology data under `Program Files\StatusVisualizer\data`, and registers an at-startup scheduled task bound to `127.0.0.1`.

## APIs

- `GET /api/health`
- `GET /api/sites`
- `GET /api/mqtt/clients`
- `PATCH /api/mqtt/clients/{uuid}`
- `GET /api/topology`
- `GET /api/liveness`
- `POST /api/liveness/check` (empty JSON object; checks saved nodes only)
- `POST /api/import/lantopolog`
- Device CRUD under `/api/devices`
- `GET`, `POST`, and `DELETE` under `/api/edges`

Topology APIs accept `X-Status-Visualizer-Site`; omit it or use `local` for the original local database.

## Deployment package (recommended)

Build once on a machine with Python:

```powershell
.\scripts\build-dist-status-visualizer.ps1
```

That creates `dist-status-visualizer\` with Windows and Linux folders:

| Folder | Where it goes | What you do |
|---|---|---|
| `Dashboard` | PC that shows the UI | Edit host/username in `mqtt.json` if needed, double-click **Start Status Visualizer.bat** (asks for password and verifies it) |
| `MqttService` | Each LanTopoLog VM | Edit `config.json` (export folder + host/username), double-click **INSTALL Service.bat** once |
| `Dashboard-Linux` | Linux dashboard host | Edit `mqtt.json`, then run `chmod +x *.sh && sudo ./INSTALL.sh` |
| `MqttService-Linux` | Linux LanTopoLog host | Edit `config.json`, then run `chmod +x *.sh && sudo ./INSTALL.sh` |

Linux folders install source into an isolated Python virtual environment and register systemd services.

You do **not** copy the whole repository or huge export folders between machines. Each host only needs its matching folder. LanTopoLog keeps exporting locally; the service publishes one retained MQTT ZIP to your existing broker.

### Advanced / source runs

MQTT also auto-loads from `mqtt.json` next to `StatusVisualizer.exe`, or from `%LOCALAPPDATA%\StatusVisualizer\mqtt.json`. For source runs you can still set `STATUS_VISUALIZER_MQTT_CONFIG`. Broker outages do not block startup.

In the UI:

1. Open **MQTT clients** when the pending badge appears.
2. Enter a friendly name and **Approve** (or **Block**).
3. Use the site selector to switch from Local to approved remotes. Selection is per browser tab.
4. Remote sites show the latest retained helper status, or stale/unavailable when the helper has not reported recently. Central ICMP checks stay local-only.

See `lantopolog_mqtt_helper/README.md` for service details. Identity and last-published hash survive upgrades under the existing `%ProgramData%\StatusVisualizer\MqttHelper` compatibility path.

Topics must never contain customer names, hostnames, addresses, or locations. Logs include UUIDs and bounded errors, never passwords or full snapshot payloads.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest --cov=app --cov-branch --cov-fail-under=95
.\.venv\Scripts\ruff.exe check app tests run.py
.\.venv\Scripts\pyright.exe
node --check app\static\app.js
```
