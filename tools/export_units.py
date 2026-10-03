"""Write the unit layer of one issue or one scored day, commit each unit on its own, then index and stamp the set.

  issue <I>      issues/<I>/categories/..., narratives/... from the frozen parquet, and, when FWD-TWEETS-1 has an issue
                 for <I> with per-unit commits in the private repo, issues/<I>/posts/... and accounts/... built from
                 those private units (sentences and accountRef only; never a private handle or a private account's post)
  outcome <T>    outcomes/<T>/categories/..., narratives/... for every issue that forecast <T>, scored against Arbiter's
                 reading of <T>; posts/... and accounts/... once the private repo holds FWD-TWEETS-1 outcome units

  hourly <day> --hour HH --table F
                 hourly/<day>/<HH>/<region>/<key>.json from a table of hourly forecast objects (HOURLY-AND-DAILY-
                 FORECAST-PROGRAM section 3), once HOURLY-1 is registered. Refused once the hour has begun (UTC): an hourly
                 issue pushed after its hour begins is void and never scored, and tools/verify.py flags it.

With --commit each unit file is committed on its own, in a fixed order (categories, narratives by rank, predicted posts,
accounts), with a message that reads like a scoreboard line. A last commit writes UNITS.json (OUTCOMES-UNITS.json for
an outcome): every unit path, its sha256 and its commit, stamped with OpenTimestamps, and regenerates the views
(by-narrative/, by-category/, SCOREBOARD.md). A run that stops part way resumes: units already committed with the same
content are skipped. A unit is never rewritten.

Before any commit, every unit text and message is scanned for every private handle FWD-TWEETS-1 holds; one hit refuses.

Usage: <python with pandas> tools/export_units.py issue|outcome <day> [--research R] [--private P] [--commit] [--ots OTS]
       <python> tools/export_units.py hourly <day> --hour HH --table <json> [--commit]
Exit codes: 0 done, 2 refused.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import subprocess
import sys
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import units_lib as U  # noqa: E402

LEDGER = U.LEDGER
DEFAULT_RESEARCH = os.path.expanduser('~/Documents/simppl/papers/narrative-reach-sim')
DEFAULT_PRIVATE = os.path.expanduser('~/Documents/simppl/papers/arbiter-prediction-prereg')
DEFAULT_OTS = os.path.join(DEFAULT_RESEARCH, 'tmp', 'venv-ots', 'bin', 'ots')
COMMIT_ORDER = {'category': 0, 'narrative': 1, 'post': 2, 'account': 3, 'hour': 4}


class Refused(RuntimeError):
    """A check failed; nothing further is written or committed."""


def git(ledger: str, *args: str, check: bool = True) -> str:
    """Run git in the ledger.

    @param ledger: repo root.
    @param args: git arguments.
    @param check: raise on a non-zero exit.
    @returns: stdout, stripped.
    @throws Refused: when check is set and git fails.
    """
    r = subprocess.run(['git', '-C', ledger, *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise Refused(f'git {" ".join(args[:3])} failed: {r.stderr.strip()}')
    return r.stdout.strip()


# ---------------------------------------------------------------- private handles

def privateHandles(research: Optional[str]) -> List[str]:
    """Every private handle FWD-TWEETS-1 has predicted or used as a persona, from the research repo's full files.

    @param research: research repo root, or None.
    @returns: lower-case handles of accounts that did not pass PublicAccounts.decide.
    @throws ValueError: when a file is not JSON.
    """
    out, examples, public = set(), set(), set()
    if not research:
        return []
    for p in sorted(glob.glob(os.path.join(research, 'data', 'fwd-tweets', 'issue-*', 'predictions-full.json'))):
        d = U.readJson(p)
        for n in d['narratives']:
            for rows in n.get('who', {}).values():
                for r in rows:
                    if not r.get('public') and r.get('handle'):
                        out.add(r['handle'].lower().lstrip('@'))
            for o in n.get('what', {}).get('S1', []):
                if not o.get('public') and o.get('persona'):
                    out.add(str(o['persona']).lower().lstrip('@'))
            for o in n.get('what', {}).get('S2', []):
                # Continuation quotes real posts; their authors are treated as private unless named public elsewhere.
                if o.get('exampleAuthor'):
                    examples.add(str(o['exampleAuthor']).lower().lstrip('@'))
            for rows in n.get('who', {}).values():
                for r in rows:
                    if r.get('public') and r.get('handle'):
                        public.add(r['handle'].lower().lstrip('@'))
    return sorted(out | (examples - public))


def scanUnits(units: List[Tuple[str, dict, str]], handles: List[str]) -> None:
    """Refuse when any unit text or commit message holds a private handle.

    @param units: [(path, unit, message)].
    @param handles: private handles.
    @returns: None.
    @throws Refused: on any hit.
    """
    texts = []
    for path, u, msg in units:
        texts += [(path, U.dumps(u)), (f'{path} (commit message)', msg)]
    hits = U.privateHandleScan(texts, handles)
    if hits:
        raise Refused('private handle in public text: ' + '; '.join(f'{w}' for w, _ in hits[:10]))


# ---------------------------------------------------------------- FWD-TWEETS-1 units from the private repo

def privateUnitIndex(private: Optional[str], issueDay: str, name: str = 'UNITS.json') -> Optional[dict]:
    """The private repo's unit index of a FWD-TWEETS-1 issue.

    @param private: private repo root, or None.
    @param issueDay: issue day.
    @param name: 'UNITS.json' (predictions) or 'OUTCOME-UNITS.json' (scores).
    @returns: the parsed index, or None when absent.
    @throws ValueError: when not JSON.
    """
    if not private:
        return None
    p = os.path.join(private, 'issues', issueDay, 'units', name)
    return U.readJson(p) if os.path.exists(p) else None


def publicPostUnit(pu: dict, entry: dict, handles: Optional[List[str]] = None) -> dict:
    """The public form of a private predicted-post unit.

    @param pu: private unit.
    @param entry: its row of the private UNITS.json (path, sha256, commit).
    @param handles: private handles to redact from the sentence.
    @returns: public unit: the sentence (a paraphrase for continuation), seat, model, chance and the persona's accountRef.
    @throws KeyError: when a field is absent.
    """
    sentence, n = U.redactHandles(pu['publicSentence'], handles or [])
    out = {'unit': 'predicted post', 'issueDay': pu['issueDay'], 'targetDay': pu['targetDay'], 'region': pu['region'],
           'arbiterKey': pu['arbiterKey'], 'title': pu['title'], 'optionId': pu['optionId'], 'seat': pu['seat'],
           'seatName': pu['seatName'], 'model': pu['model'], 'sentence': sentence,
           'sentenceIs': pu['publicSentenceIs'], 'chance': pu['chance'], 'redacted': n,
           'personaRef': (pu.get('persona') or {}).get('accountRef'),
           'derivedFrom': {'repo': 'arbiter-prediction-prereg (private)', 'file': entry['path'], 'sha256': entry['sha256'],
                           'commit': entry['commit']}}
    return out


def publicAccountUnit(pu: dict, entry: dict) -> dict:
    """The public form of a private account unit: accountRef in place of the account.

    @param pu: private unit.
    @param entry: its row of the private UNITS.json.
    @returns: public unit.
    @throws KeyError: when a field is absent.
    """
    return {'unit': 'account forecast', 'issueDay': pu['issueDay'], 'targetDay': pu['targetDay'], 'region': pu['region'],
            'arbiterKey': pu['arbiterKey'], 'title': pu['title'], 'accountRef': pu['accountRef'], 'public': pu['public'],
            'namedBecause': pu['namedBecause'], 'chances': pu['chances'], 'listRanks': pu['listRanks'],
            'accountRefRule': ('the handle for an account that passes PublicAccounts.decide at the origin; otherwise '
                               'sha256(lower(handle) + ":" + salt), with one salt per issue kept only in the private repo'),
            'derivedFrom': {'repo': 'arbiter-prediction-prereg (private)', 'file': entry['path'], 'sha256': entry['sha256'],
                            'commit': entry['commit']}}


def postMessage(u: dict) -> str:
    """Commit message of a predicted-post unit.

    @param u: public unit.
    @returns: one line.
    @throws: nothing.
    """
    return (f'predict {u["issueDay"]} {u["region"]} "{U.short(u["title"], 40)}" {u["optionId"]} ({u["seat"]}): '
            f'"{U.short(u["sentence"], 80)}", chance {u["chance"]:.2f}')


def accountMessage(u: dict) -> str:
    """Commit message of an account unit.

    @param u: public unit.
    @returns: one line.
    @throws: nothing.
    """
    ref = u['accountRef'] if u['public'] else u['accountRef'][:19]
    ch = ', '.join(f'{m} {p:.2f}' for m, p in u['chances'].items() if p is not None)
    return f'predict {u["issueDay"]} {u["region"]} "{U.short(u["title"], 40)}" account {ref}: posts in the story, {ch}'


def fwdIssueUnits(issueDay: str, private: Optional[str], handles: Optional[List[str]] = None) -> List[Tuple[str, dict, str]]:
    """Public predicted-post and account units of a FWD-TWEETS-1 issue, from its private unit commits.

    @param issueDay: issue day.
    @param private: private repo root.
    @param handles: private handles to redact from sentences.
    @returns: [(path, unit, message)]; empty when the private repo has no unit index for the issue.
    @throws Refused: when a private unit's sha256 differs from its index.
    """
    idx = privateUnitIndex(private, issueDay)
    if idx is None:
        return []
    out = []
    for e in idx['units']:
        full = os.path.join(private, e['path'])
        if U.sha256File(full) != e['sha256']:
            raise Refused(f'private unit {e["path"]} differs from the private UNITS.json')
        pu = U.readJson(full)
        hexk = U.keyHex(pu['arbiterKey'])
        if pu['unit'] == 'predicted post':
            u = publicPostUnit(pu, e, handles)
            out.append((f'issues/{issueDay}/posts/{pu["region"]}/{hexk}/{pu["optionId"]}.json', u, postMessage(u)))
        elif pu['unit'] == 'account forecast':
            u = publicAccountUnit(pu, e)
            out.append((f'issues/{issueDay}/accounts/{pu["region"]}/{hexk}/{U.refFile(pu["accountRef"])}.json', u, accountMessage(u)))
    return out


def fwdOutcomeUnits(day: str, private: Optional[str], handles: Optional[List[str]] = None) -> List[Tuple[str, dict, str]]:
    """Public predicted-post and account outcome units for a target day, from the private repo's outcome units.

    @param day: target day.
    @param private: private repo root.
    @param handles: private handles to redact from sentences.
    @returns: [(path, unit, message)].
    @throws Refused: when a private unit's sha256 differs from its index.
    """
    out: List[Tuple[str, dict, str]] = []
    handles = handles or []
    if not private:
        return out
    for ip in sorted(glob.glob(os.path.join(private, 'issues', '*', 'units', 'OUTCOME-UNITS.json'))):
        idx = U.readJson(ip)
        if idx.get('targetDay') != day:
            continue
        for e in idx['units']:
            full = os.path.join(private, e['path'])
            if U.sha256File(full) != e['sha256']:
                raise Refused(f'private unit {e["path"]} differs from the private OUTCOME-UNITS.json')
            pu = U.readJson(full)
            hexk = U.keyHex(pu['arbiterKey'])
            src = {'repo': 'arbiter-prediction-prereg (private)', 'file': e['path'], 'sha256': e['sha256'], 'commit': e['commit']}
            if pu['unit'] == 'predicted post outcome':
                u = {k: pu[k] for k in ('unit', 'issueDay', 'targetDay', 'region', 'arbiterKey', 'title', 'optionId', 'seat', 'model',
                                        'chance', 'made', 'decoyMade', 'brier', 'judge', 'onTarget')}
                u['sentence'], u['redacted'] = U.redactHandles(pu['publicSentence'], handles)
                u['derivedFrom'] = src
                msg = (f'score {day} {u["region"]} "{U.short(u["title"], 40)}" {u["optionId"]} ({u["seat"]}): '
                       f'{"made" if u["made"] else "not made"}, chance {u["chance"]:.2f}, Brier {u["brier"]:.3f}')
                out.append((f'outcomes/{day}/posts/{u["region"]}/{hexk}/{u["optionId"]}.json', u, msg))
            elif pu['unit'] == 'account outcome':
                u = {k: pu[k] for k in ('unit', 'issueDay', 'targetDay', 'region', 'arbiterKey', 'title', 'accountRef', 'public',
                                        'chances', 'posted', 'brier', 'onTarget')}
                u['derivedFrom'] = src
                ref = u['accountRef'] if u['public'] else u['accountRef'][:19]
                bs = ', '.join(f'{m} {b:.3f}' for m, b in u['brier'].items() if b is not None)
                msg = f'score {day} {u["region"]} "{U.short(u["title"], 40)}" account {ref}: {"posted" if u["posted"] else "did not post"}; Brier {bs}'
                out.append((f'outcomes/{day}/accounts/{u["region"]}/{hexk}/{U.refFile(u["accountRef"])}.json', u, msg))
    return out


# ---------------------------------------------------------------- outcome scoring records

def scoringFor(day: str, research: str, issueDays: List[str]) -> Dict[str, dict]:
    """How each issue's forecast of the day is scored: by key, through the bridge, or not scored.

    @param day: target day.
    @param research: research repo root.
    @param issueDays: issues that forecast the day.
    @returns: {issueDay: {'kind', 'mapping', 'rule', 'source'}}; an issue with no research file yet is left out.
    @throws OSError: when a research file is unreadable.
    """
    import export_outcome as EO  # noqa: PLC0415  (pandas; only the outcome side needs it)
    out: Dict[str, dict] = {}
    if day == '2026-09-28':
        return {i: {'kind': 'by key', 'mapping': None, 'rule': None, 'source': f'{EO.TOURNAMENT}/results/live/score_{day}_h2_K0.json'}
                for i in issueDays}
    if day == '2026-09-30':
        rel = f'{EO.TOURNAMENT}/results/live/bridge_{day}.json'
        br = EO.loadJson(os.path.join(research, rel))
        mp = {m['narrativeKey']: m for m in br['mapping']}
        return {i: {'kind': 'bridged', 'mapping': mp, 'rule': br['rule'], 'source': rel} for i in issueDays}
    for f in EO.forwardScoreFiles(day, research):
        i = f['issue']['issueDay']
        if f['kind'] == 'by key':
            out[i] = {'kind': 'by key', 'mapping': None, 'rule': None, 'source': f['sources'][0]}
        elif f['kind'] == 'bridged':
            out[i] = {'kind': 'bridged', 'mapping': {m['narrativeKey']: m for m in f['bridge']['mapping']},
                      'rule': f['bridge'].get('rule'), 'source': f['sources'][-1]}
        else:
            out[i] = {'kind': 'not scored', 'mapping': None, 'rule': None, 'source': f['sources'][0]}
    return out


def outcomeUnits(day: str, research: str, private: Optional[str]) -> List[Tuple[str, dict, str]]:
    """Every outcome unit of a target day.

    @param day: target day.
    @param research: research repo root.
    @param private: private repo root, or None.
    @returns: [(path, unit, message)] in commit order.
    @throws Refused: when no issue forecast the day or no research score exists yet.
    """
    import export_outcome as EO  # noqa: PLC0415
    import pandas as pd  # noqa: PLC0415
    iss = EO.issuesFor(day)
    if not iss:
        raise Refused(f'no issue in this ledger forecasts {day}')
    scoring = scoringFor(day, research, [i['issueDay'] for i in iss])
    if not scoring:
        raise Refused(f'no research score file for {day} yet (run fwd_score.py first)')
    datas = [U.loadIssue(LEDGER, i, research) for i in sorted(scoring)]
    extras = {d.issueDay: U.fwdTweetsKeys(research, d.issueDay) for d in datas}
    actual = EO.actualCounts(research, day)
    meta = pd.read_parquet(os.path.join(research, EO.LIVE_DATA, 'nk_meta.parquet'))
    cats = U.outcomeCategoryUnits(day, datas, scoring, actual, meta)
    nars = U.outcomeNarrativeUnits(day, datas, extras, scoring, actual, meta)
    linkIssueUnits(cats + nars)
    return cats + nars + fwdOutcomeUnits(day, private, privateHandles(research))


def linkIssueUnits(units: List[Tuple[str, dict, str]]) -> None:
    """Fill each outcome entry's issueUnit with the issue unit path and commit, where the issue has units.

    @param units: outcome units (changed in place).
    @returns: None.
    @throws: nothing.
    """
    cache: Dict[str, Dict[str, dict]] = {}
    for path, u, _ in units:
        for e in u.get('byIssue', []):
            i = e['issueDay']
            if i not in cache:
                p = os.path.join(LEDGER, 'issues', i, 'UNITS.json')
                cache[i] = {x['path']: x for x in U.readJson(p)['units']} if os.path.exists(p) else {}
            want = None
            if u['unit'] == 'narrative outcome':
                want = f'issues/{i}/narratives/{u["region"]}/{U.slug(u["category"])}/{U.keyHex(u["arbiterKey"])}.json'
                if want not in cache[i]:
                    want = next((p for p in cache[i] if p.startswith(f'issues/{i}/narratives/{u["region"]}/') and p.endswith(f'/{U.keyHex(u["arbiterKey"])}.json')), None)
            else:
                want = f'issues/{i}/categories/{u["region"]}/{U.slug(u["category"])}.json'
            if want and want in cache[i]:
                e['issueUnit'] = {'path': want, 'commit': cache[i][want]['commit']}


# ---------------------------------------------------------------- commits and the index

def committedSame(ledger: str, path: str, text: str) -> bool:
    """Whether HEAD already holds the path with exactly this content.

    @param ledger: repo root.
    @param path: path relative to the repo.
    @param text: expected content.
    @returns: True when committed and identical.
    @throws: nothing.
    """
    r = subprocess.run(['git', '-C', ledger, 'show', f'HEAD:{path}'], capture_output=True, text=True)
    return r.returncode == 0 and r.stdout == text


def commitUnits(ledger: str, units: List[Tuple[str, dict, str]]) -> int:
    """Write and commit each unit on its own.

    @param ledger: repo root.
    @param units: [(path, unit, message)] in commit order.
    @returns: number of new commits.
    @throws Refused: when a unit exists with other content, or git fails.
    """
    made = 0
    for path, u, msg in units:
        text = U.dumps(u)
        if committedSame(ledger, path, text):
            continue
        try:
            U.writeUnits(ledger, [(path, u, msg)])
        except FileExistsError as e:
            raise Refused(str(e)) from e
        git(ledger, 'add', '--', path)
        git(ledger, 'commit', '-q', '-m', msg, '--', path)
        made += 1
    return made


def indexObject(ledger: str, kind: str, day: str, units: List[Tuple[str, dict, str]]) -> dict:
    """UNITS.json (or OUTCOMES-UNITS.json) content: every unit path with its sha256 and commit.

    @param ledger: repo root.
    @param kind: 'issue' or 'outcome'.
    @param day: issue or target day.
    @param units: [(path, unit, message)] in commit order.
    @returns: the index object.
    @throws Refused: when a unit is not committed.
    """
    rows, counts = [], {}
    for path, _, _ in units:
        c = git(ledger, 'log', '-1', '--format=%H', '--', path)
        if not c:
            raise Refused(f'{path} is not committed')
        k = U.unitKindOf(path)
        counts[k] = counts.get(k, 0) + 1
        rows.append({'path': path, 'unit': k, 'sha256': U.sha256File(os.path.join(ledger, path)), 'commit': c})
    key = 'issueDay' if kind == 'issue' else 'day'
    return {key: day, 'kind': f'{kind} units', 'counts': counts, 'units': rows,
            'note': ('One commit per unit, in this order. The OpenTimestamps proof of this file shows every unit existed '
                     'by its Bitcoin block time; the push record on GitHub is the second clock.')}


def writeIndex(ledger: str, kind: str, day: str, units: List[Tuple[str, dict, str]], ots: Optional[str],
               views: bool = True) -> str:
    """Write, stamp and commit the unit index, with the regenerated views in the same commit.

    @param ledger: repo root.
    @param kind: 'issue' or 'outcome'.
    @param day: issue or target day.
    @param units: [(path, unit, message)].
    @param ots: path of the ots client, or None to skip the stamp.
    @param views: regenerate by-narrative/, by-category/ and SCOREBOARD.md.
    @returns: the index path relative to the repo.
    @throws Refused: when the index exists with other content.
    """
    folder = f'issues/{day}' if kind == 'issue' else f'outcomes/{day}'
    name = 'UNITS.json' if kind == 'issue' else 'OUTCOMES-UNITS.json'
    rel = f'{folder}/{name}'
    obj = indexObject(ledger, kind, day, units)
    text = U.dumps(obj)
    full = os.path.join(ledger, rel)
    if os.path.exists(full):
        if open(full, encoding='utf-8').read() != text:
            raise Refused(f'{rel} exists with other content; an index is never rewritten')
    else:
        with open(full, 'w', encoding='utf-8') as fh:
            fh.write(text)
    paths = [rel]
    if ots and os.path.exists(ots) and not os.path.exists(full + '.ots'):
        r = subprocess.run([ots, 'stamp', full], capture_output=True, text=True)
        if r.returncode != 0:
            print(f'note: ots stamp failed ({r.stderr.strip()[:200]}); stamp {rel} before the target day', file=sys.stderr)
    if os.path.exists(full + '.ots'):
        paths.append(rel + '.ots')
    if views:
        import views as Vw  # noqa: PLC0415
        paths += Vw.writeViews(ledger)
    for p in paths:
        git(ledger, 'add', '--', p)
    if git(ledger, 'diff', '--cached', '--name-only', '--', *paths):
        c = obj['counts']
        what = ', '.join(f'{n} {k}' for k, n in sorted(c.items(), key=lambda kv: COMMIT_ORDER.get(kv[0], 9)))
        git(ledger, 'commit', '-q', '-m',
            f'{"units" if kind == "issue" else "outcome units"} {day}: index of {sum(c.values())} unit commits ({what}), '
            f'sha256 {U.sha256File(full)[:16]}, OpenTimestamps stamp' + (' and views' if views else ''), '--', *paths)
    return rel


def hourlyUnits(day: str, hour: int, table: str) -> List[Tuple[str, dict, str]]:
    """Hourly units from a table of hourly forecast objects.

    @param day: UTC day.
    @param hour: UTC hour, 0 to 23.
    @param table: JSON file holding a list of hourly objects, each with region, narrative.key or candidateId, and asOf.
    @returns: [(path, unit, message)] in table order.
    @throws Refused: when an object is for another hour or lacks a key.
    """
    rows = U.readJson(table)
    rows = rows['forecasts'] if isinstance(rows, dict) else rows
    sha = U.sha256File(table)
    want = f'{day}T{hour:02d}:00'
    out = []
    for i, o in enumerate(rows):
        nar = o.get('narrative') or {}
        key = nar.get('key') or nar.get('candidateId')
        if not key or not o.get('region'):
            raise Refused(f'hourly object {i} has no region or key')
        if not str(o.get('asOf', '')).startswith(want):
            raise Refused(f'hourly object {i} is as of {o.get("asOf")}, not {want}')
        u = {'unit': 'hourly forecast', 'day': day, 'hour': hour, **o,
             'derivedFrom': {'file': os.path.basename(table), 'sha256': sha, 'row': i}}
        vol = o.get('volume') or {}
        msg = (f'predict {day} {hour:02d}:00 {o["region"]} "{U.short(nar.get("title"), 50)}": by end of day '
               f'{U.fmt(vol.get("forecastEndOfDay"))} posts')
        out.append((f'hourly/{day}/{hour:02d}/{o["region"]}/{U.keyHex(str(key))}.json', u, msg))
    return out


def runHourly(day: str, hour: int, table: str, commit: bool, ots: Optional[str], research: Optional[str],
              now: Optional[dt.datetime] = None) -> int:
    """Commit the units of one hourly issue, then hourly/<day>/<HH>/UNITS.json and its stamp.

    @param day: UTC day.
    @param hour: UTC hour.
    @param table: hourly forecast table.
    @param commit: write and commit.
    @param ots: ots client.
    @param research: research repo root (private handle list).
    @param now: the clock (tests pass one); default the system clock in UTC.
    @returns: exit code.
    @throws Refused: once the hour has begun.
    """
    start = dt.datetime.fromisoformat(f'{day}T{hour:02d}:00:00+00:00')
    if (now or dt.datetime.now(dt.timezone.utc)) >= start:
        raise Refused(f'the hour {day} {hour:02d}:00 UTC has begun; an hourly issue committed now is void')
    units = hourlyUnits(day, hour, table)
    scanUnits(units, privateHandles(research))
    if not commit:
        for p, _, m in units:
            print(f'{p}\t{m}')
        return 0
    commitUnits(LEDGER, units)
    rel = f'hourly/{day}/{hour:02d}/UNITS.json'
    obj = indexObject(LEDGER, 'hourly', f'{day}T{hour:02d}', units)
    full = os.path.join(LEDGER, rel)
    with open(full, 'w', encoding='utf-8') as fh:
        fh.write(U.dumps(obj))
    paths = [rel]
    if ots and os.path.exists(ots):
        subprocess.run([ots, 'stamp', full], capture_output=True)
        if os.path.exists(full + '.ots'):
            paths.append(rel + '.ots')
    git(LEDGER, 'add', '--', *paths)
    git(LEDGER, 'commit', '-q', '-m', f'hourly {day} {hour:02d}:00: index of {len(units)} unit commits, OpenTimestamps stamp', '--', *paths)
    print(f'{len(units)} hourly units for {day} {hour:02d}:00; push now, before the hour begins: git -C {LEDGER} push origin main')
    return 0


def issueUnits(issueDay: str, research: Optional[str], private: Optional[str]) -> List[Tuple[str, dict, str]]:
    """Every unit of an issue in commit order.

    @param issueDay: issue day.
    @param research: research repo root (category map and FWD-TWEETS-1 keys).
    @param private: private repo root (predicted posts and accounts).
    @returns: [(path, unit, message)].
    @throws ValueError: when a frozen input's hash differs.
    """
    data = U.loadIssue(LEDGER, issueDay, research)
    units = U.buildIssueUnits(data, U.fwdTweetsKeys(research, issueDay))
    return units + fwdIssueUnits(issueDay, private, privateHandles(research))


def run(kind: str, day: str, research: str, private: Optional[str], commit: bool, ots: Optional[str]) -> int:
    """Build, scan, and (with commit) write and commit the units of an issue or a scored day.

    @param kind: 'issue' or 'outcome'.
    @param day: issue or target day.
    @param research: research repo root.
    @param private: private repo root.
    @param commit: write and commit; otherwise only list what would be committed.
    @param ots: ots client path.
    @returns: exit code.
    @throws Refused: on a failed check (caught by main).
    """
    if kind == 'issue':
        if not os.path.exists(os.path.join(LEDGER, 'issues', day, 'FREEZE.json')):
            raise Refused(f'issues/{day} is not exported; run tools/ledger-publish.sh first')
        units = issueUnits(day, research, private)
    else:
        if not os.path.exists(os.path.join(LEDGER, 'outcomes', day, 'score.json')):
            raise Refused(f'outcomes/{day} is not exported; run tools/ledger-score.sh first')
        units = outcomeUnits(day, research, private)
    units.sort(key=lambda x: COMMIT_ORDER[U.unitKindOf(x[0])])
    scanUnits(units, privateHandles(research))
    counts: Dict[str, int] = {}
    for p, _, _ in units:
        counts[U.unitKindOf(p)] = counts.get(U.unitKindOf(p), 0) + 1
    if not commit:
        for p, _, m in units:
            print(f'{p}\t{m}')
        print(f'{len(units)} units: {counts}', file=sys.stderr)
        return 0
    made = commitUnits(LEDGER, units)
    rel = writeIndex(LEDGER, kind, day, units, ots)
    print(f'{made} new unit commits for {kind} {day} ({counts}); index {rel}')
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """Command-line entry.

    @param argv: arguments (defaults to sys.argv).
    @returns: exit code.
    @throws SystemExit: on bad arguments.
    """
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('kind', choices=('issue', 'outcome', 'hourly'))
    ap.add_argument('day')
    ap.add_argument('--hour', type=int, default=None)
    ap.add_argument('--table', default=None)
    ap.add_argument('--research', default=DEFAULT_RESEARCH)
    ap.add_argument('--private', default=DEFAULT_PRIVATE)
    ap.add_argument('--commit', action='store_true')
    ap.add_argument('--ots', default=DEFAULT_OTS)
    ap.add_argument('--force-early', action='store_true', help='allow an issue before 2026-10-04 (dry runs only)')
    a = ap.parse_args(argv)
    if a.kind == 'hourly':
        if a.hour is None or not a.table:
            print('refused: hourly needs --hour and --table')
            return 2
        try:
            return runHourly(a.day, a.hour, os.path.abspath(a.table), a.commit, a.ots, os.path.abspath(a.research))
        except (Refused, ValueError, KeyError, FileNotFoundError) as e:
            print(f'refused: {e}')
            return 2
    first = U.UNITS_FROM_ISSUE if a.kind == 'issue' else U.UNITS_FROM_OUTCOME
    if a.day < first and not a.force_early:
        print(f'refused: {a.kind}s before {first} were published as single commits and are not re-exported')
        return 2
    try:
        dt.date.fromisoformat(a.day)
        return run(a.kind, a.day, os.path.abspath(a.research), os.path.abspath(a.private) if a.private else None, a.commit, a.ots)
    except (Refused, ValueError, KeyError, FileNotFoundError) as e:
        print(f'refused: {e}')
        return 2


if __name__ == '__main__':
    sys.exit(main())
