# Tech Expo Multi-Network Runbook

This deployment keeps the prepared network demonstrations isolated while leaving port 6042 as the stable presentation entry point.

## Install or update the expo services

The normal Linux installer must already have created `/srv/StatusVisualizer`, its virtual environment, and the `status-visualizer` service account. From the transferred release folder, run:

```bash
sudo bash scripts/install-expo-linux.sh
```

The installer makes a transactionally consistent backup of the existing database below `/srv/StatusVisualizer/backups`, starts and validates all topology instances, and only then replaces the legacy service on port 6042 with the launcher. If launcher cutover fails, it restores the legacy service automatically.

| View | URL | Database |
|---|---|---|
| Launcher | `http://<expo-host>:6042/` | None |
| Flat | `http://<expo-host>:6043/` | `/srv/StatusVisualizer/data/flat/status.db` |
| Compartmentalized | `http://<expo-host>:6044/` | `/srv/StatusVisualizer/data/compartmentalized/status.db` |
| Live | `http://<expo-host>:6045/` | `/srv/StatusVisualizer/data/live/status.db` |

## Operate and troubleshoot

```bash
systemctl status status-visualizer-launcher
systemctl status status-visualizer@flat status-visualizer@compartmentalized status-visualizer@live
sudo systemctl restart status-visualizer-launcher
sudo systemctl restart status-visualizer@flat
journalctl -u status-visualizer-launcher -f
journalctl -u status-visualizer@flat -f
```

Health checks:

```bash
curl --fail http://127.0.0.1:6042/api/health
curl --fail http://127.0.0.1:6043/api/health
curl --fail http://127.0.0.1:6044/api/health
curl --fail http://127.0.0.1:6045/api/health
```

Import the flat and compartmentalized Lantopolog export folders in their respective browser views. Layout edits and manual nodes are stored only in that instance's database.

## Activate the live demonstration

Keep SNMP credentials in Lantopolog; never place a password, community string, or token in Status Visualizer or the launcher configuration.

1. Use Lantopolog to create the live export and import it at `http://<expo-host>:6045/`.
2. Verify `http://127.0.0.1:6045/api/health`, topology contents, ICMP status, layout, and PDF export.
3. Edit `/srv/StatusVisualizer/expo-launcher/networks.json` and change only the live entry from `"visible": false` to `"visible": true`.
4. Reload the launcher. A service restart is not required.

To withdraw the live demonstration, change the value back to `false`.

## Presentation entry

The presentation project's `slides.json` is maintained outside this repository. Replace its Status Visualizer entry with:

```json
{
  "name": "Status Visualizer",
  "tag": "Compare Network Designs",
  "description": "Explore flat and compartmentalized network topologies.",
  "image": "",
  "ip": "",
  "port": "6042",
  "path": "/"
}
```

## Security and rehearsal

- Restrict TCP ports 6042–6045 to the trusted expo client subnet in the VM firewall. The services have no authentication and must not be internet-accessible.
- Test from the real presentation computer using the VM hostname or IP; testing only through localhost is insufficient.
- Reboot the VM and confirm all four enabled services return healthy.
- Confirm browser Back returns from a topology to the launcher.
- Stop `status-visualizer@live` and confirm the two prepared demonstrations still work.
- Complete an offline rehearsal with SNMP and external network access unavailable.

## Removal and rollback

Remove the expo services while preserving all topology databases and backups:

```bash
sudo bash scripts/uninstall-expo-linux.sh
```

Adding `--purge-data` permanently deletes the entire `/srv/StatusVisualizer/data` directory, including the prepared instance databases. It does not delete `/srv/StatusVisualizer/backups`.

The installer’s automatic rollback restores the old service before the original database is retired. For a manual rollback after a successful expo cutover, first identify the exact archived `status.db.retired-from-data` file under `/srv/StatusVisualizer/backups/<timestamp>/`. Then restore that selected file before starting the legacy service:

```bash
sudo systemctl stop status-visualizer.service
sudo install -o status-visualizer -g status-visualizer -m 0640 \
  /srv/StatusVisualizer/backups/<timestamp>/status.db.retired-from-data \
  /srv/StatusVisualizer/data/status.db
sudo systemctl enable --now status-visualizer.service
curl --fail http://127.0.0.1:6042/api/health
```

Replace `<timestamp>` with the reviewed backup directory name; do not paste the placeholder literally. Keep the backup copy in place until the restored service and topology have been verified.
