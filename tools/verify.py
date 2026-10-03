"""Check that every forecast in this ledger was made before the day it predicts.

For each issue folder it runs these checks and prints one line per check, PASS or FAIL:

  manifest  every file matches MANIFEST.json, and no file is missing or added
  freeze    every published forecast file matches its sha256 in FREEZE.json
  stamp     the OpenTimestamps proof commits to FREEZE.json, and the Bitcoin block that anchors it
            (header fetched from a public block explorer) is older than the first target day
  push      GitHub's own record of the push that added FREEZE.json (public activity API, then the
            events API) is older than the first target day
  posts     once outcomes/<day>/posts.json exists: every real post's X id decodes to a time after
            that push

It also checks outcome folders against their MANIFEST.json and GitHub's record for force pushes.

Usage: python3 tools/verify.py [issueDay ...] [--repo OWNER/NAME] [--offline] [--ots-python PATH]
Exit code 0 when every check passes, 1 otherwise. Standard library only, Python 3.9 or later; the
stamp check also needs the opentimestamps package (pip install opentimestamps-client), found either
in this interpreter or in the one given by --ots-python or OTS_PYTHON.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Tuple

LEDGER = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
REPO = 'SimPPL/arbiter-forecast-ledger'
EXPLORERS = ('https://blockstream.info/api', 'https://mempool.space/api')
TWITTER_EPOCH_MS = 1288834974657
DEFAULT_OTS_PYTHON = os.path.expanduser('~/Documents/simppl/papers/narrative-reach-sim/tmp/venv-ots/bin/python')
UTC = dt.timezone.utc
# Left by `ots upgrade` and macOS; git ignores them too.
IGNORED = ('.ots.bak', '.DS_Store')


class Report:
    """Collects one line per check and the overall result."""

    def __init__(self) -> None:
        """Start an empty report.

        @returns: None.
        @throws: nothing.
        """
        self.failed = 0
        self.total = 0

    def line(self, ok: bool, where: str, check: str, text: str) -> None:
        """Print one check result.

        @param ok: whether the check passed.
        @param where: folder the check is about.
        @param check: short check name.
        @param text: what was found.
        @returns: None.
        @throws: nothing.
        """
        self.total += 1
        self.failed += 0 if ok else 1
        print(f'{"PASS" if ok else "FAIL"}  {where:<20} {check:<9} {text}')


def sha256(path: str) -> str:
    """Hash a file.

    @param path: file path.
    @returns: hex sha256.
    @throws OSError: when unreadable.
    """
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def iso(t: dt.datetime) -> str:
    """Format an aware time as UTC ISO 8601.

    @param t: aware datetime.
    @returns: YYYY-MM-DDTHH:MM:SSZ.
    @throws: nothing.
    """
    return t.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def parseTime(s: str) -> dt.datetime:
    """Parse a GitHub or ISO time string.

    @param s: e.g. 2026-10-02T23:08:58Z.
    @returns: aware UTC datetime.
    @throws ValueError: on a malformed string.
    """
    return dt.datetime.fromisoformat(s.replace('Z', '+00:00')).astimezone(UTC)


def fetchJson(url: str, timeout: float = 30.0) -> object:
    """GET a JSON document.

    @param url: address.
    @param timeout: seconds.
    @returns: parsed JSON.
    @throws urllib.error.URLError: on a network failure.
    @throws ValueError: when the body is not JSON.
    """
    return fetchPage(url, timeout)[0]


def fetchPage(url: str, timeout: float = 30.0) -> Tuple[object, Optional[str]]:
    """GET a JSON document and the address of its next page from the Link header.

    @param url: address.
    @param timeout: seconds.
    @returns: (parsed JSON, next page address or None).
    @throws urllib.error.URLError: on a network failure.
    @throws ValueError: when the body is not JSON.
    """
    req = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'arbiter-forecast-ledger-verify'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.loads(r.read().decode('utf-8'))
        nxt = None
        for part in (r.headers.get('Link') or '').split(','):
            if 'rel="next"' in part:
                nxt = part.split(';')[0].strip().strip('<>')
        return body, nxt


def fetchText(url: str, timeout: float = 30.0) -> str:
    """GET a text document.

    @param url: address.
    @param timeout: seconds.
    @returns: body text, stripped.
    @throws urllib.error.URLError: on a network failure.
    """
    req = urllib.request.Request(url, headers={'User-Agent': 'arbiter-forecast-ledger-verify'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8').strip()


def checkManifest(rep: Report, folder: str) -> Optional[dict]:
    """Re-hash every file against MANIFEST.json.

    @param rep: report.
    @param folder: issue or outcome folder.
    @returns: the parsed manifest, or None when absent.
    @throws OSError: on an unreadable file.
    """
    where = os.path.relpath(folder, LEDGER)
    path = os.path.join(folder, 'MANIFEST.json')
    if not os.path.exists(path):
        rep.line(False, where, 'manifest', 'MANIFEST.json is missing')
        return None
    man = json.load(open(path, encoding='utf-8'))
    listed = man['files']
    present = set()
    for root, _, names in os.walk(folder):
        for n in names:
            rel = os.path.relpath(os.path.join(root, n), folder)
            if rel != 'MANIFEST.json' and not rel.endswith(IGNORED):
                present.add(rel)
    bad = [n for n, d in listed.items() if n not in present or sha256(os.path.join(folder, n)) != d]
    extra = sorted(present - set(listed))
    if bad or extra:
        rep.line(False, where, 'manifest', f'changed or missing: {", ".join(bad) or "none"}; not listed: {", ".join(extra) or "none"}')
    else:
        rep.line(True, where, 'manifest', f'{len(listed)} files match MANIFEST.json')
    return man


def checkFreeze(rep: Report, folder: str) -> Optional[dict]:
    """Re-hash the published forecast files against FREEZE.json.

    @param rep: report.
    @param folder: issue folder.
    @returns: the parsed FREEZE.json, or None when absent.
    @throws OSError: on an unreadable file.
    """
    where = os.path.relpath(folder, LEDGER)
    path = os.path.join(folder, 'FREEZE.json')
    if not os.path.exists(path):
        rep.line(False, where, 'freeze', 'FREEZE.json is missing')
        return None
    fr = json.load(open(path, encoding='utf-8'))
    ok, bad, private = [], [], []
    for name, digest in fr['files'].items():
        local = os.path.join(folder, name.split(':', 1)[-1]) if not name.startswith('input:') else None
        if local is None or not os.path.exists(local):
            private.append(name.split(':', 1)[-1])
        elif sha256(local) == digest:
            ok.append(name)
        else:
            bad.append(name)
    published = [n for n in ok if n.startswith(('forecast_', 'pred_'))]
    missing = not any(n.startswith('forecast_') for n in ok)
    if bad or missing:
        rep.line(False, where, 'freeze', f'mismatch: {", ".join(bad) or "none"}' + ('; no forecast file published' if missing else ''))
    else:
        rep.line(True, where, 'freeze', f'{len(published)} forecast files match FREEZE.json (sha256 {sha256(path)[:16]}); '
                 f'{len(private)} private inputs listed, not published')
    return fr


def firstTargetStart(folder: str) -> Tuple[Optional[dt.datetime], List[str]]:
    """Start of the earliest target day (00:00 UTC) and all target days.

    @param folder: issue folder.
    @returns: (start time or None, sorted target days).
    @throws OSError: on an unreadable forecast.json.
    """
    path = os.path.join(folder, 'forecast.json')
    if not os.path.exists(path):
        return None, []
    days = sorted(h['targetDay'] for h in json.load(open(path, encoding='utf-8'))['horizons'])
    return dt.datetime.fromisoformat(days[0]).replace(tzinfo=UTC), days


def otsPython(explicit: Optional[str]) -> Optional[str]:
    """Interpreter that can import opentimestamps.

    @param explicit: --ots-python value.
    @returns: path, or None when none is found.
    @throws: nothing.
    """
    for cand in (explicit, os.environ.get('OTS_PYTHON'), sys.executable, DEFAULT_OTS_PYTHON):
        if not cand or not os.path.exists(cand):
            continue
        r = subprocess.run([cand, '-c', 'import opentimestamps'], capture_output=True)
        if r.returncode == 0:
            return cand
    ots = shutil.which('ots')
    if ots:
        cand = os.path.join(os.path.dirname(os.path.realpath(ots)), 'python')
        if os.path.exists(cand):
            return cand
    return None


def otsAttestations(py: str, otsPath: str) -> dict:
    """Read the file digest and every attestation of an .ots proof.

    @param py: interpreter with opentimestamps.
    @param otsPath: proof file.
    @returns: {'digest': hex, 'bitcoin': [(height, msgHex)], 'pending': [url]}.
    @throws RuntimeError: when the proof cannot be parsed.
    """
    r = subprocess.run([py, os.path.abspath(__file__), '--ots-dump', otsPath], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or 'ots dump failed')
    return json.loads(r.stdout)


def otsDump(otsPath: str) -> int:
    """Print an .ots proof's digest and attestations as JSON (runs under the opentimestamps interpreter).

    @param otsPath: proof file.
    @returns: exit code.
    @throws ImportError: when opentimestamps is not installed.
    """
    from opentimestamps.core.notary import BitcoinBlockHeaderAttestation, PendingAttestation
    from opentimestamps.core.serialize import StreamDeserializationContext
    from opentimestamps.core.timestamp import DetachedTimestampFile
    with open(otsPath, 'rb') as fh:
        d = DetachedTimestampFile.deserialize(StreamDeserializationContext(fh))
    out = {'digest': d.file_digest.hex(), 'op': str(d.file_hash_op), 'bitcoin': [], 'pending': []}
    for msg, att in d.timestamp.all_attestations():
        if isinstance(att, BitcoinBlockHeaderAttestation):
            out['bitcoin'].append([att.height, msg.hex()])
        elif isinstance(att, PendingAttestation):
            out['pending'].append(att.uri)
    print(json.dumps(out))
    return 0


def blockHeader(height: int) -> Tuple[bytes, str]:
    """Fetch a Bitcoin block header by height from a public explorer, checking its hash.

    @param height: block height.
    @returns: (80-byte header, explorer used).
    @throws RuntimeError: when no explorer returns a header that hashes to the block hash.
    """
    errors = []
    for base in EXPLORERS:
        try:
            bhash = fetchText(f'{base}/block-height/{height}')
            hdr = bytes.fromhex(fetchText(f'{base}/block/{bhash}/header'))
            calc = hashlib.sha256(hashlib.sha256(hdr).digest()).digest()[::-1].hex()
            if len(hdr) == 80 and calc == bhash:
                return hdr, base
            errors.append(f'{base}: header does not hash to {bhash}')
        except (urllib.error.URLError, ValueError, OSError) as e:
            errors.append(f'{base}: {e}')
    raise RuntimeError('; '.join(errors))


def checkStamp(rep: Report, folder: str, start: Optional[dt.datetime], py: Optional[str], offline: bool, name: str = 'FREEZE.json') -> None:
    """Check the OpenTimestamps proof of a file against Bitcoin block headers.

    @param rep: report.
    @param folder: folder holding the file and its .ots.
    @param start: time the stamp must precede (None: existence only).
    @param py: interpreter with opentimestamps, or None.
    @param offline: skip network calls.
    @param name: stamped file name.
    @returns: None.
    @throws: nothing; every failure becomes a FAIL line.
    """
    where = os.path.relpath(folder, LEDGER)
    otsPath = os.path.join(folder, f'{name}.ots')
    if not os.path.exists(otsPath):
        rep.line(False, where, 'stamp', f'{name}.ots is missing')
        return
    if py is None:
        rep.line(False, where, 'stamp', 'not checked: install opentimestamps-client (pip install opentimestamps-client) and re-run, '
                 f'or run `ots verify {os.path.relpath(otsPath, LEDGER)}` with a Bitcoin node')
        return
    try:
        a = otsAttestations(py, otsPath)
    except RuntimeError as e:
        rep.line(False, where, 'stamp', f'proof unreadable: {e}')
        return
    if a['digest'] != sha256(os.path.join(folder, name)):
        rep.line(False, where, 'stamp', f'proof commits to {a["digest"][:16]}, not to this {name}')
        return
    if not a['bitcoin']:
        rep.line(False, where, 'stamp', f'not yet anchored in Bitcoin ({len(a["pending"])} calendars pending); run `ots upgrade` later')
        return
    if offline:
        rep.line(False, where, 'stamp', f'commits to {name}; Bitcoin blocks {sorted(h for h, _ in a["bitcoin"])} not checked (offline)')
        return
    best = None
    errors = []
    for height, msg in a['bitcoin']:
        try:
            hdr, base = blockHeader(height)
        except RuntimeError as e:
            errors.append(f'block {height}: {e}')
            continue
        if hdr[36:68].hex() != msg:
            errors.append(f'block {height}: merkle root does not match the proof')
            continue
        t = dt.datetime.fromtimestamp(int.from_bytes(hdr[68:72], 'little'), UTC)
        if best is None or t < best[1]:
            best = (height, t, base)
    if best is None:
        rep.line(False, where, 'stamp', '; '.join(errors) or 'no attestation verified')
        return
    height, t, base = best
    text = f'{name} is in Bitcoin block {height}, mined {iso(t)} (header from {base.split("/")[2]})'
    if start is None:
        rep.line(True, where, 'stamp', text)
    elif t < start:
        rep.line(True, where, 'stamp', f'{text}, before {iso(start)}')
    else:
        rep.line(False, where, 'stamp', f'{text}, after {iso(start)}: proves existence from then on, not before the target day')


def git(*args: str) -> subprocess.CompletedProcess:
    """Run git in the ledger clone.

    @param args: git arguments.
    @returns: completed process.
    @throws OSError: when git is absent.
    """
    return subprocess.run(['git', '-C', LEDGER, *args], capture_output=True, text=True)


def addingCommit(path: str) -> Optional[str]:
    """Oldest commit that added a path.

    @param path: path relative to the ledger root.
    @returns: commit sha, or None when not committed.
    @throws: nothing.
    """
    out = git('log', '--diff-filter=A', '--format=%H', '--', path).stdout.split()
    return out[-1] if out else None


def isAncestor(commit: str, head: Optional[str]) -> bool:
    """Whether a commit is reachable from head.

    @param commit: candidate ancestor.
    @param head: descendant, or None / all zeros for an empty ref.
    @returns: True when reachable.
    @throws: nothing.
    """
    if not head or set(head) == {'0'}:
        return False
    return git('merge-base', '--is-ancestor', commit, head).returncode == 0


def githubPushes(repo: str) -> Tuple[List[dict], str]:
    """GitHub's record of pushes to the repo, oldest first.

    @param repo: OWNER/NAME.
    @returns: ([{'time', 'before', 'after', 'type'}], source name).
    @throws RuntimeError: when neither API answers.
    """
    errors = []
    try:
        rows, seen, url, pages = [], set(), f'https://api.github.com/repos/{repo}/activity?per_page=100', 0
        while url and pages < 20:
            got, url = fetchPage(url)
            pages += 1
            for g in got:
                if g['id'] not in seen:
                    seen.add(g['id'])
                    rows.append({'time': parseTime(g['timestamp']), 'before': g.get('before'), 'after': g.get('after'),
                                 'type': g.get('activity_type')})
        if rows:
            return sorted(rows, key=lambda r: r['time']), 'activity API'
    except (urllib.error.URLError, ValueError, KeyError, OSError) as e:
        errors.append(f'activity API: {e}')
    try:
        got = fetchJson(f'https://api.github.com/repos/{repo}/events?per_page=100')
        rows = [{'time': parseTime(g['created_at']), 'before': g['payload'].get('before'), 'after': g['payload'].get('head'),
                 'type': 'push'} for g in got if g.get('type') == 'PushEvent']
        if rows:
            return sorted(rows, key=lambda r: r['time']), 'events API'
    except (urllib.error.URLError, ValueError, KeyError, OSError) as e:
        errors.append(f'events API: {e}')
    raise RuntimeError('; '.join(errors) or 'no push recorded')


def pushTimeOf(commit: str, pushes: List[dict]) -> Optional[dt.datetime]:
    """Time GitHub first received a commit.

    @param commit: commit sha.
    @param pushes: rows from githubPushes.
    @returns: push time, or None when no recorded push carried it.
    @throws: nothing.
    """
    for p in pushes:
        if isAncestor(commit, p['after']) and not isAncestor(commit, p['before']):
            return p['time']
    return None


def checkPush(rep: Report, folder: str, start: Optional[dt.datetime], pushes: Optional[List[dict]], source: str) -> Optional[dt.datetime]:
    """Compare GitHub's receipt of the commit that added FREEZE.json with the first target day.

    @param rep: report.
    @param folder: issue folder.
    @param start: first target day start.
    @param pushes: GitHub push rows, or None when unavailable.
    @param source: name of the API the rows came from, or the error.
    @returns: the push time, when found.
    @throws: nothing.
    """
    where = os.path.relpath(folder, LEDGER)
    commit = addingCommit(os.path.relpath(os.path.join(folder, 'FREEZE.json'), LEDGER))
    if commit is None:
        rep.line(False, where, 'push', 'FREEZE.json is not committed')
        return None
    if pushes is None:
        rep.line(False, where, 'push', f'GitHub record unavailable ({source})')
        return None
    t = pushTimeOf(commit, pushes)
    if t is None:
        rep.line(False, where, 'push', f'commit {commit[:7]} is not in any push GitHub recorded ({source}); push it, or fetch first')
        return None
    text = f'GitHub received commit {commit[:7]} at {iso(t)} ({source})'
    if start is None:
        rep.line(False, where, 'push', f'{text}; forecast.json is missing, so no target day to compare')
    elif t < start:
        rep.line(True, where, 'push', f'{text}, before {iso(start)}')
    else:
        rep.line(False, where, 'push', f'{text}, after {iso(start)}: this copy reached the ledger late, see the folder README')
    return t


def xPostTime(postId: str) -> dt.datetime:
    """Creation time encoded in an X (Twitter) post id.

    @param postId: numeric id as a string.
    @returns: aware UTC datetime.
    @throws ValueError: when the id is not numeric.
    """
    return dt.datetime.fromtimestamp(((int(postId) >> 22) + TWITTER_EPOCH_MS) / 1000, UTC)


def checkPosts(rep: Report, issueDay: str, days: List[str], pushed: Optional[dt.datetime]) -> None:
    """Every real post scored against this issue must postdate the issue's push.

    @param rep: report.
    @param issueDay: issue folder name.
    @param days: target days.
    @param pushed: GitHub push time of the issue.
    @returns: None.
    @throws: nothing; every failure becomes a FAIL line.
    """
    for day in days:
        path = os.path.join(LEDGER, 'outcomes', day, 'posts.json')
        if not os.path.exists(path):
            continue
        rows = [p for p in json.load(open(path, encoding='utf-8')).get('posts', []) if p.get('issueDay') == issueDay]
        if not rows:
            continue
        where = f'issues/{issueDay}'
        try:
            times = [xPostTime(str(p['postId'])) for p in rows]
        except (KeyError, ValueError) as e:
            rep.line(False, where, 'posts', f'outcomes/{day}/posts.json has a bad post id: {e}')
            continue
        first = min(times)
        if pushed is None:
            rep.line(False, where, 'posts', f'{len(rows)} posts on {day}, earliest {iso(first)}; no push time to compare')
        elif first > pushed:
            rep.line(True, where, 'posts', f'{len(rows)} posts on {day}, earliest created {iso(first)} (from its X id), after the push')
        else:
            rep.line(False, where, 'posts', f'{len(rows)} posts on {day}, earliest created {iso(first)}, before the push at {iso(pushed)}')


def main(argv: Optional[List[str]] = None) -> int:
    """Run every check.

    @param argv: arguments (defaults to sys.argv).
    @returns: 0 when all pass, 1 otherwise.
    @throws SystemExit: on bad arguments.
    """
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('days', nargs='*', help='issue days to check (default: all)')
    ap.add_argument('--repo', default=REPO)
    ap.add_argument('--offline', action='store_true', help='skip GitHub and Bitcoin lookups')
    ap.add_argument('--ots-python', default=None)
    ap.add_argument('--ots-dump', default=None, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.ots_dump:
        return otsDump(a.ots_dump)
    rep = Report()
    issuesDir = os.path.join(LEDGER, 'issues')
    days = a.days or sorted(d for d in os.listdir(issuesDir) if os.path.isdir(os.path.join(issuesDir, d)))
    py = otsPython(a.ots_python)
    pushes, source = None, 'offline'
    if not a.offline:
        try:
            pushes, source = githubPushes(a.repo)
        except RuntimeError as e:
            source = str(e)
    if pushes is not None:
        forced = [p for p in pushes if p['type'] == 'force_push']
        rep.line(not forced, a.repo, 'history', f'{len(forced)} force pushes in {len(pushes)} recorded pushes ({source})')
    for day in days:
        folder = os.path.join(issuesDir, day)
        if not os.path.isdir(folder):
            rep.line(False, f'issues/{day}', 'folder', 'no such issue')
            continue
        checkManifest(rep, folder)
        checkFreeze(rep, folder)
        start, targets = firstTargetStart(folder)
        checkStamp(rep, folder, start, py, a.offline)
        pushed = checkPush(rep, folder, start, pushes, source)
        checkPosts(rep, day, targets, pushed)
    outDir = os.path.join(LEDGER, 'outcomes')
    if not a.days and os.path.isdir(outDir):
        for day in sorted(os.listdir(outDir)):
            if os.path.isdir(os.path.join(outDir, day)):
                checkManifest(rep, os.path.join(outDir, day))
    print(f'{rep.total - rep.failed} of {rep.total} checks passed')
    return 0 if rep.failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
