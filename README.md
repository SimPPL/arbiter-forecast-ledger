# Arbiter forecast ledger

Every morning we forecast how many posts each story Arbiter already tracks on X will get over the next one to two days, in the United States and in India. Most of a day's posts go to stories that do not exist yet when we forecast, and the forecast does not cover those. This repository publishes each forecast before the day it predicts and adds what actually happened afterwards, so anyone can see how often we were right. You do not have to take our word for the timing either. Each forecast has three clocks we cannot set ourselves, and this README and `VERIFY.md` show how to read them.

The stories come from Arbiter, SimPPL's social listening platform, which groups posts on X into stories, which it calls narratives, each with a key such as `IN:f1bb9ee3c01de292`. We publish the forecasts in the open because a forecast scored only by the people who made it is hard to trust.

## What we predict

For each story that had at least one post in the seven days before the forecast, we predict how many posts Arbiter's reading of the target day will assign to it for that day. Each forecast gives a median count and an 80 percent range, and `forecast.json` lists the ten stories per country we expect to be busiest. The model is boosted trees, refitted every morning on each story's recent daily counts (`gbm_med`; the earliest two issues used an earlier version, `gbm`).

## From what data

We use Arbiter's own daily readings and nothing else. Arbiter publishes the reading of a day at about 09:30 IST the next morning, and each forecast starts from the newest reading it has published. The model sees how many posts each story held on each earlier day, as those readings stood on the forecast morning. Those internal tables stay private, but `FREEZE.json` lists their hashes, so we cannot swap them later without the change showing.

## When the forecast is frozen

`issues/<day>/` holds one forecast, and the folder's date is the UTC date we froze it. `FREEZE.json` holds the sha256 of every forecast file, the commit of the code that made them and the time of the freeze. `FREEZE.json.ots` is an OpenTimestamps proof that `FREEZE.json` existed by a Bitcoin block time, and `MANIFEST.json` holds the sha256 of every file in the folder. We push each issue here before 00:00 UTC on its first target day.

## How we score it

`outcomes/<day>/` holds what happened on a target day. `TABLE.md` puts our top ten next to the real post count and the real rank of each story, with the real top ten underneath. `stories.json` and `score.json` hold the same numbers for machines, and `ranks.json` holds the real ranks. Our main score is the log error, the mean of |log(1 + real posts) minus log(1 + forecast)| over every story the forecast covered, where lower is better. On this scale being off by a factor of two counts the same for a small story and a big one. We also count how many of each country's ten busiest real stories our top ten found.

## The baselines

Two simple forecasts run beside ours every day. Yesterday's count says each story gets as many posts as it had in the latest reading. Every story dies says no story gets any post at all, and it is hard to beat, because on a typical day most stories in a reading get no post the next day. In our view a forecast that cannot beat both is not worth much, so `score.json` always reports all three.

## The score so far

We have scored two target days so far. Both forecasts were frozen two days ahead, before the horizon rules of 1 and 3 October existed.

| target day | how scored | boosted trees | yesterday's count | every story dies | table |
|---|---|---|---|---|---|
| 28 Sep 2026 | by story key | 0.230 | 0.282 | 0.215 | [`outcomes/2026-09-28/TABLE.md`](outcomes/2026-09-28/TABLE.md) |
| 30 Sep 2026 | bridged across Arbiter's rename | 1.181 (35.8 percent of predicted posts unmatched) | 1.660 (13.3 percent unmatched) | 1.687 (79.3 percent of stories unmatched) | [`outcomes/2026-09-30/TABLE.md`](outcomes/2026-09-30/TABLE.md) |

On 28 September every story dies had the lowest log error, which we think says more about how many stories die than about our model. Boosted trees found one of each country's ten busiest stories while yesterday's count found none. Arbiter renamed every story on 30 September, so no forecast story kept its key. We followed each one to the story of that day holding at least half of its earlier posts, a rule we fixed before computing any score. Every number from that day says "bridged" and gives the share we could not match. The bridged score covers the stories that kept going, which is a selected slice of the day and is not comparable with 28 September.

Issues 2026-10-02 and 2026-10-03 are frozen and waiting for their target days (3 to 6 October).

## Horizons from 3 October 2026

Swapneel set the horizons on 3 October 2026: forecasts are hourly, for the next day (D+1), and at most two days ahead (D+2). The three-day horizon is retired. The three-day targets frozen before that change, 5 and 6 October, are each scored once, and then that horizon ends. Issues 2026-10-02 and 2026-10-03 each hold a three-day target, published unchanged because changing them would break their hashes. From the next issue on, the ledger holds D+1 and D+2 forecasts.

## What it cannot tell you

The forecast covers stories that already exist. On a typical day most posts go to stories that did not exist the morning before, and no forecast here lists them; `TABLE.md` marks those stories in the real top ten as new. Arbiter re-groups posts every day, so a story key can end while the conversation goes on under another key. Two scored days are a description and test nothing. And the ledger shows story volumes only. What we expect accounts to post is a separate benchmark, frozen in a private repository because it covers private accounts. From issue 2026-10-04 on, this ledger receives each of its predicted points and account rows as unit commits, with private accounts as salted hashes, and each row's score once the issue is scored.

## How to check the timing

1. Re-hash every file in a folder against `MANIFEST.json`, and every forecast file against `FREEZE.json`. If one byte changed after the freeze, the hash will not match.
2. Run `ots verify FREEZE.json.ots`, which shows the Bitcoin block that holds the hash of `FREEZE.json`. Nobody can backdate a Bitcoin block.
3. Check when GitHub received the commit. A commit date is whatever the author's clock said, so it proves nothing. GitHub records the time it received each push, and its public API shows it. The push that added an issue folder must come before 00:00 UTC on the target day. Once real posts are published beside predicted ones, those posts give a fourth clock, because an X post id encodes the time the post was created.

`tools/verify.py` runs these checks and prints one line per check. `VERIFY.md` explains each one and how to run it by hand.

```
python3 tools/verify.py
```

## Rules

- Horizons are hourly, one day and at most two days ahead, counted from the issue day (from 3 October 2026). Issues 2026-10-02 and 2026-10-03 also hold a three-day target each, scored once. The two earliest issues, frozen on 27 and 29 September 2026, forecast two days ahead. We made them before this rule and publish them unchanged, because changing them would break their hashes.
- Every forecast starts from the newest reading Arbiter has published.
- Any account this ledger shows by handle is a public account: a public figure, an organisation, a media outlet, or an account with a post seen at least 250,000 times. Never a private account and never a minor. We cite each real post by its id and link, so anyone can open it on X.
- Days are UTC.

## What the timing records show today

The first issues are copies of forecasts frozen in our private research repository, with identical hashes and stamps.

- Issue 2026-09-27, the pilot, forecast 28 September. Its OpenTimestamps proof dates from 29 September, after the target day, and it reached this repository on 2 October. Its timing rests on our private repository's push record, which an outsider cannot check.
- Issue 2026-09-29 forecast 30 September. We made its stamp at 04:34 UTC on 29 September, before the target day, and Bitcoin anchors it.
- Issue 2026-10-02 forecast 3 October (one day ahead) and 5 October (three days ahead), stamped at 07:02 UTC on 2 October.
- Issue 2026-10-03 forecast 4 October and 6 October.

## One commit per story, category, predicted post and account

From issue 2026-10-04 on, every forecast in this ledger is its own commit, and so is every score. The frozen parquet still holds the whole forecast and is still what we score. On top of it we commit small files, one per unit, so you can open the history of one story or one category and read each forecast and its score as a separate commit. We think a reader should be able to follow one story from the morning we forecast it to the morning we score it without opening a parquet file. We also want each of those steps to have its own commit, with its own place in the history, next to the two thousand other stories of the same issue.

A unit is one of these:

- a story (narrative): `issues/<day>/narratives/<country>/<category>/<key>.json` gives the story's forecast for D+1 and D+2, the 80 percent range, the chance of any post, and the baselines' forecasts beside it. Each issue gets a file for the 50 busiest US stories and the 20 busiest India stories by the D+1 forecast, every story in the published top 10, and every story the account benchmark covers.
- a category: `issues/<day>/categories/<country>/<category>.json` gives the forecast posts in one Arbiter category, its share of the country's forecast posts, and the list of stories in it.
- a predicted post: `issues/<day>/posts/<country>/<key>/<option>.json` gives one point we expect people to make in a story, the model that wrote it and its chance. When the point repeats a real post from the day before, we publish a paraphrase and keep the post itself private.
- an account: `issues/<day>/accounts/<country>/<key>/<account>.json` gives the chance that one account posts in the story, from each of the three account models. A public account appears by handle. Every other account appears as a hash of its handle and a salt we draw for each issue and keep in our private repository. That lets us prove later which account a row meant, while you check every score without learning who the person is.
- an hour: `hourly/<day>/<HH>/<country>/<key>.json`, once the hourly benchmark is registered. An hourly issue that reaches GitHub after its hour begins is void and stays out of every score.

The commits come in a fixed order: categories, then stories by forecast rank (US first, then India), then predicted posts, then accounts. Each message reads like a line of a scoreboard, for example `predict 2026-10-04 US sports "NHL season opener sparks team and player reactions": D+1 22 posts [0.0, 298], any post 0.63; D+2 ...`. The last commit of an issue writes `UNITS.json`, which lists every unit file with its sha256 and the commit that added it, and `UNITS.json.ots`, its OpenTimestamps proof. One Bitcoin stamp of that file shows that every unit existed by the block time, and GitHub's record of the push that brought the issue is the second clock.

Scoring works the same way under `outcomes/<day>/`. Each story and each category gets one commit with the forecast beside the real count, the real rank, the log error and the Brier score on any post, and `OUTCOMES-UNITS.json` with its stamp comes last. When Arbiter renames a story, its file holds the bridge record that says which story of the target day we followed it to and why. Units of the 3 October target are the first scored this way.

Every unit holds `derivedFrom`, the frozen file it came from, that file's sha256 and the row. `tools/verify.py` rebuilds every story and category unit from the frozen parquet and fails on any difference. Before anything is committed, `tools/commit_units.sh` scans every unit and every commit message for the handle of every private account in the account benchmark, and it refuses on a single match.

Three views grow by a row every day, and we regenerate them in the same commit as each index and never edit them by hand. `by-narrative/<country>/<key>.md` lists each issue that forecast a story, with links to the forecast commit and the score commit. `by-category/<country>/<category>.md` does the same for a category, with the running error against yesterday's count and against every story dies. `SCOREBOARD.md` holds the benchmark so far, every number beside its baseline.

Issues before 2026-10-04 were published as single commits, one per issue, and outcomes before the 3 October target as one commit per day. We leave them as they are, because rewriting them would change their history.

## Daily procedure

`tools/ledger-publish.sh <issueDay>` exports a new frozen issue, commits it, runs `tools/commit_units.sh <issueDay>` for the unit commits, and prints the push command. `tools/ledger-score.sh <day>` exports the outcome of a scored day, writes its `TABLE.md` with `tools/outcome_table.py`, commits it, runs `tools/commit_units.sh --outcome <day>`, and prints the push command; neither script pushes by itself. `tools/commit_units.sh <day> --dry-run` rehearses the unit commits in a scratch clone and leaves this one untouched. A target day forecast by two issues, D+1 of one and D+2 of the issue before, gets one entry per issue in `score.json`, and the two are never averaged. The tests of the tools run with `<python with pandas> -m unittest discover -s tests`.
