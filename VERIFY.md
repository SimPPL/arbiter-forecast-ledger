# How to verify a forecast

Each forecast in `issues/<day>/` claims it was made before the day it predicts. You can check that claim with three tests. None of them asks you to trust us, and a fourth applies once predicted posts are published.

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

## What fails today, and why

- Issue 2026-09-27, the pilot, fails both the stamp and the push check. Its stamp was made on 29 September and this copy reached the ledger on 2 October, both after its target day of 28 September. Its only earlier record is a push to our private research repo, which you cannot check.
- Issue 2026-09-29 fails the push check, because this copy reached the ledger on 2 October. Its stamp passes: the hash of its `FREEZE.json` was in Bitcoin block 969098, mined at 05:05 UTC on 29 September, before 30 September began.
- From issue 2026-10-02 on, every issue is pushed here before its target day, so all checks are expected to pass.
