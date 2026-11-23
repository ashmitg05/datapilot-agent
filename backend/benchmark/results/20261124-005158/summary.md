Run `20261124-005158` · 69 questions × 1 repeats · provider openai · models {'single_shot': 'gemini-3.5-flash-lite', 'loop_no_schema': 'gemini-3.5-flash-lite', 'loop_schema': 'gemini-3.5-flash-lite', 'self_correction': 'gemini-3.5-flash-lite', 'verification': 'gemini-3.5-flash-lite'} · SQL-only

| Config | Success % | Success % (excl. provider errors) | Provider errors % | Correct refusal % | Avg steps | Avg tokens | Cost / q | p50 s | p95 s | Error recovery % |
|---|---|---|---|---|---|---|---|---|---|---|
| single_shot | 60.9 ± 0.0 | 60.9 ± 0.0 | 0.0 ± 0.0 | 0.0 ± 0.0 | 1.0 ± 0.0 | 93 ± 0 | $0.0000 ± 0.0000 | 8.8 ± 0.0 | 32.5 ± 0.0 | 0.0 ± 0.0 |
| loop_no_schema | 37.7 ± 0.0 | 66.7 ± 0.0 | 43.5 ± 0.0 | 12.5 ± 0.0 | 1.6 ± 0.0 | 2084 ± 0 | $0.0000 ± 0.0000 | 16.1 ± 0.0 | 76.4 ± 0.0 | 0.0 ± 0.0 |
| loop_schema | 0.0 ± 0.0 | n/a | 100.0 ± 0.0 | 0.0 ± 0.0 | 0.0 ± 0.0 | 0 ± 0 | $0.0000 ± 0.0000 | 15.6 ± 0.0 | 17.3 ± 0.0 | n/a |
| self_correction | 0.0 ± 0.0 | n/a | 100.0 ± 0.0 | 0.0 ± 0.0 | 0.0 ± 0.0 | 0 ± 0 | $0.0000 ± 0.0000 | 15.8 ± 0.0 | 16.9 ± 0.0 | n/a |
| verification | 0.0 ± 0.0 | n/a | 100.0 ± 0.0 | 0.0 ± 0.0 | 0.0 ± 0.0 | 0 ± 0 | $0.0000 ± 0.0000 | 15.5 ± 0.0 | 17.4 ± 0.0 | n/a |

Success % by difficulty

| Config | easy | hard | medium |
|---|---|---|---|
| single_shot | 76.9 ± 0.0 | 53.8 ± 0.0 | 50.0 ± 0.0 |
| loop_no_schema | 42.3 ± 0.0 | 15.4 ± 0.0 | 43.3 ± 0.0 |
| loop_schema | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.0 ± 0.0 |
| self_correction | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.0 ± 0.0 |
| verification | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.0 ± 0.0 |

Success % by category

| Config | aggregation | ambiguous | filtering | multi-step reasoning | time comparison | unanswerable |
|---|---|---|---|---|---|---|
| single_shot | 94.7 ± 0.0 | 0.0 ± 0.0 | 91.7 ± 0.0 | 63.6 ± 0.0 | 50.0 ± 0.0 | 0.0 ± 0.0 |
| loop_no_schema | 63.2 ± 0.0 | 28.6 ± 0.0 | 25.0 ± 0.0 | 9.1 ± 0.0 | 58.3 ± 0.0 | 12.5 ± 0.0 |
| loop_schema | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.0 ± 0.0 |
| self_correction | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.0 ± 0.0 |
| verification | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.0 ± 0.0 |
