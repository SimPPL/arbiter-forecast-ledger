# Outcome 2026-09-30

`stories.json` puts each forecast story beside the number of posts Arbiter's reading of the day assigned to it, and lists the day's real top 10 per region. `score.json` scores every model against the references, with the GitHub push time of the issue that forecast this day. `MANIFEST.json` holds the sha256 of every file here, and `sources` in it names the research files the numbers came from, with their sha256.

Arbiter renamed every story on 30 September, so no forecast story kept its key. Each forecast story was followed to the 30 September story holding at least half of its 28 September posts, a rule fixed before any score was computed. Stories with no such match are excluded and counted. Every number here says "bridged" and gives the unmatched share beside it.

posts.json is absent. Real posts by named accounts are published from the outcome of issue 2026-10-03 on, beside the predicted posts they are scored against.
