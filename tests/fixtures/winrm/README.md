# WinRM service-query sample responses

JSON exactly as returned by the `Get-Service` script in
`src/hfm_dashboard/adapters/winrm_services.py` (`ConvertTo-Json -Compress`).

**Synthetic for now**: written in the script's output format before monitoring access existed
(A06-A08). Replace with masked recordings from the development servers once access is granted.
Service names are the ones already in `config/environments.template.yaml`; no host names,
addresses or accounts appear in these files.

| File | Shows |
|---|---|
| `web_node_all_running.json` | every web-node service Running |
| `web_node_mixed.json` | Stopped, StartPending and NotFound results |
| `single_object.json` | one result serialised as an object instead of a one-element array |
| `not_json.txt` | PowerShell error text instead of JSON |
