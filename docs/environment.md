# Environment (from masked discovery, 27 Oct - 3 Nov 2026)

No real names, IPs or ports here. Placeholders map to `config/environments.template.yaml`.

## Production (6 servers, Windows Server 2022, EPM 11.2.25)
| Node | Runs | Windows services |
|---|---|---|
| {{WEB_NODE_01}} | OHS, OHS Node Manager, WebLogic Admin Server, Foundation, HFM Web, FR Web | OHS; Oracle Weblogic ohs NodeManager (...); Oracle Weblogic EPMSystem_AdminServer; HyS9FoundationServices_EPMWEB1; HyS9FinancialManagementWeb_EPMWEB1; HyS9FRReports_EPMWEB1 |
| {{WEB_NODE_02}} | Same, without Admin Server | same names with suffix EPMWEB2 |
| {{HFM_NODE_01..03}} | HFM Java Server (one cluster; all apps on every node) | HyS9FinancialManagementJavaServer_EPMHFM1 / 2 / 3 |
| {{DB_NODE_01}} | SQL Server 2022 Enterprise, standalone default instance | MSSQLSERVER; SQLSERVERAGENT |

## Web tier
- Foundation, HFM Web and FR Web are separate WebLogic clusters, one managed server per web node
  (FoundationServices0/1, HFMWeb0/1, FinancialReporting0/1). Active-active.
- Each OHS routes /workspace and /interop (Foundation), /hfmadf (HFM Web), /hr and /frdesigner
  (FR Web) to both web nodes. Exception: /oracle-epm-fm-webservices goes to web node 2 only (Q50).
- WebLogic stuck-thread max time 3600 s on all servers. Node Manager CrashRecoveryEnabled=false.

## Database
- EPM databases: HSS (Shared Services registry), HFM (application), EPMRCU (WebLogic/OPSS).
- No Always On, no failover cluster: single point of failure.

## Behaviour observed
- All EPM services are Manual start; nothing restarts automatically.
- Restart order (26 Oct full restart): SQL Server -> Admin Server -> Foundation -> OHS -> FR Web ->
  HFM Web -> HFM Java Servers. Each instance has its own bin\start.bat / stop.bat.
- About 80 days without unplanned outages; maintenance 8 Oct (~16:20-18:50 UTC, two reboot cycles)
  and full restart 26 Oct (~09:16-09:26 UTC); a 12-second single-node HFM restart on 30 Oct.
- HFM nodes 2 and 3 log in a different time zone from the other servers.

## Development (non-prod)
One web node, one HFM node, one SQL Server node; same software; service names without numeric
suffix (e.g. HyS9FoundationServices_EPMWEB, HyS9FinancialManagementJavaServer_EPMHFM).

## New Relic
Agent installed on all servers but watches no EPM service in production. Not used.
