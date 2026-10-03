"""Refuse when a private handle appears in any public file or commit message of the ledger.

The private handles are every account FWD-TWEETS-1 predicted or used as a persona that did not pass
PublicAccounts.decide, read from the research repo's predictions-full.json files (clear handles live only there).
A handle matches as a whole word, any case, with or without '@'.

Usage: <python> tools/scan_public.py [--research R] [--since <commit>] [--all]
  --since  scan the files changed and the commit messages written after that commit (default: every commit)
  --all    scan every tracked text file, not only the changed ones
Exit codes: 0 clean, 1 a handle was found, 2 refused (no handle list).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from typing import List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import export_units as EX  # noqa: E402
import units_lib as U  # noqa: E402

TEXT_ENDINGS = ('.json', '.md', '.txt', '.csv', '.py', '.sh')


def gitOut(ledger: str, *args: str) -> str:
    """Run git and return stdout.

    @param ledger: repo root.
    @param args: git arguments.
    @returns: stdout.
    @throws subprocess.CalledProcessError: when git fails.
    """
    return subprocess.run(['git', '-C', ledger, *args], capture_output=True, text=True, check=True).stdout


def publicTexts(ledger: str, since: Optional[str], everything: bool) -> List[Tuple[str, str]]:
    """Text of the public files and commit messages to scan.

    @param ledger: repo root.
    @param since: commit after which to scan, or None for the whole history.
    @param everything: scan every tracked text file.
    @returns: [(where, text)].
    @throws subprocess.CalledProcessError: when git fails.
    """
    rng = f'{since}..HEAD' if since else 'HEAD'
    if everything or not since:
        files = gitOut(ledger, 'ls-files').split('\n')
    else:
        files = gitOut(ledger, 'diff', '--name-only', since, 'HEAD').split('\n')
    out = []
    for f in files:
        if f.endswith(TEXT_ENDINGS) and os.path.exists(os.path.join(ledger, f)):
            with open(os.path.join(ledger, f), encoding='utf-8', errors='replace') as fh:
                out.append((f, fh.read()))
    for line in gitOut(ledger, 'log', '--format=%H%x00%B%x01', rng).split('\x01'):
        if '\x00' in line:
            h, msg = line.strip().split('\x00', 1)
            out.append((f'commit {h[:7]} message', msg))
    return out


def main(argv: Optional[List[str]] = None) -> int:
    """Command-line entry.

    @param argv: arguments.
    @returns: exit code.
    @throws SystemExit: on bad arguments.
    """
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--research', default=EX.DEFAULT_RESEARCH)
    ap.add_argument('--ledger', default=U.LEDGER)
    ap.add_argument('--since', default=None)
    ap.add_argument('--all', action='store_true')
    a = ap.parse_args(argv)
    handles = EX.privateHandles(a.research)
    if not handles:
        print('refused: no private handle list found in the research repo; nothing was checked')
        return 2
    texts = publicTexts(a.ledger, a.since, a.all)
    hits = U.privateHandleScan(texts, handles)
    if hits:
        # Never print the handle itself: this output can end up in a public log.
        for where, _ in hits:
            print(f'FAIL  private handle in {where}')
        return 1
    print(f'PASS  {len(texts)} public texts hold none of {len(handles)} private handles')
    return 0


if __name__ == '__main__':
    sys.exit(main())
