#!/usr/bin/env bash
# Publish one frozen issue to the ledger: export, commit, upgrade or add the stamp, then print the push
# command. It never pushes. Run after the research repo's fwd_freeze.sh has frozen, committed and pushed
# the issue there.
#
# Usage: tools/ledger-publish.sh <issueDay> [--dry-run]
# From issue 2026-10-04 on it also runs tools/commit_units.sh (one commit per unit, then UNITS.json).
#   --dry-run  export into a scratch copy of the ledger and show what would be committed; nothing changes here.
# FORCE=1 rewrites an existing issue folder (the frozen files are copied again; their hashes cannot change).
# Exit codes: 0 done, 2 refused.
set -euo pipefail

LEDGER="$(cd "$(dirname "$0")/.." && pwd)"
RESEARCH="${RESEARCH:-$HOME/Documents/simppl/papers/narrative-reach-sim}"
OTS="${OTS:-$RESEARCH/tmp/venv-ots/bin/ots}"
DAY="${1:?usage: ledger-publish.sh <issueDay> [--dry-run]}"
DRY="${2:-}"

if [[ ! "$DAY" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "refused: issue day must be YYYY-MM-DD" >&2; exit 2
fi

WORK="$LEDGER"
if [[ "$DRY" == "--dry-run" ]]; then
  WORK="$(mktemp -d "${TMPDIR:-/tmp}/ledger-dry.XXXXXX")"
  git clone -q "$LEDGER" "$WORK/ledger"
  WORK="$WORK/ledger"
  echo "dry run in $WORK"
fi

cd "$WORK"
if [[ -n "$(git status --porcelain -- issues)" ]]; then
  echo "refused: issues/ has uncommitted changes" >&2; exit 2
fi

python3 tools/export_issue.py "$DAY" --research "$RESEARCH" ${FORCE:+--force}
FOLDER="issues/$DAY"

if [[ ! -f "$FOLDER/FREEZE.json.ots" ]]; then
  if [[ -x "$OTS" ]]; then
    "$OTS" stamp "$FOLDER/FREEZE.json"
    python3 tools/export_issue.py "$FOLDER" --refresh-manifest
  else
    echo "note: no stamp in the research folder and no ots client at $OTS; stamp FREEZE.json before the target day" >&2
  fi
fi

git add "$FOLDER"
SHA="$(shasum -a 256 "$FOLDER/FREEZE.json" | cut -c1-16)"
git commit -q -m "issue $DAY: frozen forecast copied with identical hashes (FREEZE.json sha256 $SHA...)" || echo "nothing changed in $FOLDER"
git log --oneline -1

python3 tools/verify.py "$DAY" --offline || true

# From issue 2026-10-04 on, every category and narrative (and every FWD-TWEETS-1 predicted post and account, once the
# private repo has committed its units) gets its own commit, then UNITS.json and its stamp.
if [[ ! "$DAY" < "2026-10-04" ]]; then
  bash tools/commit_units.sh "$DAY" || echo "units refused for $DAY; the issue itself is committed. Fix and run tools/commit_units.sh $DAY" >&2
fi

if [[ "$DRY" == "--dry-run" ]]; then
  echo "dry run: committed only in the scratch clone; the ledger at $LEDGER is unchanged"
else
  echo "push before 00:00 UTC on the first target day, as its own command:"
  echo "  git -C $LEDGER push origin main"
fi
