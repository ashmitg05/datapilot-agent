Run `20261124-002455` · 10 questions × 1 repeats · provider openai · models {'single_shot': 'gemini-3.5-flash-lite', 'loop_no_schema': 'gemini-3.5-flash-lite', 'loop_schema': 'gemini-3.5-flash-lite', 'self_correction': 'gemini-3.5-flash-lite', 'verification': 'gemini-3.5-flash-lite'} · SQL-only

| Config | Success % | Success % (excl. provider errors) | Provider errors % | Correct refusal % | Avg steps | Avg tokens | Cost / q | p50 s | p95 s | Error recovery % |
|---|---|---|---|---|---|---|---|---|---|---|
| single_shot | 90.0 ± 0.0 | 90.0 ± 0.0 | 0.0 ± 0.0 | n/a | 1.0 ± 0.0 | 71 ± 0 | $0.0000 ± 0.0000 | 6.5 ± 0.0 | 79.8 ± 0.0 | 0.0 ± 0.0 |
| loop_no_schema | 60.0 ± 0.0 | 60.0 ± 0.0 | 0.0 ± 0.0 | n/a | 2.5 ± 0.0 | 2976 ± 0 | $0.0000 ± 0.0000 | 5.7 ± 0.0 | 65.5 ± 0.0 | 0.0 ± 0.0 |
| loop_schema | 100.0 ± 0.0 | 100.0 ± 0.0 | 0.0 ± 0.0 | n/a | 3.3 ± 0.0 | 5580 ± 0 | $0.0000 ± 0.0000 | 23.0 ± 0.0 | 101.8 ± 0.0 | n/a |
| self_correction | 100.0 ± 0.0 | 100.0 ± 0.0 | 0.0 ± 0.0 | n/a | 3.4 ± 0.0 | 5796 ± 0 | $0.0000 ± 0.0000 | 25.0 ± 0.0 | 255.2 ± 0.0 | n/a |
| verification | 80.0 ± 0.0 | 100.0 ± 0.0 | 20.0 ± 0.0 | n/a | 4.3 ± 0.0 | 8204 ± 0 | $0.0000 ± 0.0000 | 41.4 ± 0.0 | 301.5 ± 0.0 | n/a |

Success % by difficulty

| Config | easy | medium |
|---|---|---|
| single_shot | 100.0 ± 0.0 | 80.0 ± 0.0 |
| loop_no_schema | 60.0 ± 0.0 | 60.0 ± 0.0 |
| loop_schema | 100.0 ± 0.0 | 100.0 ± 0.0 |
| self_correction | 100.0 ± 0.0 | 100.0 ± 0.0 |
| verification | 100.0 ± 0.0 | 60.0 ± 0.0 |

Success % by category

| Config | aggregation | filtering | time comparison |
|---|---|---|---|
| single_shot | 83.3 ± 0.0 | 100.0 ± 0.0 | 100.0 ± 0.0 |
| loop_no_schema | 66.7 ± 0.0 | 50.0 ± 0.0 | 50.0 ± 0.0 |
| loop_schema | 100.0 ± 0.0 | 100.0 ± 0.0 | 100.0 ± 0.0 |
| self_correction | 100.0 ± 0.0 | 100.0 ± 0.0 | 100.0 ± 0.0 |
| verification | 83.3 ± 0.0 | 100.0 ± 0.0 | 50.0 ± 0.0 |
