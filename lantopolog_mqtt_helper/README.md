# LanTopoLog MQTT service

Build the deploy pack once:

```powershell
.\scripts\build-dist-status-visualizer.ps1
```

Then copy only `dist-status-visualizer\MqttService` to each LanTopoLog PC/VM.

1. Edit `config.json` (`export_folder` + broker host/username).
2. Double-click **INSTALL Service.bat** once (UAC prompt).
3. Export in LanTopoLog; wait ~10 seconds.

To remove it later, double-click **UNINSTALL Service.bat** (keeps identity/config under ProgramData by default).

The service publishes the latest topology as one retained MQTT ZIP at
`statusvisualizer/<client UUID>/snapshot`. It normally probes devices every 15
seconds. A miss triggers two 500 ms verification probes; continued failures use
3-second degraded probes until two consecutive replies arrive. Initial probes
are staggered and no more than 20 run concurrently. Devices without a valid IP
are skipped.

One retained version 2 site-health snapshot is published every 30 seconds at
`statusvisualizer/<client UUID>/status`; individual probes are never published.
Loss is time weighted (`bad seconds / observed seconds`), so faster degraded
probing does not exaggerate loss. History is compacted from 15-second buckets
to 1-minute, 5-minute, and 15-minute buckets, retaining 24 hours. Every window
includes observed duration so partial startup history is not confused with a
fully observed zero-loss window. Current RTT is null after failure while the
last successful RTT and response time are retained.

These monitoring defaults can be overridden in `config.json` with
`status_interval_seconds`, `ping_timeout_seconds`, `ping_concurrency`, and
`recovery_successes`.
The service does not copy export folders to the dashboard machine.

Identity, configuration, and last-published hash live under
`%ProgramData%\StatusVisualizer\MqttHelper` and survive upgrades. Uninstall
keeps that data by default.

Give each service publish-only access to exactly these two topics:

- `statusvisualizer/<client UUID>/snapshot`
- `statusvisualizer/<client UUID>/status`

The UUID is in `client-id` after first start. A Mosquitto-style ACL is:

```text
topic write statusvisualizer/<client UUID>/snapshot
topic write statusvisualizer/<client UUID>/status
```

The dashboard subscriber needs read-only access to
`statusvisualizer/+/snapshot` and `statusvisualizer/+/status`. Broker payloads
must allow at least 1 MiB.
