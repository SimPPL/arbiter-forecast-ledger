# Issue 2026-09-27

Frozen 2026-09-27T21:18:32Z (2026-09-28T02:48:32+05:30 in India) from Arbiter's reading of 2026-09-26, with the model `gbm|K0`.

- 2 days ahead of the origin reading (lag 2): target 2026-09-28, frozen before that day began (UTC).

The lag is the number of days from the reading the forecast starts from to the target day.

`forecast.json` lists the top 10 stories per region for each target. `FREEZE.json` and `FREEZE.json.ots` are copied byte for byte from the research repo, with every forecast file they hash. `MANIFEST.json` holds the sha256 of every file here.

Pilot, two days ahead of the reading it started from, issued before the horizon rule of 3 October 2026 (forecasts hourly, for the next day, and at most two days ahead). Published unchanged. This copy reached the ledger on 2 October and its OpenTimestamps proof was made on 29 September, both after the target day, so the stamp and push checks in tools/verify.py fail for it. The only record that it was made before 28 September is the push to our private research repo at 21:19:29 UTC on 27 September, which an outsider cannot check.

FREEZE.json also hashes these model inputs, which stay private because they are internal Arbiter tables: `data/tomorrow_tournament/vint.parquet`, `data/tomorrow_tournament/nk_meta.parquet`, `cleanroom/2026-09-28-tomorrow-tournament/specs/live_base.json`. The forecast files themselves are all here.

This issue carries no predicted posts. Predicted posts begin with issue 2026-10-03.
