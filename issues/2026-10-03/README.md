# Issue 2026-10-03

Frozen 2026-10-03T07:24:16Z (2026-10-03T12:54:16+05:30 in India) from Arbiter's reading of 2026-10-02, with the model `gbm_med`.

- 1 day ahead of the issue day: target 2026-10-04, frozen before that day began (UTC).
- 3 days ahead of the issue day: target 2026-10-06, frozen before that day began (UTC).

`forecast.json` lists the top 10 stories per region for each target. `FREEZE.json` and `FREEZE.json.ots` are copied byte for byte from the research repo, with every forecast file they hash. `MANIFEST.json` holds the sha256 of every file here.

FREEZE.json also hashes these model inputs, which stay private because they are internal Arbiter tables: `inputs/build_stats.json`, `inputs/nk_meta.parquet`, `inputs/vint.parquet`. The forecast files themselves are all here.
