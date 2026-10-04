#!/usr/bin/env bash
# Commit one issue's (or one scored day's) unit files, one commit per unit, then index and stamp them. It never pushes.
#
# Usage: tools/commit_units.sh <issueDay> [--dry-run]
#        tools/commit_units.sh --outcome <targetDay> [--dry-run]
#   --dry-run  work in a scratch clone of the ledger; nothing changes here. The commit list is printed at the end.
# Order of commits: categories, narratives by rank (US, then India), predicted posts, accounts, then UNITS.json
# (OUTCOMES-UNITS.json for an outcome) with its OpenTimestamps stamp and the regenerated views.
# Before anything is committed every unit and message is scanned for every private FWD-TWEETS-1 handle; a hit refuses.
# A run that stops part way can be run again: units already committed with the same content are skipped.
# Exit codes: 0 done, 2 refused.
set -euo pipefail

LEDGER="$(cd "$(dirname "$0")/.." && pwd)"
RESEARCH="${RESEARCH:-$HOME/Documents/simppl/papers/narrative-reach-sim}"
PRIVATE="${PRIVATE:-$HOME/Documents/simppl/papers/arbiter-prediction-prereg}"
OTS="${OTS:-$RESEARCH/tmp/venv-ots/bin/ots}"
PY="${PY:-$RESEARCH/tmp/venv-bo/bin/python}"
# The parquet reads and the whole-issue rebuild share the machine with other agents: one heavy job at a time through the
# research repo's lock (tmp/MEMORY-RULES.txt there), with capped threads. LOCK= (empty) runs without it.
LOCK="${LOCK-$RESEARCH/tmp/heavy.lock}"
export OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4
HEAVY=()
if [[ -n "$LOCK" && -d "$(dirname "$LOCK")" ]] && command -v lockf >/dev/null; then HEAVY=(lockf -k "$LOCK"); fi

KIND=issue
if [[ "${1:-}" == "--outcome" ]]; then KIND=outcome; shift; fi
DAY="${1:?usage: commit_units.sh [--outcome] <day> [--dry-run]}"
DRY="${2:-}"
if [[ ! "$DAY" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "refused: day must be YYYY-MM-DD" >&2; exit 2
fi

WORK="$LEDGER"
EXTRA=()
if [[ "$DRY" == "--dry-run" ]]; then
  WORK="$(mktemp -d "${TMPDIR:-/tmp}/ledger-units-dry.XXXXXX")/ledger"
  git clone -q "$LEDGER" "$WORK"
  # Rehearse with the tools as they stand in the working ledger, committed or not.
  cp "$LEDGER"/tools/*.py "$LEDGER"/tools/*.sh "$WORK/tools/"
  # A dry run may cover a day before the unit rule began, to rehearse on published data.
  EXTRA=(--force-early)
  echo "dry run in $WORK"
fi
cd "$WORK"
START="$(git rev-parse HEAD)"

if [[ "$KIND" == "issue" ]]; then FOLDER="issues/$DAY"; else FOLDER="outcomes/$DAY"; fi
if [[ -n "$(git status --porcelain -- "$FOLDER" by-narrative by-category by-hour SCOREBOARD.md)" ]]; then
  echo "refused: $FOLDER or the views have uncommitted changes" >&2; exit 2
fi

${HEAVY[@]+"${HEAVY[@]}"} "$PY" tools/export_units.py "$KIND" "$DAY" --research "$RESEARCH" --private "$PRIVATE" --ots "$OTS" --commit ${EXTRA[@]+"${EXTRA[@]}"}

# Every public file and every new commit message, scanned once more for private handles.
"$PY" tools/scan_public.py --research "$RESEARCH" --since "$START"

if [[ "$KIND" == "issue" ]]; then
  ${HEAVY[@]+"${HEAVY[@]}"} "$PY" tools/verify.py "$DAY" --offline --units-only || true
else
  ${HEAVY[@]+"${HEAVY[@]}"} "$PY" tools/verify.py --outcome-day "$DAY" --offline --units-only || true
fi

N="$(git rev-list --count "$START"..HEAD)"
echo "$N new commits on top of ${START:0:7}"
if [[ "$DRY" == "--dry-run" ]]; then
  git log --reverse --format='%h %s' "$START"..HEAD > "$WORK/../commits.txt"
  echo "dry run: commit list in $WORK/../commits.txt; the ledger at $LEDGER is unchanged"
else
  echo "push before 00:00 UTC on the first target day, as its own command:"
  echo "  git -C $LEDGER push origin main"
fi
