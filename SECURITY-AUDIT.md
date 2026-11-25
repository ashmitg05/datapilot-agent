# Security audit — DataPilot (`c7c3b77`, quick profile)

Scope: sandbox (local Docker + Cloud Run service), SQL guard, API (rate limit, uploads,
traces), secrets, CI, `DEPLOY.md`. Source review only; no deployed endpoint was probed.
Quick profile = partial coverage. No prior audit ledger.

## Confirmed

None at medium or above. Local Docker sandbox security tests (26) and SQL guard tests pass
and cover network, filesystem, fork bomb, memory, cross-job leak and SQL file/extension access.

## Needs validation (deployment facts not in source; no severity)

### NV-1 Cloud Run sandbox: metadata server and DNS still reachable — FIXED in source
`run_job` now puts each job in an empty netns (`unshare(CLONE_NEWNET)`), failing closed.
Tests: `test_server_job_has_no_network`, `test_server_fails_closed_without_isolation`.
Still to validate: that Cloud Run gen2 allows `unshare` for root (see DEPLOY.md check).

`DEPLOY.md` removes internet egress with a no-NAT VPC. The Cloud Run metadata server
(`169.254.169.254`) is not routed through the VPC, so job code can likely:
- fetch an ID/access token for `datapilot-sandbox` SA (no roles, so low value), and
- resolve arbitrary hostnames through the platform resolver, a DNS exfil channel for
  uploaded data.
Validate: deploy to a scratch project, run `socket.getaddrinfo("<random>.<your-domain>", 80)`
in a job and check your authoritative DNS logs; `curl -H Metadata-Flavor:Google
http://169.254.169.254/computeMetadata/v1/` from a job.
Fix if confirmed: in `sandbox/server.py` `_limits()`, drop network for the child with
`unshare(CLONE_NEWNET)` (gVisor supports it) or add a seccomp filter denying `socket()`
except `AF_UNIX`; add a test that `getaddrinfo` fails.

### NV-2 Cloud Run sandbox: job runs as the same uid as `server.py` — FIXED in source
Server starts as root, sets `PR_SET_DUMPABLE=0`, and drops each job to uid 1000 with
`NO_NEW_PRIVS`; `scrub()` kills only uid 1000. Test: `test_server_job_cannot_touch_server`.

Child jobs share the server's uid. A job can signal or (if `ptrace` is allowed under gVisor)
attach to the server process and survive `scrub()`, reading later jobs' `files`.
Concurrency 1 limits this to the same instance, which is reused across users.
Validate: in a scratch deploy, a job that runs `os.kill(os.getppid(), 0)` and attempts
`ptrace(PTRACE_ATTACH, ppid)`.
Fix: run the child as a second uid (`preexec_fn` → `os.setgid/os.setuid(1001)` with the
server started as root only long enough to drop), or `prctl(PR_SET_DUMPABLE, 0)` in the server
plus `PR_SET_NO_NEW_PRIVS` in the child.

### NV-3 Per-instance, per-IP rate limit
`RateLimiter` is in memory and keyed on one IP. With `--max-instances 2` and IPv6 address
rotation, one actor can exceed `DAILY_QUESTION_LIMIT` and spend LLM budget. `COST_CAP_USD`
bounds each question, not the total. Already noted with a `ponytail:` comment.
Validate: provider billing alert. Fix: global daily spend counter in Firestore/Redis,
key IPv6 on `/64`.

## Rejected (checked, no boundary crossed)

- SQL guard bypass: even an unparsed function is blocked by DuckDB `read_only`,
  `enable_external_access=false`, `lock_configuration=true`.
- `/traces/{id}` IDOR: ids are `uuid4().hex` (122 bits), not enumerable.
- Upload path traversal: files saved as `<uuid>/data.csv`; dataset ids match `^[a-z0-9_-]{1,64}$`.
- Upload disk-fill: uploads disabled in the documented deploy (`SAMPLES_ONLY=true`).
  Without it, there is no rate limit on `POST /datasets`; keep `SAMPLES_ONLY=true` public.
- Secrets: no keys, `.env` or data CSVs tracked (`git grep` for Gemini/OpenRouter/Anthropic
  key patterns clean); deploy uses Secret Manager.
- CI: `permissions: contents: read`, no `pull_request_target`, no secrets used.
- Frontend: no `dangerouslySetInnerHTML`/`innerHTML`.
- Sandbox HTTP auth: `--no-allow-unauthenticated --ingress internal`, ID token from API SA.

## Residual gaps

Not covered: dependency CVE scan (no network fetch allowed), Next.js build config, LLM prompt
injection from uploaded CSV cell text into tool calls (bounded by the same SQL/sandbox guards).
