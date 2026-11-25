# Security audit — DataPilot

## Full audit (security-audit skill, standard profile, source ref `740c203`)

Done with the full six-phase workflow:
- 4 reconnaissance agents.
- 17 hunters over 4 waves, with 5 coverage critics (the final clean pass found nothing missing).
- 7 independent candidate verifiers and 7 final-record verifiers, 40 agents in total.
- 22 coverage units, with both skill validators passing.

All checks were source-only: no target code was run during the audit.

**Result:** no confirmed vulnerabilities. Seven leads needed runtime or deployment facts to settle. All seven are now fixed in source, each with a regression test:

| # | Lead | Fix | Test |
|---|---|---|---|
| 1 | Sandbox `scrub()` ran its kill helper as the job uid, so a detached job could kill it and survive into the next job | The root server scans `/proc` and kills every uid-1000 process itself, repeating until none are left, and fails closed if any survive. It also empties `/dev/shm` and `/var/tmp`. | `test_server_scrub_survives_job_that_kills_cleaner` |
| 2 | The daily budget was checked at admission but charged only after a successful run | Each question reserves `COST_CAP_USD` at admission, and the reservation is always settled in `finally`. `run_agent` now catches provider errors and keeps their cost. | `test_budget_reserved_at_admission`, `test_provider_error_keeps_cost` |
| 2b | A `final_answer` over 4000 chars was truncated into invalid JSON and crashed the run | The answer is taken from the validated tool args, not the truncated output. | `test_long_final_answer_is_not_truncated_into_bad_json` |
| 3 | `max_steps` was checked per LLM round, not per tool call | The step budget is checked before every tool call. | `test_max_steps_counts_each_parallel_call` |
| 4 | Job stdout was buffered with no limit in the API process (docker mode) and the sandbox server | Both pipes are read with a 1 MB cap, and the job is killed past it. | `test_docker_output_cap`, `test_server_output_cap` |
| 5 | Anonymous uploads had no rate limit or total disk cap | `DAILY_UPLOAD_LIMIT` per client and `MAX_UPLOAD_DIR_MB` total, returning 429 and 507. | `test_upload_quota_and_storage_cap` |
| 6 | `GET /datasets/samples` re-ingested about 55 MB of CSV per request | Computed once per process (`functools.cache`). | `test_samples_computed_once` |
| 7 | Traces kept upload-derived rows forever, despite the 24 h deletion promise | Traces for `upload-*` datasets are purged after 24 h at startup and hourly. | `test_upload_traces_expire` |

**Still to verify on a real deployment:**
- Cloud Run gen2 must allow `unshare(CLONE_NEWNET)`. If it doesn't, jobs fail closed; see DEPLOY.md.
- The per-instance in-memory counters require `--max-instances 1`.
- The budget works only when `LLM_PRICE_*` is set.

**Checked and clean:**
- SQL guard (parser plus locked read-only DuckDB)
- secrets (none in git, images or job env)
- CI (read-only token, no secrets)
- frontend (no HTML sinks, no credentialed CORS)
- capability ids (uuid4)
- the sandbox ID-token fallback (Cloud Run IAM still enforces)
- config across deploy modes

**Not covered:** a dependency CVE scan (no offline database), and the live behaviour of any deployment.
