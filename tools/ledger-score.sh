#!/usr/bin/env bash
# Publish the outcome of one scored target day: upgrade any pending stamps, export outcomes/<day>/,
# commit, then print the push command. It never pushes. Run after the research repo's fwd_score.py has
# written and committed the day's score file.
#
# Usage: tools/ledger-score.sh <day> [--dry-run]
#   --dry-run  work in a scratch clone of the ledger; nothing changes here.
# FORCE=1 rewrites an existing outcome folder. ots upgrade leaves FREEZE.json.ots.bak beside each proof; git ignores it.
# Exit codes: 0 done, 2 refused.
set -euo pipefail

LEDGER="$(cd "$(dirname "$0")/.." && pwd)"
RESEARCH="${RESEARCH:-$HOME/Documents/simppl/papers/narrative-reach-sim}"
OTS="${OTS:-$RESEARCH/tmp/venv-ots/bin/ots}"
PY="${PY:-$RESEARCH/tmp/venv-bo/bin/python}"
DAY="${1:?usage: ledger-score.sh <day> [--dry-run]}"
DRY="${2:-}"

if [[ ! "$DAY" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "refused: day must be YYYY-MM-DD" >&2; exit 2
fi

WORK="$LEDGER"
if [[ "$DRY" == "--dry-run" ]]; then
  WORK="$(mktemp -d "${TMPDIR:-/tmp}/ledger-dry.XXXXXX")"
  git clone -q "$LEDGER" "$WORK/ledger"
  WORK="$WORK/ledger"
  echo "dry run in $WORK"
fi

cd "$WORK"
if [[ -n "$(git status --porcelain -- issues outcomes)" ]]; then
  echo "refused: issues/ or outcomes/ has uncommitted changes" >&2; exit 2
fi

# A pending stamp gains its Bitcoin attestation some hours after it was made; the stamped file never changes.
if [[ -x "$OTS" ]]; then
  for proof in issues/*/FREEZE.json.ots; do
    if "$OTS" info "$proof" | grep -q BitcoinBlockHeaderAttestation; then continue; fi
    if "$OTS" upgrade "$proof" >/dev/null 2>&1 && "$OTS" info "$proof" | grep -q BitcoinBlockHeaderAttestation; then
      python3 tools/export_issue.py "$(dirname "$proof")" --refresh-manifest
      git add "$(dirname "$proof")"
      git commit -q -m "$(dirname "$proof"): OpenTimestamps proof upgraded with its Bitcoin attestation"
      echo "upgraded $proof"
    else
      echo "still pending: $proof"
    fi
  done
fi

"$PY" tools/export_outcome.py "$DAY" --research "$RESEARCH" ${FORCE:+--force}
"$PY" tools/outcome_table.py "$DAY" --research "$RESEARCH"
git add "outcomes/$DAY"
git commit -q -m "outcome $DAY: actual post counts and scores from the research score files" || echo "nothing changed in outcomes/$DAY"
git log --oneline -1

python3 tools/verify.py --offline || true

if [[ "$DRY" == "--dry-run" ]]; then
  echo "dry run: committed only in the scratch clone; the ledger at $LEDGER is unchanged"
else
  echo "push as its own command:"
  echo "  git -C $LEDGER push origin main"
fi
