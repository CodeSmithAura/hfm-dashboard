# SQL Server health query samples

Rows per query in `src/hfm_dashboard/adapters/sql_health.py` (`QUERIES`), as lists of row tuples
(JSON arrays), keyed by query name. `null` for a whole query means it failed (e.g. no permission).

**Synthetic for now**: written before the read-only SQL login existed (A10). Replace with masked
recordings once access is granted. Database names are the EPM databases already in
`docs/environment.md`; volume labels are drive letters only.

| File | Shows |
|---|---|
| `healthy.json` | everything within thresholds |
| `production_disks.json` | healthy, except the disk levels found in production discovery (Q52) |
| `stressed.json` | blocking, deadlocks, full log, old backup, failed Agent job, long query |
| `no_view_server_state.json` | DMV queries failed (permission missing), msdb queries worked |
