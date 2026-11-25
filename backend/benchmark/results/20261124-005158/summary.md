Run `20261124-005158` · 69 questions × 1 repeats · provider openai · models {'single_shot': 'gemini-3.5-flash-lite', 'loop_no_schema': 'gemini-3.5-flash-lite', 'loop_schema': 'gemini-3.5-flash-lite', 'self_correction': 'gemini-3.5-flash-lite', 'verification': 'gemini-3.5-flash-lite'} · SQL-only

| Config | Success % | Success % (excl. provider errors) | Provider errors % | Correct refusal % | Avg steps | Avg tokens | Cost / q | p50 s | p95 s | Error recovery % |
|---|---|---|---|---|---|---|---|---|---|---|
| single_shot | 60.9 ± 0.0 | 60.9 ± 0.0 | 0.0 ± 0.0 | 0.0 ± 0.0 | 1.0 ± 0.0 | 93 ± 0 | $0.0000 ± 0.0000 | 8.8 ± 0.0 | 32.5 ± 0.0 | 0.0 ± 0.0 |
| loop_no_schema | 59.4 ± 0.0 | 59.4 ± 0.0 | 0.0 ± 0.0 | 37.5 ± 0.0 | 2.7 ± 0.0 | 3631 ± 0 | $0.0000 ± 0.0000 | 25.9 ± 0.0 | 71.9 ± 0.0 | 0.0 ± 0.0 |
| loop_schema | 81.2 ± 0.0 | 81.2 ± 0.0 | 0.0 ± 0.0 | 25.0 ± 0.0 | 3.4 ± 0.0 | 6647 ± 0 | $0.0000 ± 0.0000 | 42.6 ± 0.0 | 71.6 ± 0.0 | 0.0 ± 0.0 |
| self_correction | 94.2 ± 0.0 | 94.2 ± 0.0 | 0.0 ± 0.0 | 100.0 ± 0.0 | 3.7 ± 0.0 | 7302 ± 0 | $0.0000 ± 0.0000 | 39.1 ± 0.0 | 79.5 ± 0.0 | 100.0 ± 0.0 |
| verification | 89.9 ± 0.0 | 96.9 ± 0.0 | 7.2 ± 0.0 | 62.5 ± 0.0 | 5.4 ± 0.0 | 12308 ± 0 | $0.0000 ± 0.0000 | 69.7 ± 0.0 | 111.8 ± 0.0 | 100.0 ± 0.0 |

Success % by difficulty

| Config | easy | hard | medium |
|---|---|---|---|
| single_shot | 76.9 ± 0.0 | 53.8 ± 0.0 | 50.0 ± 0.0 |
| loop_no_schema | 57.7 ± 0.0 | 38.5 ± 0.0 | 70.0 ± 0.0 |
| loop_schema | 84.6 ± 0.0 | 84.6 ± 0.0 | 76.7 ± 0.0 |
| self_correction | 96.2 ± 0.0 | 92.3 ± 0.0 | 93.3 ± 0.0 |
| verification | 96.2 ± 0.0 | 84.6 ± 0.0 | 86.7 ± 0.0 |

Success % by category

| Config | aggregation | ambiguous | filtering | multi-step reasoning | time comparison | unanswerable |
|---|---|---|---|---|---|---|
| single_shot | 94.7 ± 0.0 | 0.0 ± 0.0 | 91.7 ± 0.0 | 63.6 ± 0.0 | 50.0 ± 0.0 | 0.0 ± 0.0 |
| loop_no_schema | 78.9 ± 0.0 | 57.1 ± 0.0 | 50.0 ± 0.0 | 36.4 ± 0.0 | 75.0 ± 0.0 | 37.5 ± 0.0 |
| loop_schema | 100.0 ± 0.0 | 42.9 ± 0.0 | 100.0 ± 0.0 | 90.9 ± 0.0 | 83.3 ± 0.0 | 25.0 ± 0.0 |
| self_correction | 100.0 ± 0.0 | 57.1 ± 0.0 | 100.0 ± 0.0 | 90.9 ± 0.0 | 100.0 ± 0.0 | 100.0 ± 0.0 |
| verification | 100.0 ± 0.0 | 57.1 ± 0.0 | 100.0 ± 0.0 | 90.9 ± 0.0 | 100.0 ± 0.0 | 62.5 ± 0.0 |
