# WebLogic REST sample responses

Responses of `POST /management/weblogic/latest/domainRuntime/search` with the query in
`src/hfm_dashboard/adapters/weblogic_rest.py` (`SEARCH_QUERY`).

**Synthetic for now**: written in the WebLogic 12.2.1 REST bulk-search format before access to the
Admin Server existed (A09). Replace with masked recordings once the Monitor user is available.
Server names are the WebLogic defaults already in `config/environments.template.yaml` and
`docs/environment.md`; no host names, addresses or accounts appear here.

| File | Shows |
|---|---|
| `domain_all_running.json` | Admin Server and all managed servers RUNNING, health ok |
| `domain_one_shutdown.json` | FoundationServices1 SHUTDOWN (in lifecycle list, absent from serverRuntimes) |
| `domain_stressed.json` | FoundationServices0 health warning, 2 stuck threads, 5% heap free |
