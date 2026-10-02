# Issue 2026-09-29

Frozen 2026-09-29T04:33:58Z (2026-09-29T10:03:58+05:30 in India) from Arbiter's reading of 2026-09-28, with the model `gbm|K0`.

- 2 days ahead of the origin reading: target 2026-09-30, frozen before that day began (UTC).

`forecast.json` lists the top 10 stories per region for each target. `FREEZE.json` and `FREEZE.json.ots` are copied byte for byte from the research repo, with every forecast file they hash. `MANIFEST.json` holds the sha256 of every file here.

Two days ahead of the reading it started from, issued before the rule of one and three days. Published unchanged. This copy reached the ledger on 2 October, after the target day, so the push check in tools/verify.py fails for it. The OpenTimestamps proof, anchored in Bitcoin on 29 September, is the public clock that shows it was made before 30 September.

FREEZE.json also hashes these model inputs, which stay private because they are internal Arbiter tables: `data/tomorrow_tournament/live/vint.parquet`, `data/tomorrow_tournament/live/nk_meta.parquet`, `cleanroom/2026-09-28-tomorrow-tournament/specs/live_base.json`. The forecast files themselves are all here.

This issue carries no predicted posts. Predicted posts begin with issue 2026-10-03.
