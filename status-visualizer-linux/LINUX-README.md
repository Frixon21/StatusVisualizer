# Status Visualizer Linux Package

Copy this entire folder to the Linux machine, then choose one of the run modes below.

## Option 1: Native systemd install

Use this on a Linux VM or server where you want Status Visualizer to start automatically.

Prerequisites:

- Python 3.11 or newer
- python3-venv
- iputils-ping
- systemd

Ubuntu/Debian prerequisites:

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv iputils-ping
```

Install and start the service:

```bash
cd /path/to/status-visualizer-linux
sudo bash scripts/install-linux.sh
```

The default install binds to `127.0.0.1:8092`. From your own computer, connect through SSH:

```bash
ssh -L 8092:127.0.0.1:8092 <user>@<linux-machine>
```

Then open:

```text
http://localhost:8092
```

To intentionally expose it on the Linux machine network interface:

```bash
sudo bash scripts/install-linux.sh --host 0.0.0.0 --port 8092
```

Status Visualizer does not have authentication. If you bind to `0.0.0.0`, restrict access with the VM firewall, cloud security group, or a protected reverse proxy.

Useful service commands:

```bash
systemctl status status-visualizer
journalctl -u status-visualizer -f
sudo systemctl restart status-visualizer
```

Uninstall but keep topology data:

```bash
sudo bash scripts/uninstall-linux.sh
```

Uninstall and delete topology data:

```bash
sudo bash scripts/uninstall-linux.sh --purge-data
```

Native install locations:

- App: `/opt/status-visualizer`
- Data: `/opt/status-visualizer/data/status.db`
- Service: `/etc/systemd/system/status-visualizer.service`

### Custom installation and database paths

Both paths can be changed during installation. Paths must be absolute and
cannot contain spaces. If `--data-dir` is omitted, it defaults to a `data`
directory under the selected installation directory.

```bash
sudo bash scripts/install-linux.sh \
  --install-dir /srv/status-visualizer \
  --data-dir /srv/status-visualizer/data
```

For example, this also places the database under a different disk or mount:

```bash
sudo bash scripts/install-linux.sh \
  --install-dir /srv/status-visualizer \
  --data-dir /mnt/app-data/status-visualizer
```

When uninstalling a custom installation, repeat the same paths:

```bash
sudo bash scripts/uninstall-linux.sh \
  --install-dir /srv/status-visualizer \
  --data-dir /srv/status-visualizer/data
```

Normal uninstall preserves the data directory. Add `--purge-data` only when
you intend to delete the database permanently.

## Option 2: Docker Compose

Use this when Docker Engine and Compose are already available on the Linux machine.

```bash
cd /path/to/status-visualizer-linux
docker compose up --build -d
```

Open:

```text
http://localhost:8092
```

Compose stores the SQLite database in the `status-visualizer-data` Docker volume.

Useful Docker commands:

```bash
docker compose logs -f
docker compose down
docker compose down -v
```

`docker compose down -v` deletes the Docker volume and the app data inside it.

## Direct development run

Use this only for a quick manual run:

```bash
cd /path/to/status-visualizer-linux
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python run.py --host 0.0.0.0 --port 8092 --data-dir ./data
```

## Tech expo multi-network deployment

For the optional port-6042 launcher with isolated flat, compartmentalized, and live topology instances, first complete the native systemd install using `/srv/StatusVisualizer`, then follow `EXPO-RUNBOOK.md`.
