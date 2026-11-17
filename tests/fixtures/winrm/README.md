# WinRM service-query sample responses

JSON exactly as returned by the `Get-Service` script in
`src/hfm_dashboard/adapters/winrm_services.py` (`ConvertTo-Json -Compress`).

**Synthetic for now**: written in the script's output format before monitoring access existed
(A06-A08). Replace with masked recordings from the development servers once access is granted:
run `hfm-check-services --env NONPROD --record <dir>` (see the README), review the files, then copy
them here and point the parse tests at them.
Service names are the ones already in `config/environments.template.yaml`; no host names,
addresses or accounts appear in these files.

| File | Shows |
|---|---|
| `web_node_all_running.json` | every web-node service Running |
| `web_node_mixed.json` | Stopped, StartPending and NotFound results |
| `single_object.json` | one result serialised as an object instead of a one-element array |
| `not_json.txt` | PowerShell error text instead of JSON |

## Disk space and event log (host query)

JSON as returned by the host script in `src/hfm_dashboard/adapters/winrm_host_health.py`.
Synthetic for now, like the files above.

| File | Shows |
|---|---|
| `host_healthy.json` | volumes well above thresholds, no events |
| `host_problems.json` | production DB-node disk levels (C: 8.6%, D: 11.6%, Q52), 2 Agent crash events, 1 unexpected restart |
| `host_single_items.json` | one volume and one service serialised as objects instead of arrays |
