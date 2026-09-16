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

Only the latest state, check time, and measured round-trip duration are stored on each node. There is no liveness history or discovery behavior.

## Run from source

Python 3.11 or newer is required.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe run.py
```

Open [http://localhost:8092](http://localhost:8092). Source runs store the database in `data/status.db`.

When `dist\StatusVisualizer.exe` is run directly, it stores the database in
`%LOCALAPPDATA%\StatusVisualizer\status.db`, so no Administrator access is needed.

Options:

```text
--host       Bind address (default 127.0.0.1)
--port       HTTP port (default 8092)
--data-dir   Directory containing status.db
```

`STATUS_VISUALIZER_DATA_DIR` can also set the data directory.

## Run with Docker

Docker Desktop or another Docker Engine with Compose is required. From the project directory, build and start the application with:

```powershell
docker compose up --build -d
```

Open [http://localhost:8092](http://localhost:8092). The SQLite database is stored in the Compose-managed `status-visualizer-data` volume, so topology data and manual edits survive image rebuilds and container replacement. Docker Compose prefixes its physical volume name with the project name. The Lantopolog folder is still selected in the browser and uploaded as one import; it does not need to be mounted into the container.

If port `8092` is already in use, choose another host port before starting Compose:

```powershell
$env:STATUS_VISUALIZER_PORT = "18092"
docker compose up --build -d
```

Then open `http://localhost:18092`.

Useful commands:

```powershell
# Follow application logs
docker compose logs -f

# Stop the application without deleting its database
docker compose down

# Stop the application and permanently delete its database volume
docker compose down -v
```

The Compose configuration publishes the dashboard only on the Docker host's loopback interface. To intentionally make it available to other computers, start Compose with `STATUS_VISUALIZER_BIND=0.0.0.0` and protect access at the host firewall or reverse proxy. The container runs as a non-root user with a read-only application filesystem; only the named `/data` volume is writable. `NET_RAW` is added back after dropping other Linux capabilities because the current-status feature requires ICMP ping.

On Windows, the checks originate from Docker Desktop's Linux VM and its NAT network rather than directly from the Windows host. ICMP reachability can therefore differ from the standalone EXE, particularly with VPNs, segmented networks, or host firewall rules. Verify the results against representative LAN devices before relying on Docker-based status checks.

An optional destructive smoke test creates an isolated temporary Compose project and volume, verifies health and database persistence across container replacement, and then deletes only those temporary resources:

```powershell
.\scripts\docker-smoke.ps1
```

## Run on a Linux VM without Docker

The native Linux installation targets a systemd-based VM and requires Python 3.11 or newer, Python venv support, and `iputils-ping`. Copy the project source to the VM, change into its directory, and then install it. For Ubuntu or Debian:

```bash
sudo apt update
sudo apt install -y python3 python3-venv iputils-ping
sudo bash scripts/install-linux.sh
```

The secure default listens only on the VM's loopback interface. Reach it from your workstation through an SSH tunnel:

```bash
ssh -L 8092:127.0.0.1:8092 user@vm-address
```

Then open `http://localhost:8092` on your workstation. To make the dashboard directly reachable on the VM's network interface, install with:

```bash
sudo bash scripts/install-linux.sh --host 0.0.0.0 --port 8092
```

Status Visualizer does not have authentication, so a network-bound installation must be limited to trusted source addresses using the VM firewall or a protected reverse proxy. Do not expose it directly to the internet. Native Linux ICMP checks originate from the VM itself, which generally makes this deployment better suited to checking devices on the VM's LAN than Docker Desktop on Windows.

The installer creates a non-login `status-visualizer` service account, installs the application under `/opt/status-visualizer`, stores SQLite data under `/opt/status-visualizer/data/status.db`, and enables a hardened `status-visualizer.service`. It can be safely rerun from a newer project copy to update the application without replacing its database.

Both locations are configurable. If `--data-dir` is omitted, it defaults to a `data` directory under the selected installation directory:

```bash
sudo bash scripts/install-linux.sh \
  --install-dir /srv/status-visualizer \
  --data-dir /srv/status-visualizer/data
```

Use the same path options when uninstalling a custom installation. A normal uninstall preserves the data directory; `--purge-data` deletes it.

Operations:

```bash
sudo systemctl status status-visualizer
sudo journalctl -u status-visualizer -f
sudo systemctl restart status-visualizer

# Remove the application but preserve topology data
sudo bash scripts/uninstall-linux.sh

# Permanently remove the application and topology database
sudo bash scripts/uninstall-linux.sh --purge-data
```

For a consistent backup, stop the service before copying `status.db`, then start it again. The browser-based Lantopolog import works the same way as it does on Windows; the export folder remains on the workstation and is uploaded through the browser.

## Editing behavior

- Drag nodes or open **Arrange** to apply hierarchy, switch, VLAN, or subnet layouts. **Save current** and **Restore saved** preserve one custom arrangement independently of those defaults.
- Add manual nodes and connections alongside imported data.
- Editing an imported node with **Preserve edits on re-import** enabled keeps its display name, type, icon, shape, notes, and position on later imports while refreshing source metadata, IP, MAC, ports, links, and VLANs.
- Imported node IDs and connection IDs are deterministic, so re-import is stable.

## Input safety

The backend accepts at most 20 uploaded entries from the UI model. The parser additionally enforces recognized filenames, 64 files maximum, 5 MiB per file, and 20 MiB total. CSV is parsed with quote-aware semicolon handling. XML is parsed only for the bounded mxGraph structure; file paths and parser internals are not exposed in API error responses. Imported values are rendered as text or HTML-escaped by the UI.

## Standalone Windows build and startup

```powershell
.\scripts\build.ps1
```

The result is `dist\StatusVisualizer.exe`. From an elevated PowerShell window, `scripts\install.ps1` installs it under Program Files, stores topology data under ProgramData, registers an at-startup scheduled task bound to `127.0.0.1`, and removes any older Status Visualizer firewall rules.

## APIs

- `GET /api/health`
- `GET /api/topology`
- `GET /api/liveness`
- `POST /api/liveness/check` (empty JSON object; checks saved nodes only)
- `POST /api/import/lantopolog`
- Device CRUD under `/api/devices`
- `GET`, `POST`, and `DELETE` under `/api/edges`

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest --cov=app --cov-branch --cov-fail-under=80
.\.venv\Scripts\ruff.exe check app tests run.py
.\.venv\Scripts\pyright.exe
node --check app\static\app.js
```
