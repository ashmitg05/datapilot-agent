Run `20261124-005158` · 69 questions × 1 repeats · provider openai · models {'single_shot': 'gemini-3.5-flash-lite', 'loop_no_schema': 'gemini-3.5-flash-lite', 'loop_schema': 'gemini-3.5-flash-lite', 'self_correction': 'gemini-3.5-flash-lite', 'verification': 'gemini-3.5-flash-lite'} · SQL-only

| Config | Success % | Success % (excl. provider errors) | Provider errors % | Correct refusal % | Avg steps | Avg tokens | Cost / q | p50 s | p95 s | Error recovery % |
|---|---|---|---|---|---|---|---|---|---|---|
| single_shot | 60.9 ± 0.0 | 60.9 ± 0.0 | 0.0 ± 0.0 | 0.0 ± 0.0 | 1.0 ± 0.0 | 93 ± 0 | $0.0000 ± 0.0000 | 8.8 ± 0.0 | 32.5 ± 0.0 | 0.0 ± 0.0 |
| loop_no_schema | 47.8 ± 0.0 | 64.7 ± 0.0 | 26.1 ± 0.0 | 37.5 ± 0.0 | 2.1 ± 0.0 | 2736 ± 0 | $0.0000 ± 0.0000 | 17.5 ± 0.0 | 71.9 ± 0.0 | 0.0 ± 0.0 |
| loop_schema | 79.7 ± 0.0 | 80.9 ± 0.0 | 1.4 ± 0.0 | 12.5 ± 0.0 | 3.4 ± 0.0 | 6572 ± 0 | $0.0000 ± 0.0000 | 42.6 ± 0.0 | 74.5 ± 0.0 | 0.0 ± 0.0 |
| self_correction | 82.6 ± 0.0 | 95.0 ± 0.0 | 13.0 ± 0.0 | 62.5 ± 0.0 | 3.1 ± 0.0 | 6197 ± 0 | $0.0000 ± 0.0000 | 38.7 ± 0.0 | 77.2 ± 0.0 | 100.0 ± 0.0 |
| verification | 0.0 ± 0.0 | n/a | 100.0 ± 0.0 | 0.0 ± 0.0 | 0.0 ± 0.0 | 0 ± 0 | $0.0000 ± 0.0000 | 15.8 ± 0.0 | 19.4 ± 0.0 | n/a |

Success % by difficulty

| Config | easy | hard | medium |
|---|---|---|---|
| single_shot | 76.9 ± 0.0 | 53.8 ± 0.0 | 50.0 ± 0.0 |
| loop_no_schema | 57.7 ± 0.0 | 15.4 ± 0.0 | 53.3 ± 0.0 |
| loop_schema | 84.6 ± 0.0 | 76.9 ± 0.0 | 76.7 ± 0.0 |
| self_correction | 92.3 ± 0.0 | 84.6 ± 0.0 | 73.3 ± 0.0 |
| verification | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.0 ± 0.0 |

Success % by category

| Config | aggregation | ambiguous | filtering | multi-step reasoning | time comparison | unanswerable |
|---|---|---|---|---|---|---|
| single_shot | 94.7 ± 0.0 | 0.0 ± 0.0 | 91.7 ± 0.0 | 63.6 ± 0.0 | 50.0 ± 0.0 | 0.0 ± 0.0 |
| loop_no_schema | 73.7 ± 0.0 | 42.9 ± 0.0 | 41.7 ± 0.0 | 9.1 ± 0.0 | 58.3 ± 0.0 | 37.5 ± 0.0 |
| loop_schema | 100.0 ± 0.0 | 42.9 ± 0.0 | 100.0 ± 0.0 | 90.9 ± 0.0 | 83.3 ± 0.0 | 12.5 ± 0.0 |
| self_correction | 100.0 ± 0.0 | 42.9 ± 0.0 | 91.7 ± 0.0 | 81.8 ± 0.0 | 83.3 ± 0.0 | 62.5 ± 0.0 |
| verification | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.0 ± 0.0 |
