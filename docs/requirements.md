# Requirements (approved approach, refined by application-team answers and discovery)

## Goal
One view of HFM health that shows every component's availability, one stability score for the
application, the components needing attention, and caution notices to owners until restored.

## Functional requirements
1. **Checks** every 60 s (rules.yaml `collection.poll_interval_seconds`):
   - Windows service state of every monitored service on every node (WinRM).
   - Disk free space per volume; System event log service stops and crashes.
   - URL probes through the load balancer and per node: Workspace, HFM Web, FR Web (paths in
     environments.template.yaml).
   - Login probe with a test user: Workspace login -> open an HFM application -> run a small FR report.
   - WebLogic server state, stuck threads, heap use (WebLogic REST management API).
   - SQL Server: service, blocking, deadlocks, log/tempdb/disk space, failed backups and Agent jobs,
     long-running queries.
2. **Status model**: Up / Degraded / Down / Unknown per node and per component (rules.yaml `checks`).
3. **Stability score**: weighted by criticality, node-share for multi-node components, capped at 0%
   when a hard dependency (SQL Server, all Foundation nodes, all OHS nodes) is Down. Bands
   Green >= 95, Amber >= 70, Red below.
4. **Dashboard**: overall score and band; component x node grid; attention list (worst first, with
   time down); per-environment view (production, development); maintenance indicator. History
   (24 h / 7 d / 30 d availability) is a nice-to-have, behind `history_views_enabled`.
5. **Caution notices (email only, no tickets)**: first notice after 15 min continuous Down (5 min in
   the month-end/quarter-end close calendar); repeat every 30 min; escalate to manager at 60 min;
   recovery notice; downstream notices grouped under the root cause; include restart guidance and
   start order. Degraded shows on the dashboard only. Non-production: dashboard only, no notices.
6. **Maintenance windows** entered on the dashboard suppress notices for listed components.
7. **Configuration**: all behaviour in `config/rules.yaml`; all environment values in
   `environments.yaml` (from the template). Changes need a restart, not a code change.

## Non-functional requirements
- Read-only towards HFM; least-privilege accounts; secrets from a secret store.
- No identifiers in the repository (see CLAUDE.md hard rules).
- Runs as a Windows service on a dedicated monitoring server; survives restarts; keeps 90 days of history.
- Dashboard behind HTTPS with Windows/AD login: viewer and admin groups.
- All timestamps in UTC; display in the viewer's local time.

## Out of scope
Automatic restarts, ticket creation, New Relic integration, FDMEE / Calculation Manager / Essbase,
FR RMI (does not exist), LCM (no scheduled jobs).
