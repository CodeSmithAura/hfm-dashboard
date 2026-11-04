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
