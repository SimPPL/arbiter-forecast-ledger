# How to verify a forecast

Each forecast in `issues/<day>/` claims it was made before the day it predicts. You can check that claim with three tests. None of them asks you to trust us, and a fourth applies once predicted posts are published. A fifth, from issue 2026-10-04 on, checks that every story, category, predicted post and account is its own commit and rebuilds from the frozen forecast.

## The quick way

```
git clone https://github.com/SimPPL/arbiter-forecast-ledger
cd arbiter-forecast-ledger
pip install opentimestamps-client   # only needed for the stamp check
python3 tools/verify.py
```

The script prints one line per check, PASS or FAIL, and a total at the end. It uses only the Python standard library, plus the `opentimestamps` package for the stamp check. `python3 tools/verify.py 2026-10-02` checks one issue.

## 1. The files have not changed

`MANIFEST.json` lists the sha256 hash of every file in the folder, and `FREEZE.json` lists the hash of every forecast file at the moment of the freeze. Change one byte of a forecast and its hash changes.

By hand: `shasum -a 256 issues/2026-10-02/*` and compare the output with both files. `FREEZE.json` also lists a few model inputs that we do not publish because they are internal Arbiter tables. Their hashes are there, so we cannot swap them later without the change showing.

## 2. The hash of FREEZE.json was in the Bitcoin blockchain before the target day

`FREEZE.json.ots` is an OpenTimestamps proof. It links the hash of `FREEZE.json` through a chain of hash operations to the Merkle root of a Bitcoin block. A block's time is fixed once it is mined, and nobody can add a hash to an old block.

`tools/verify.py` reads the proof, fetches the block header from a public block explorer (blockstream.info, then mempool.space), checks that the header hashes to the block's id and that its Merkle root is the one the proof arrives at, and compares the block time with 00:00 UTC on the first target day.

By hand: `ots verify issues/2026-10-02/FREEZE.json.ots` with a Bitcoin node running, or drop `FREEZE.json` and its `.ots` file onto https://opentimestamps.org. A proof that still says "pending" has not reached a block yet. `ots upgrade` completes it some hours after the stamp, and we commit the upgraded proof. The upgraded proof commits to the same `FREEZE.json`.

## 3. GitHub received the forecast before the target day

A commit's own date is whatever the author's computer said, so it proves nothing. GitHub records when it receives each push, and for a public repository that record is open to anyone:

```
curl -s https://api.github.com/repos/SimPPL/arbiter-forecast-ledger/activity
```

Each entry gives the push time and the commit the branch moved to (`after`). Find the commit that added the issue's `FREEZE.json` with `git log --diff-filter=A -- issues/2026-10-02/FREEZE.json`, find the first push that carried it, and compare that time with 00:00 UTC on the target day. The script does this, falling back to the events API, which GitHub keeps for about 90 days. The same record lists any force push, which would mean history was rewritten. The script reports the count.

## 4. Real posts came after the forecast (from issue 2026-10-03 on)

An X post id encodes the moment the post was created: shift the id right by 22 bits and add 1288834974657 to get milliseconds since 1970 in UTC. Once `outcomes/<day>/posts.json` exists, the script checks that every real post scored against an issue was created after GitHub received that issue.

## 5. Each unit is its own commit, and rebuilds from the frozen forecast (from issue 2026-10-04 on)

From issue 2026-10-04 on, each story, category, predicted post and account in an issue is a small file committed on its own (the README explains the units). `UNITS.json` lists every unit file with its sha256 and the commit that added it. The script checks four things:

- Every unit file matches its sha256. The commit listed beside it holds that file alone, and every unit file on disk appears in the list.
- Every story and category unit, rebuilt from the frozen parquet in the same folder, equals the published file once both are parsed. The category lists split each country's stories, with no story left out or counted twice.
- `UNITS.json.ots` puts `UNITS.json` in a Bitcoin block before the first target day.
- GitHub received the commit that added `UNITS.json` before the first target day.

The rebuild reads parquet, so it needs `pip install pandas pyarrow`. Without them the script reports the rebuild as not checked. Outcome folders get the same checks against `OUTCOMES-UNITS.json`, and the rebuild there also recomputes every score from the forecast and the real count, and compares each real count with `stories.json`.

By hand: pick a unit, read its `derivedFrom`, open that parquet with pandas and find the row by its key. The forecast, range and chance of any post in the unit are the values in that row. `git log --format='%H %s' -- <unit path>` shows the one commit that added it, and `git show --stat <commit>` shows that the commit holds that file alone.

The category of each story comes from an internal Arbiter table that stays private. `FREEZE.json` holds its hash, so we cannot change it after the freeze. Account rows show a public account by handle and every other account by `accountRef`, a hash of the handle and a salt we keep private for each issue. You can check every account score from the public files, and we can show which account a row meant to anyone who has the right to know.

An hourly issue (`hourly/<day>/<HH>/`) must reach GitHub before its hour begins. The script marks a late one VOID, and it stays out of every score.

Issues before 2026-10-04 were published as single commits and have no unit files. Outcomes before the 3 October target have none either.

## What fails today, and why

- Issue 2026-09-27, the pilot, fails both the stamp and the push check. Its stamp was made on 29 September and this copy reached the ledger on 2 October, both after its target day of 28 September. Its only earlier record is a push to our private research repo, which you cannot check.
- Issue 2026-09-29 fails the push check, because this copy reached the ledger on 2 October. Its stamp passes: the hash of its `FREEZE.json` was in Bitcoin block 969098, mined at 05:05 UTC on 29 September, before 30 September began.
- From issue 2026-10-02 on, every issue is pushed here before its target day, so all checks are expected to pass.
