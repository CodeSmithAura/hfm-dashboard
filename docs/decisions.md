# Decisions

| # | Date | Decision | Why |
|---|---|---|---|
| D1 | 12 Oct | Build a dashboard with stability score and email caution notices; application team approved the approach | Project goal |
| D2 | 21 Oct | No server names, IPs or credentials in documents or code; placeholders everywhere | Security requirement from the user |
| D3 | 22 Oct | Proceed on documented assumptions where the application team does not answer; every rule configurable in rules.yaml | Not all questions will be answered |
| D4 | 20 Oct | Notices by email only; no ticket creation | Application team answer (Q33) |
| D5 | 20 Oct | First notice after 15 min down, 5 min during month-end/quarter-end close | Application team answers (Q31, Q34) |
| D6 | 20 Oct | Multi-node components: Degraded, score reduced by node share | Application team answer (Q27) |
| D7 | 3 Nov | Architecture option A: own service polling servers directly | New Relic covers no EPM service in production; API access delayed. Pending formal sign-off (A12) |
| D8 | 27 Oct - 3 Nov | Monitor Foundation, HFM Web and FR Web as separate components | Discovery: three separate services and clusters |
| D9 | 3 Nov | FR RMI and LCM not monitored | Discovery: FR RMI service does not exist; no scheduled LCM jobs |
| D10 | 3 Nov | Store all times in UTC | Servers run in different time zones |
| D11 | 4 Nov | Use personal Remote Desktop credentials only for temporary development testing, never in production | Audit, lockout and policy risk |
| D12 | 4 Nov | A node whose checks cannot run (Unknown) is left out of its component's node share; a component with no checkable node is left out of the overall score. Configurable as `scoring.unknown_nodes` (`exclude` / `count_as_down`) | A monitoring fault, such as lost WinRM access, should not lower the score or trigger the 0% cap. ASSUMED Q53, awaiting the application team |
| D13 | 4 Nov | Several checks on one node: worst result wins, in the order Down > Degraded > Unknown > Up. While a failure is not yet confirmed the node keeps its previous status; recovery shows straight away | A definite failure tells more than "could not check"; flap protection (Q40) should delay Down, not Up |
| D14 | 4 Nov | Component status: all nodes Up = Up, all nodes Down = Down, anything else = Degraded. A hard dependency caps the overall score only when the whole component is Down. Overall score rounded to 2 decimals before choosing the band | Follows Q27 (node share) and Q28 (OHS caps only when all nodes are down); rounding stops float noise from changing the band |
| D15 | 4 Nov | rules.yaml is checked strictly at start-up: unknown or duplicate keys, out-of-range values and broken references stop the program. `auto_restart: false` and UTC storage are enforced by the check itself | Hard rules 3, 5 and 6 in CLAUDE.md; a typo must not quietly fall back to a default |
| D16 | 4 Nov | Code layout: `src/hfm_dashboard/domain` (no I/O), `config/`, then `ports/`, `adapters/`, `app/`. A test blocks I/O imports in the domain | Ports-and-adapters design (CLAUDE.md); every adapter can be replaced by a fake in tests |
