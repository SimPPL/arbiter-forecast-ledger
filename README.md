# Arbiter forecast ledger

Every morning we forecast which stories people on X will post about one day and three days from now, in the United States and in India. This repository publishes each forecast before the day it predicts, and adds what actually happened afterwards. You do not have to take our word for the timing. Each forecast carries three clocks we cannot set ourselves, and this README shows you how to read them.

The stories are Arbiter's. Arbiter is SimPPL's social listening platform, and it groups posts on X into stories (we call each one a narrative, with a key such as `IN:f1bb9ee3c01de292`). A forecast says how many posts each story will get on a given day, with an 80 percent range.

## What is in each folder

`issues/<day>/` holds one forecast, named by the UTC date it was frozen.

- `forecast.json` lists the ten stories we expect to be busiest in each region, for each target day, with the forecast count, the range, the story title and its Arbiter key.
- `FREEZE.json` holds the sha256 hash of every forecast file, the commit of the code that made them and the time of the freeze.
- `FREEZE.json.ots` is an OpenTimestamps proof that `FREEZE.json` existed at a Bitcoin block time.
- `MANIFEST.json` holds the sha256 of every file in the folder.

`outcomes/<day>/` holds what happened on a target day: the actual post counts for the forecast stories (`stories.json`) and the score against simple references (`score.json`), such as yesterday's count and a forecast that every story dies.

## How to check the timing in three steps

1. Re-hash the files. Every file in a folder must match `MANIFEST.json`, and every forecast file must match `FREEZE.json`. If one byte changed after the freeze, the hash will not match.
2. Check the OpenTimestamps proof. `ots verify FREEZE.json.ots` shows the Bitcoin block that holds the hash of `FREEZE.json`. Nobody can backdate a Bitcoin block.
3. Check when GitHub received the commit. A commit date is whatever the author's clock said, so it proves nothing. GitHub records the time it received each push, and its public API shows it. The push that added an issue folder must come before 00:00 UTC on the target day. Once predicted posts are published, the posts they are scored against carry their own clock: an X post id encodes the time the post was created.

`tools/verify.py` runs all three and prints one line per check. `VERIFY.md` explains each check in more detail and how to run it by hand.

```
python3 tools/verify.py
```

## Rules

- Horizons are one day and three days ahead, counted from the issue day. The two earliest issues, frozen on 27 and 29 September 2026, forecast two days ahead. They were made before this rule and are published unchanged, because changing them would break their hashes.
- Every forecast starts from the newest reading Arbiter has published.
- Predicted posts name public accounts only: public figures, organisations, media outlets, or accounts with a post seen at least 250,000 times. Never a private account and never a minor. Each real post is cited by its id and link, so anyone can open it on X.
- Days are UTC.

## What is here today

The first three issues are copies of forecasts frozen in our private research repository, with identical hashes and stamps.

- Issue 2026-09-27, the pilot, forecast 28 September and was scored by story key. Its OpenTimestamps proof was made on 29 September, after the target day, and it reached this repository on 2 October. Its timing rests on our private repository's push record, so an outsider cannot check it.
- Issue 2026-09-29 forecast 30 September. Its stamp was made at 04:34 UTC on 29 September, before the target day, and it is anchored in Bitcoin. Arbiter renamed every story on 30 September, so the forecast was scored through a bridge that follows each story's posts across the rename. Every number from that score says "bridged" and gives the share of the forecast that could not be matched.
- Issue 2026-10-02 forecast 3 October (one day ahead) and 5 October (three days ahead). It was stamped at 07:02 UTC on 2 October.

Predicted posts, the text we expect named accounts to post, begin with issue 2026-10-03. Earlier issues forecast story volumes only.

## Daily procedure

`tools/ledger-publish.sh <issueDay>` exports a new frozen issue, commits it and prints the push command. `tools/ledger-score.sh <day>` exports the outcome of a scored day, commits it and prints the push command. Neither pushes by itself.
