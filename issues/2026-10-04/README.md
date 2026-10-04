# Issue 2026-10-04

Frozen 2026-10-04T08:24:43Z (2026-10-04T13:54:43+05:30 in India) from Arbiter's reading of 2026-10-03, with the model `gbm_med`.

- D+1 (lag 2): target 2026-10-05, frozen before that day began (UTC).
- D+2 (lag 3): target 2026-10-06, frozen before that day began (UTC).

D+n is the number of days from the issue day to the target day. The lag is the number of days from the reading the forecast starts from to the target day, so D+1 (lag 2) is the second day after that reading.

`forecast.json` lists the top 10 stories per region for each target. `FREEZE.json` and `FREEZE.json.ots` are copied byte for byte from the research repo, with every forecast file they hash. `MANIFEST.json` holds the sha256 of every file here.

FREEZE.json also hashes these model inputs, which stay private because they are internal Arbiter tables: `inputs/build_stats.json`, `inputs/nk_meta.parquet`, `inputs/vint.parquet`. The forecast files themselves are all here.
