"""Export what happened on one target day into outcomes/<day>/ from the research repo's score files.

stories.json gives, for every issue that forecast the day, the headline model's top 10 per region
beside the actual post count Arbiter's reading of the day assigns to each story, and the day's
actual top 10. score.json gives each model's log error and top-10 hits against the references
(yesterday's count, every story dies), copied from the research score file with that file's
sha256, plus the GitHub push time of the issue in this ledger.

Three kinds of day:
  2026-09-28  scored by story key (pilot)
  2026-09-30  scored through the post-overlap bridge, because Arbiter renamed every story that day;
              every number carries "bridged" and the share of the forecast that could not be matched
  later days  scored by key by the forward block's fwd_score.py (results/issue-<I>/score_<day>_h<h>_T<K>.json)

Usage: <python with pandas> tools/export_outcome.py <day> [--research <path>] [--force] [--offline]
Exit codes: 0 written, 2 refused (no score yet, source uncommitted, or folder exists).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import subprocess
import sys
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import export_issue as EI  # noqa: E402
import verify as V  # noqa: E402

LEDGER = EI.LEDGER
TOURNAMENT = 'cleanroom/2026-09-28-tomorrow-tournament'
FORWARD = 'cleanroom/2026-10-01-tomorrow-forward'
LIVE_DATA = 'data/tomorrow_tournament/live'
REGIONS = ('US', 'IN')
# Models the page and the ledger report, with plain names; the score files hold more.
NAMES = {'gbm': 'boosted trees', 'gbm_med': 'boosted trees (median)', 'persist': "yesterday's count",
         'zero': 'every story dies', 'volols': 'one-variable volume regression', 'ewma': 'exponential smoothing',
         'holt_damped': 'damped Holt'}
REFERENCES = ('persist', 'zero')
# Private research-repo push times for the copied legacy issues (PROOF-OF-TIMING.md in the research repo).
RESEARCH_PUSH = {'2026-09-27': ('2026-09-27T21:19:29Z', 'ad25f9d'), '2026-09-29': ('2026-09-29T04:34:46Z', '8d628db'),
                 '2026-10-02': ('2026-10-02T07:02:18Z', '79a7df4')}
NO_POSTS = ('posts.json is absent. Real posts by named accounts are published from the outcome of issue 2026-10-03 on, '
            'beside the predicted posts they are scored against.')


def loadJson(path: str) -> dict:
    """Read a research JSON file that may hold bare NaN values.

    @param path: file path.
    @returns: parsed object with NaN read as None.
    @throws OSError: when unreadable.
    @throws ValueError: when not JSON.
    """
    return json.loads(re.sub(r'\bNaN\b', 'null', open(path, encoding='utf-8').read()))


def num(x: Optional[float], nd: int = 3) -> Optional[float]:
    """Round a number for publication, keeping None for missing values.

    @param x: value.
    @param nd: decimals.
    @returns: rounded float or None.
    @throws: nothing.
    """
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    return round(float(x), nd)


def actualCounts(research: str, day: str, K: int = 0) -> pd.Series:
    """Posts on a day per Arbiter key, as Arbiter's readings through day + K assign them.

    This is the research scorers' target (tn_lib.target): the vintage counts with publication day
    equal to the day and reading day at most day + K.

    @param research: research repo root.
    @param day: target day.
    @param K: readings after the day (0 or 6).
    @returns: posts indexed by narrative key.
    @throws OSError: when the live tables are absent.
    """
    v = pd.read_parquet(os.path.join(research, LIVE_DATA, 'vint.parquet'))
    meta = pd.read_parquet(os.path.join(research, LIVE_DATA, 'nk_meta.parquet')).drop_duplicates('nk').set_index('nk')
    D = (pd.Timestamp(day) - pd.Timestamp('2026-01-01')).days
    y = v[(v['pday'] == D) & (v['f'] <= D + K)].groupby('nk')['n'].sum()
    keys = meta['narrativeKey'].reindex(y.index)
    return pd.Series(y.values, index=keys.values).groupby(level=0).sum()


def issuesFor(day: str) -> List[dict]:
    """Ledger issues whose forecast targets the day.

    @param day: target day.
    @returns: list of {'issueDay', 'horizon', 'forecast'} sorted by issue day.
    @throws OSError: when an issue folder is unreadable.
    """
    out = []
    root = os.path.join(LEDGER, 'issues')
    for d in sorted(os.listdir(root)):
        p = os.path.join(root, d, 'forecast.json')
        if not os.path.exists(p):
            continue
        fc = json.load(open(p, encoding='utf-8'))
        for h in fc['horizons']:
            if h['targetDay'] == day:
                out.append({'issueDay': d, 'horizon': h, 'forecast': fc})
    return out


def pushRecord(issueDay: str, offline: bool) -> dict:
    """GitHub's receipt time of the commit that added the issue's FREEZE.json, plus the research record.

    @param issueDay: ledger issue day.
    @param offline: skip the network.
    @returns: timing fields for score.json.
    @throws: nothing; a lookup failure is recorded as text.
    """
    rec: Dict[str, Optional[str]] = {'ledgerCommit': V.addingCommit(f'issues/{issueDay}/FREEZE.json'), 'ledgerPushUtc': None}
    if not offline and rec['ledgerCommit']:
        try:
            pushes, source = V.githubPushes(V.REPO)
            t = V.pushTimeOf(rec['ledgerCommit'], pushes)
            rec['ledgerPushUtc'] = V.iso(t) if t else None
            rec['ledgerPushSource'] = f'https://api.github.com/repos/{V.REPO}/activity ({source})'
        except RuntimeError as e:
            rec['ledgerPushSource'] = f'unavailable: {e}'
    if issueDay in RESEARCH_PUSH:
        rec['researchPushUtc'], rec['researchCommit'] = RESEARCH_PUSH[issueDay]
        rec['researchPushNote'] = 'Push to the private research repo, recorded from its GitHub events; an outsider cannot check it.'
    return rec


def storyRows(top: List[dict], actual: pd.Series) -> List[dict]:
    """Forecast top 10 of one region beside the actual counts, by key.

    @param top: forecast.json rows.
    @param actual: posts by key.
    @returns: rows with the actual count and whether it fell in the 80 percent range.
    @throws: nothing.
    """
    rows = []
    for r in top:
        a = int(actual.get(r['arbiterKey'], 0))
        rows.append({**r, 'actual': a, 'inRange': bool(r['lo'] - 1e-9 <= a <= r['hi'] + 1e-9)})
    return rows


def actualTop(score: dict) -> Dict[str, List[dict]]:
    """The day's actual top 10 per region from a research score file.

    @param score: research score object holding actualTop10.
    @returns: {region: [{rank, title, arbiterKey, posts, forecastListed}]}.
    @throws KeyError: when actualTop10 is absent.
    """
    out = {}
    for R in REGIONS:
        out[R] = [{'rank': i + 1, 'title': t.get('title'), 'arbiterKey': t.get('narrativeKey'), 'posts': int(t['posts']),
                   'liveAtOrigin': bool(t.get('liveAtOrigin'))} for i, t in enumerate(score['actualTop10'][R])]
    return out


def computedTop(research: str, actual: pd.Series, matched: set) -> Dict[str, List[dict]]:
    """The day's actual top 10 per region, computed from the live tables (non-hub stories only).

    @param research: research repo root.
    @param actual: posts by key for the day.
    @param matched: keys of the day that a forecast story was bridged to.
    @returns: {region: [{rank, title, arbiterKey, posts, bridgedFromForecast, label}]}.
    @throws OSError: when nk_meta is absent.
    """
    meta = pd.read_parquet(os.path.join(research, LIVE_DATA, 'nk_meta.parquet')).drop_duplicates('narrativeKey').set_index('narrativeKey')
    out = {}
    for R in REGIONS:
        keys = [k for k in actual.index if k in meta.index and meta.at[k, 'region'] == R and not bool(meta.at[k, 'hub']) and actual[k] > 0]
        top = sorted(keys, key=lambda k: (-int(actual[k]), k))[:10]
        out[R] = [{'rank': i + 1, 'title': meta.at[k, 'title'], 'arbiterKey': k, 'posts': int(actual[k]),
                   'bridgedFromForecast': k in matched, 'label': 'actual 30 Sep story (new key); bridged comparison only'}
                  for i, k in enumerate(top)]
    return out


def metric(label: str, value: Optional[float], **extra: object) -> dict:
    """One published number with its label.

    @param label: plain-words label.
    @param value: number.
    @param extra: further fields (e.g. the unmatched share).
    @returns: {'label', 'value', ...}.
    @throws: nothing.
    """
    return {'label': label, 'value': value, **extra}


def legacyByKey(day: str, research: str) -> tuple:
    """Stories and score for 28 Sep, scored by key.

    @param day: '2026-09-28'.
    @param research: research repo root.
    @returns: (stories, score, source files).
    @throws OSError: when a source file is absent.
    """
    rel = f'{TOURNAMENT}/results/live/score_{day}_h2_K0.json'
    sc = loadJson(os.path.join(research, rel))
    actual = actualCounts(research, day)
    iss = issuesFor(day)
    stories = {'day': day, 'kind': 'by key', 'reading': 'T0: posts published on the day that Arbiter\'s reading of the day assigns to the story',
               'forecasts': [{'issueDay': i['issueDay'], 'horizonDays': i['horizon']['horizonDays'], 'model': i['forecast']['model'],
                              'regions': {R: storyRows(i['horizon']['regions'][R]['top'], actual) for R in REGIONS}} for i in iss],
               'actualTop10': actualTop(sc)}
    pr = pd.read_parquet(os.path.join(research, f'{TOURNAMENT}/forecasts/pilot-2026-09-28/pred_{day}_h2_K0.parquet'))
    g = pr[pr['model'] == 'gbm'].copy()
    if 'narrativeKey' not in g.columns:
        meta = pd.read_parquet(os.path.join(research, LIVE_DATA, 'nk_meta.parquet')).drop_duplicates('nk').set_index('nk')
        g['narrativeKey'] = meta['narrativeKey'].reindex(g['nk'].values).values
    g['y'] = actual.reindex(g['narrativeKey'].values).fillna(0).values
    zero = {'all': float(np.mean(np.log1p(g['y']))), **{R: float(np.mean(np.log1p(g.loc[g['region'] == R, 'y']))) for R in REGIONS}}
    models = {}
    for name, m in sc['models'].items():
        hits = {d['region']: d['hits'] for d in m.get('perDay', [])}
        models[name] = {'name': NAMES.get(name, name),
                        'logError': metric('log error, all stories', num(m.get('male'))),
                        'logErrorUS': metric('log error, US', num(m.get('male_US'))),
                        'logErrorIN': metric('log error, India', num(m.get('male_IN'))),
                        'top10HitsUS': metric("real top-10 stories found, US", hits.get('US')),
                        'top10HitsIN': metric("real top-10 stories found, India", hits.get('IN')),
                        'cover80': metric('share of actuals inside the 80 percent range', num(m.get('cover80'))),
                        'rows': m.get('rows')}
    models['zero'] = {'name': NAMES['zero'],
                      'logError': metric('log error, all stories', num(zero['all'])),
                      'logErrorUS': metric('log error, US', num(zero['US'])),
                      'logErrorIN': metric('log error, India', num(zero['IN'])),
                      'top10HitsUS': metric('real top-10 stories found, US', None),
                      'top10HitsIN': metric('real top-10 stories found, India', None),
                      'rows': int(len(g)),
                      'note': 'Computed by tools/export_outcome.py over the same rows; the research score file has no zero row. It ranks nothing, so it finds no top-10 story.'}
    score = {'day': day, 'kind': 'by key', 'horizonDays': 2, 'reading': 'T0',
             'logErrorDefinition': 'mean |log(1 + actual) - log(1 + forecast)| over every story the forecast listed; lower is better',
             'headline': 'gbm', 'references': list(REFERENCES), 'models': models,
             'newStoryShare': sc.get('newShare'),
             'notes': ['The US half of this day is degraded: US collection fell from 24 to 28 September, and the day holds 4,486 posts in US stories against 29,183 in India.']}
    return stories, score, [rel, f'{TOURNAMENT}/forecasts/pilot-2026-09-28/pred_{day}_h2_K0.parquet', f'{LIVE_DATA}/vint.parquet', f'{LIVE_DATA}/nk_meta.parquet']


def legacyBridged(day: str, research: str) -> tuple:
    """Stories and score for 30 Sep, scored through the bridge.

    @param day: '2026-09-30'.
    @param research: research repo root.
    @returns: (stories, score, source files).
    @throws OSError: when a source file is absent.
    """
    relS = f'{TOURNAMENT}/results/live/score_bridged_{day}_h2_K0.json'
    relB = f'{TOURNAMENT}/results/live/bridge_{day}.json'
    sc = loadJson(os.path.join(research, relS))['folders']['live-2026-09-30']
    br = loadJson(os.path.join(research, relB))
    mapping = {m['narrativeKey']: m for m in br['mapping']}
    actual = actualCounts(research, day)
    unK = sc['unmatched']['gbm']['unmatchedShareOfKeys']
    iss = issuesFor(day)
    fan = pd.Series([m['bestKey'] for m in br['mapping'] if m['matched']]).value_counts()
    forecasts = []
    for i in iss:
        regions = {}
        for R in REGIONS:
            rows = []
            for r in i['horizon']['regions'][R]['top']:
                m = mapping.get(r['arbiterKey'])
                row = dict(r)
                if m and m['matched']:
                    a = int(actual.get(m['bestKey'], 0))
                    row.update({'bridged': True, 'bridgedTo': m['bestKey'], 'bridgeShare': num(m['bestShare']),
                                'storiesSharingTarget': int(fan.get(m['bestKey'], 1)),
                                'actualBridged': a, 'label': 'bridged actual: posts on 30 Sep in the story that holds most of this story\'s 28 Sep posts'})
                else:
                    row.update({'bridged': False, 'bridgedTo': None, 'actualBridged': None,
                                'label': 'unmatched by the bridge: no 30 Sep story holds half of its 28 Sep posts, so it is not scored'})
                rows.append(row)
            regions[R] = rows
        forecasts.append({'issueDay': i['issueDay'], 'horizonDays': i['horizon']['horizonDays'], 'model': i['forecast']['model'], 'regions': regions})
    stories = {'day': day, 'kind': 'bridged',
               'reading': 'T0, bridged: Arbiter renamed every story on 30 September, so each forecast story is followed to the 30 Sep story holding at least half of its 28 Sep posts',
               'bridgeRule': br['rule'], 'unmatchedShareOfKeys': num(unK),
               'forecasts': forecasts,
               'actualTop10': computedTop(research, actual, {m['bestKey'] for m in br['mapping'] if m['matched']})}
    models = {}
    for name, m in sc['models'].items():
        un = sc['unmatched'][name]
        sP, sK = num(un.get('unmatchedShareOfPredicted')), num(un.get('unmatchedShareOfKeys'))
        tag = (f'bridged, {round(100 * sP, 1)} percent of its predicted posts unmatched' if sP is not None
               else f'bridged, {round(100 * sK, 1)} percent of keys unmatched')
        hits = {d['region']: d['hits'] for d in m.get('perDay', [])}
        byR = {R: num(un['byRegion'][R].get('unmatchedShareOfPredicted')) for R in REGIONS}
        voidIN = name == 'nb_glm'
        models[name] = {'name': NAMES.get(name, name),
                        'logError': metric(f'bridged log error, matched stories ({tag})', num(m.get('male')), unmatchedShareOfPredicted=sP, unmatchedShareOfKeys=sK),
                        'logErrorUS': metric(f'bridged log error, US ({tag})', num(m.get('male_US')), unmatchedShareOfPredicted=byR['US'], unmatchedShareOfKeys=sK),
                        'logErrorIN': metric(f'bridged log error, India ({tag})', num(m.get('male_IN')), unmatchedShareOfPredicted=byR['IN'], unmatchedShareOfKeys=sK),
                        'top10HitsUS': metric(f'bridged real top-10 stories found, US, at most 4 ({tag})',
                                              None if name == 'zero' else hits.get('US'), unmatchedShareOfKeys=sK),
                        'top10HitsIN': metric(f'bridged real top-10 stories found, India, at most 4 ({tag})',
                                              None if (name == 'zero' or voidIN) else hits.get('IN'), unmatchedShareOfKeys=sK,
                                              **({'void': 'ties at zero fill this top 10 (audit F4)'} if voidIN else {})),
                        'rows': m.get('rows'), 'rowsMerged': m.get('rowsMerged')}
    score = {'day': day, 'kind': 'bridged', 'horizonDays': 2, 'reading': 'T0, bridged',
             'logErrorDefinition': 'mean |log(1 + actual) - log(1 + forecast)| over the matched 30 Sep stories; lower is better',
             'headline': 'gbm', 'references': list(REFERENCES), 'models': models,
             'bridge': {'keysMatched': br['keysMatched'], 'forecastKeysUnmatched': sc['unmatched']['gbm']['keysUnmatched'],
                        'forecastKeys': sc['unmatched']['gbm']['keys'], 'unmatchedShareOfKeys': num(unK),
                        'dayPostsCoveredByMatchedStories': sc['dayPostsCoveredByMatchedTargets']},
             'notes': ['Bridged numbers cover the stories that kept their posts across the rename, a selected slice of the day, and are not comparable with a by-key score.',
                       'Only 4 of each region\'s real top 10 are matched stories, so bridged top-10 hits are capped at 4.',
                       'Forecasts of 102 unmatched stories whose posts landed in a matched story are dropped, which biases bridged forecasts low; adding them back lowers every model\'s error by about 0.04 to 0.06 and leaves the order unchanged.',
                       'Coverage of the 80 percent range and the area-share comparison are not reported for this day.']}
    return stories, score, [relS, relB, f'{LIVE_DATA}/vint.parquet', f'{LIVE_DATA}/nk_meta.parquet']


def forwardByKey(day: str, research: str) -> Optional[tuple]:
    """Stories and score for a day scored by the forward block.

    @param day: target day.
    @param research: research repo root.
    @returns: (stories, score, source files), or None when no score file exists yet.
    @throws OSError: when a source file is unreadable.
    """
    iss = issuesFor(day)
    found = []
    for i in iss:
        h = i['horizon']['horizonDays']
        rel = f'{FORWARD}/results/issue-{i["issueDay"]}/score_{day}_h{h}_T0.json'
        if os.path.exists(os.path.join(research, rel)):
            found.append((i, rel, loadJson(os.path.join(research, rel))))
    if not found:
        return None
    actual = actualCounts(research, day)
    stories = {'day': day, 'kind': 'by key', 'reading': 'T0: posts published on the day that Arbiter\'s reading of the day assigns to the story',
               'forecasts': [{'issueDay': i['issueDay'], 'horizonDays': i['horizon']['horizonDays'], 'model': i['forecast']['model'],
                              'regions': {R: storyRows(i['horizon']['regions'][R]['top'], actual) for R in REGIONS}} for i, _, _ in found],
               'actualTop10': actualTop(found[-1][2])}
    per = []
    for i, rel, sc in found:
        models = {}
        for name, m in sc['models'].items():
            models[name] = {'name': NAMES.get(name, name),
                            'logError': metric('log error, all stories', num(m['all']['logErr'])),
                            **{f'logError{R}': metric(f'log error, {"US" if R == "US" else "India"}', num(m.get(R, {}).get('logErr'))) for R in REGIONS},
                            **{f'top10Hits{R}': metric(f'real top-10 stories found, {"US" if R == "US" else "India"}',
                                                       None if name == 'zero' else m.get(R, {}).get('top10All')) for R in REGIONS},
                            'cover80': metric('share of actuals inside the 80 percent range', num(m['all'].get('cover80'))),
                            'rows': m['all'].get('rows')}
        per.append({'issueDay': i['issueDay'], 'horizonDays': i['horizon']['horizonDays'], 'models': models,
                    'newStoryShare': sc.get('newShare'), 'degradedTarget': sc.get('degradedTarget')})
    score = {'day': day, 'kind': 'by key', 'reading': 'T0',
             'logErrorDefinition': 'mean |log(1 + actual) - log(1 + forecast)| over every story live at the origin; lower is better',
             'headline': EI.FORWARD_MODEL, 'references': list(REFERENCES), 'byIssue': per}
    return stories, score, [rel for _, rel, _ in found] + [f'{LIVE_DATA}/vint.parquet', f'{LIVE_DATA}/nk_meta.parquet']


def outcomeReadme(day: str, kind: str) -> str:
    """Plain-prose README for an outcome folder.

    @param day: target day.
    @param kind: 'by key' or 'bridged'.
    @returns: markdown text.
    @throws: nothing.
    """
    lines = [f'# Outcome {day}', '',
             '`stories.json` puts each forecast story beside the number of posts Arbiter\'s reading of the day assigned to it, '
             'and lists the day\'s real top 10 per region. `score.json` scores every model against the references, with the '
             'GitHub push time of the issue that forecast this day. `MANIFEST.json` holds the sha256 of every file here, and '
             '`sources` in it names the research files the numbers came from, with their sha256.', '']
    if kind == 'bridged':
        lines += ['Arbiter renamed every story on 30 September, so no forecast story kept its key. Each forecast story was followed '
                  'to the 30 September story holding at least half of its 28 September posts, a rule fixed before any score was '
                  'computed. Stories with no such match are excluded and counted. Every number here says "bridged" and gives the '
                  'unmatched share beside it.', '']
    lines += [NO_POSTS, '']
    return '\n'.join(lines)


def export(day: str, research: str, force: bool, offline: bool) -> int:
    """Write outcomes/<day>/.

    @param day: target day.
    @param research: research repo root.
    @param force: overwrite an existing folder.
    @param offline: skip the GitHub lookup.
    @returns: exit code.
    @throws OSError: on a file system failure.
    """
    out = os.path.join(LEDGER, 'outcomes', day)
    if os.path.exists(out) and not force:
        print(f'refused: {out} exists (use --force to rewrite it)')
        return 2
    if not issuesFor(day):
        print(f'refused: no issue in this ledger forecasts {day}')
        return 2
    if day == '2026-09-28':
        built = legacyByKey(day, research)
    elif day == '2026-09-30':
        built = legacyBridged(day, research)
    else:
        built = forwardByKey(day, research)
    if built is None:
        print(f'refused: no research score file for {day} yet (run fwd_score.py first)')
        return 2
    stories, score, sources = built
    for rel in sources[:-2]:
        if EI.git(research, 'ls-files', '--error-unmatch', rel).returncode != 0 or EI.git(research, 'diff', '--quiet', 'HEAD', '--', rel).returncode != 0:
            print(f'refused: {rel} is not committed and clean in the research repo')
            return 2
    score['timing'] = {i['issueDay']: {'targetStartsUtc': f'{day}T00:00:00Z', **pushRecord(i['issueDay'], offline)} for i in issuesFor(day)}
    os.makedirs(out, exist_ok=True)
    for name, obj in (('stories.json', stories), ('score.json', score)):
        with open(os.path.join(out, name), 'w', encoding='utf-8') as fh:
            json.dump(obj, fh, indent=1, ensure_ascii=False)
            fh.write('\n')
    with open(os.path.join(out, 'README.md'), 'w', encoding='utf-8') as fh:
        fh.write(outcomeReadme(day, stories['kind']))
    EI.writeManifest(out, {'kind': 'outcome', 'day': day, 'scoring': stories['kind'],
                           'researchHead': EI.git(research, 'rev-parse', 'HEAD').stdout.strip(),
                           'sources': {rel: EI.sha256(os.path.join(research, rel)) for rel in sources}})
    print(f'wrote {os.path.relpath(out, LEDGER)} ({stories["kind"]})')
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """Command-line entry.

    @param argv: arguments (defaults to sys.argv).
    @returns: exit code.
    @throws SystemExit: on bad arguments.
    """
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('day')
    ap.add_argument('--research', default=EI.DEFAULT_RESEARCH)
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--offline', action='store_true')
    a = ap.parse_args(argv)
    return export(a.day, os.path.abspath(a.research), a.force, a.offline)


if __name__ == '__main__':
    sys.exit(main())
