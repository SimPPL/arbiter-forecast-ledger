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
  later days  the forward block's fwd_score.py, one entry per issue that forecast the day (a day can be D+1 of
              one issue and D+2 of the issue before; the entries are kept apart, never averaged):
                results/issue-<I>/score_<day>_h<h>_T0.json          scored by key (amendment 04 scores included)
                results/issue-<I>/rename_<day>_h<h>_T0.json         Arbiter renamed its stories after the origin;
                results/issue-<I>/score_bridged_<day>_h<h>_T0.json  the bridged score, when it exists, with the
                                                                     bridge file its `bridge` field names

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
         'holt_damped': 'damped Holt', 'surv_age': 'age-conditioned survival',
         'tsb': 'Teunter-Syntetos-Babai activity probability', 'hurdle_nb': 'hurdle negative binomial'}
REFERENCES = ('persist', 'zero', 'volols')
# The two tournament days were scored against these two only.
LEGACY_REFERENCES = ('persist', 'zero')
CELLS = ('all',) + REGIONS
CELL_WORDS = {'all': 'all stories', 'US': 'US', 'IN': 'India'}
# Amendment 04 scores, in the order they are reported: every one comes before log error.
DISTRIBUTIONAL = (('rps', 'ranked probability score'), ('brierActive', 'Brier score on any post'),
                  ('logErrGivenActive', 'log error on stories that got a post'))
# What each fitted reference is scored on (amendment 04, section 2); surv_age and tsb give only P(any post).
REFERENCE_SCORES = {'surv_age': ('brierActive',), 'tsb': ('brierActive',),
                    'hurdle_nb': ('rps', 'brierActive', 'logErrGivenActive', 'logErr')}
CLIMATOLOGY = ('every story dies forecasts no posts, but its predictive distribution is the spread of the calibration '
               'days, a climatology, so these scores describe that climatology and not "every story dies"')
SCORE_DEFINITIONS = {
    'activeRows': 'stories with at least one post on the day',
    'rps': 'ranked probability score of the post count under the model\'s predictive distribution; lower is better',
    'brierActive': '(P(at least one post) - 1 if the story got a post, else 0) squared, averaged; lower is better',
    'logErrGivenActive': 'over stories with at least one post, |log(1 + actual) - log(1 + median given a post)|; lower is better',
    'logError': 'mean |log(1 + actual) - log(1 + forecast)| over every story live at the origin; lower is better',
    'climatology': CLIMATOLOGY,
}
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


def computedTop(research: str, actual: pd.Series, matched: set, label: str) -> Dict[str, List[dict]]:
    """The day's actual top 10 per region, computed from the live tables (non-hub stories only).

    @param research: research repo root.
    @param actual: posts by key for the day.
    @param matched: keys of the day that a forecast story was bridged to.
    @param label: what each row is, in plain words.
    @returns: {region: [{rank, title, arbiterKey, posts, bridgedFromForecast, label}]}.
    @throws OSError: when nk_meta is absent.
    """
    meta = pd.read_parquet(os.path.join(research, LIVE_DATA, 'nk_meta.parquet')).drop_duplicates('narrativeKey').set_index('narrativeKey')
    out = {}
    for R in REGIONS:
        keys = [k for k in actual.index if k in meta.index and meta.at[k, 'region'] == R and not bool(meta.at[k, 'hub']) and actual[k] > 0]
        top = sorted(keys, key=lambda k: (-int(actual[k]), k))[:10]
        out[R] = [{'rank': i + 1, 'title': meta.at[k, 'title'], 'arbiterKey': k, 'posts': int(actual[k]),
                   'bridgedFromForecast': k in matched, 'label': label}
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


def shortDay(day: str) -> str:
    """A day as "30 Sep".

    @param day: YYYY-MM-DD.
    @returns: day of month and abbreviated month.
    @throws ValueError: when the day is malformed.
    """
    d = dt.date.fromisoformat(day)
    return f'{d.day} {d.strftime("%b")}'


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
             'headline': 'gbm', 'references': list(LEGACY_REFERENCES), 'models': models,
             'newStoryShare': sc.get('newShare'),
             'notes': ['The US half of this day is degraded: US collection fell from 24 to 28 September, and the day holds 4,486 posts in US stories against 29,183 in India.']}
    return stories, score, [rel, f'{TOURNAMENT}/forecasts/pilot-2026-09-28/pred_{day}_h2_K0.parquet', f'{LIVE_DATA}/vint.parquet', f'{LIVE_DATA}/nk_meta.parquet']


def bridgedRows(top: List[dict], br: dict, actual: pd.Series) -> List[dict]:
    """Forecast top 10 of one region beside the actual count of the story each was bridged to.

    @param top: forecast.json rows.
    @param br: bridge file (fromDay, toDay, minShare, mapping).
    @param actual: posts by key on the target day.
    @returns: rows with bridged, bridgedTo, bridgeShare, storiesSharingTarget, actualBridged and a label.
    @throws KeyError: when the bridge file lacks its mapping.
    """
    mapping = {m['narrativeKey']: m for m in br['mapping']}
    fan = pd.Series([m['bestKey'] for m in br['mapping'] if m['matched']]).value_counts()
    fromD, toD = shortDay(br['fromDay']), shortDay(br['toDay'])
    need = 'half' if abs(float(br.get('minShare', 0.5)) - 0.5) < 1e-12 else f'{round(100 * float(br["minShare"]))} percent'
    rows = []
    for r in top:
        m = mapping.get(r['arbiterKey'])
        row = dict(r)
        if m and m['matched']:
            a = int(actual.get(m['bestKey'], 0))
            row.update({'bridged': True, 'bridgedTo': m['bestKey'], 'bridgeShare': num(m['bestShare']),
                        'storiesSharingTarget': int(fan.get(m['bestKey'], 1)),
                        'actualBridged': a, 'label': f'bridged actual: posts on {toD} in the story that holds most of this story\'s {fromD} posts'})
        else:
            row.update({'bridged': False, 'bridgedTo': None, 'actualBridged': None,
                        'label': f'unmatched by the bridge: no {toD} story holds {need} of its {fromD} posts, so it is not scored'})
        rows.append(row)
    return rows


def bridgedModels(sc: dict, voidTop10IN: tuple = ()) -> dict:
    """Bridged log error and top-10 hits per model, each beside its unmatched share.

    @param sc: one folder object of a bridged score file (models, unmatched).
    @param voidTop10IN: models whose India top 10 is void because ties at zero fill it (30 Sep audit F4); a model
        may also carry its own 'top10Void' reason.
    @returns: {model: {name, logError, logErrorUS, logErrorIN, top10HitsUS, top10HitsIN, rows, rowsMerged}}.
    @throws KeyError: when a model has no unmatched record.
    """
    models = {}
    for name, m in sc['models'].items():
        un = sc['unmatched'][name]
        sP, sK = num(un.get('unmatchedShareOfPredicted')), num(un.get('unmatchedShareOfKeys'))
        tag = (f'bridged, {round(100 * sP, 1)} percent of its predicted posts unmatched' if sP is not None
               else f'bridged, {round(100 * sK, 1)} percent of keys unmatched')
        hits = {d['region']: d['hits'] for d in m.get('perDay', [])}
        byR = {R: num(un['byRegion'][R].get('unmatchedShareOfPredicted')) for R in REGIONS}
        voidIN = name in voidTop10IN
        ownVoid = m.get('top10Void')
        models[name] = {'name': NAMES.get(name, name),
                        'logError': metric(f'bridged log error, matched stories ({tag})', num(m.get('male')), unmatchedShareOfPredicted=sP, unmatchedShareOfKeys=sK),
                        'logErrorUS': metric(f'bridged log error, US ({tag})', num(m.get('male_US')), unmatchedShareOfPredicted=byR['US'], unmatchedShareOfKeys=sK),
                        'logErrorIN': metric(f'bridged log error, India ({tag})', num(m.get('male_IN')), unmatchedShareOfPredicted=byR['IN'], unmatchedShareOfKeys=sK),
                        'top10HitsUS': metric(f'bridged real top-10 stories found, US, at most 4 ({tag})',
                                              None if (name == 'zero' or ownVoid) else hits.get('US'), unmatchedShareOfKeys=sK,
                                              **({'void': ownVoid} if ownVoid else {})),
                        'top10HitsIN': metric(f'bridged real top-10 stories found, India, at most 4 ({tag})',
                                              None if (name == 'zero' or voidIN or ownVoid) else hits.get('IN'), unmatchedShareOfKeys=sK,
                                              **({'void': 'ties at zero fill this top 10 (audit F4)'} if voidIN else {'void': ownVoid} if ownVoid else {})),
                        'rows': m.get('rows'), 'rowsMerged': m.get('rowsMerged')}
    return models


def bridgeSummary(br: dict, sc: dict, headline: str) -> dict:
    """Counts of the bridge itself: keys matched and unmatched, and the share of the day the matched stories hold.

    @param br: bridge file.
    @param sc: one folder object of a bridged score file.
    @param headline: model whose unmatched record gives the forecast key counts.
    @returns: {keysMatched, forecastKeysUnmatched, forecastKeys, unmatchedShareOfKeys, dayPostsCoveredByMatchedStories}.
    @throws KeyError: when a field is absent.
    """
    un = sc['unmatched'][headline]
    return {'keysMatched': br['keysMatched'], 'forecastKeysUnmatched': un['keysUnmatched'],
            'forecastKeys': un['keys'], 'unmatchedShareOfKeys': num(un['unmatchedShareOfKeys']),
            'dayPostsCoveredByMatchedStories': sc['dayPostsCoveredByMatchedTargets']}


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
    actual = actualCounts(research, day)
    unK = sc['unmatched']['gbm']['unmatchedShareOfKeys']
    iss = issuesFor(day)
    forecasts = [{'issueDay': i['issueDay'], 'horizonDays': i['horizon']['horizonDays'], 'model': i['forecast']['model'],
                  'regions': {R: bridgedRows(i['horizon']['regions'][R]['top'], br, actual) for R in REGIONS}} for i in iss]
    stories = {'day': day, 'kind': 'bridged',
               'reading': 'T0, bridged: Arbiter renamed every story on 30 September, so each forecast story is followed to the 30 Sep story holding at least half of its 28 Sep posts',
               'bridgeRule': br['rule'], 'unmatchedShareOfKeys': num(unK),
               'forecasts': forecasts,
               'actualTop10': computedTop(research, actual, {m['bestKey'] for m in br['mapping'] if m['matched']},
                                          'actual 30 Sep story (new key); bridged comparison only')}
    score = {'day': day, 'kind': 'bridged', 'horizonDays': 2, 'reading': 'T0, bridged',
             'logErrorDefinition': 'mean |log(1 + actual) - log(1 + forecast)| over the matched 30 Sep stories; lower is better',
             'headline': 'gbm', 'references': list(LEGACY_REFERENCES), 'models': bridgedModels(sc, ('nb_glm',)),
             'bridge': bridgeSummary(br, sc, 'gbm'),
             'notes': ['Bridged numbers cover the stories that kept their posts across the rename, a selected slice of the day, and are not comparable with a by-key score.',
                       'Only 4 of each region\'s real top 10 are matched stories, so bridged top-10 hits are capped at 4.',
                       'Forecasts of 102 unmatched stories whose posts landed in a matched story are dropped, which biases bridged forecasts low; adding them back lowers every model\'s error by about 0.04 to 0.06 and leaves the order unchanged.',
                       'Coverage of the 80 percent range and the area-share comparison are not reported for this day.']}
    return stories, score, [relS, relB, f'{LIVE_DATA}/vint.parquet', f'{LIVE_DATA}/nk_meta.parquet']


def voidReason(sc: dict, name: str) -> Optional[str]:
    """Why a model's amendment 04 scores are void in one score file, or None when they stand.

    They are void when the scorer set secondaryVoid, or when the replay of the frozen forecast is missing or not ok.

    @param sc: research score file.
    @param name: model name.
    @returns: the reason in plain words, or None.
    @throws: nothing.
    """
    own = (sc.get('models', {}).get(name) or {}).get('secondaryVoid')
    if own:
        return str(own)
    rc = (sc.get('replayCheck') or {}).get(name)
    if rc is None:
        return 'the score file holds no replay check for this model'
    if not rc.get('ok'):
        return f'the replay does not reproduce the frozen forecast (largest difference {rc.get("maxAbsDiff")})'
    return None


def regionCell(m: dict, R: str) -> dict:
    """One cell ('all', 'US' or 'IN') of a score file's model entry, or an empty dict.

    A model entry also holds keys that are not cells (secondaryVoid is a string), so only dict values count.

    @param m: models[name] of a score file.
    @param R: cell name.
    @returns: the cell, or {} when absent or not a cell.
    @throws: nothing.
    """
    c = m.get(R)
    return c if isinstance(c, dict) else {}


def forwardModel(name: str, m: dict, void: Optional[str]) -> dict:
    """Published scores of one model in one by-key score file: amendment 04 scores first, then log error.

    @param name: model name.
    @param m: models[name] of the score file.
    @param void: voidReason for the model, or None.
    @returns: the model entry for score.json.
    @throws: nothing.
    """
    clim = name == 'zero'
    out: Dict[str, object] = {'name': NAMES.get(name, name)}
    if clim:
        out['distributionNote'] = CLIMATOLOGY
    for R in CELLS:
        sfx = '' if R == 'all' else R
        out[f'activeRows{sfx}'] = metric(f'stories with at least one post, {CELL_WORDS[R]}', regionCell(m, R).get('activeRows'))
    for key, words in DISTRIBUTIONAL:
        for R in CELLS:
            sfx = '' if R == 'all' else R
            label = f'{words}, {CELL_WORDS[R]}' + (' (climatology)' if clim else '')
            value = None if void else num(regionCell(m, R).get(key))
            out[f'{key}{sfx}'] = metric(label, value, **({'void': void} if void else {}))
    out['logError'] = metric('log error, all stories', num(regionCell(m, 'all').get('logErr')))
    for R in REGIONS:
        out[f'logError{R}'] = metric(f'log error, {CELL_WORDS[R]}', num(regionCell(m, R).get('logErr')))
    for R in REGIONS:
        out[f'top10Hits{R}'] = metric(f'real top-10 stories found, {CELL_WORDS[R]}', None if clim else regionCell(m, R).get('top10All'))
    out['cover80'] = metric('share of actuals inside the 80 percent range', num(regionCell(m, 'all').get('cover80')))
    out['rows'] = regionCell(m, 'all').get('rows')
    if void:
        out['secondaryVoid'] = void
    return out


def forwardReference(name: str, cells: dict) -> dict:
    """Published scores of one fitted reference (surv_age, tsb, hurdle_nb): only the scores it is registered for.

    @param name: reference name.
    @param cells: secondaryReferences[name] of the score file.
    @returns: the reference entry for score.json.
    @throws: nothing.
    """
    words = dict(DISTRIBUTIONAL, logErr='log error')
    keys = REFERENCE_SCORES.get(name, tuple(k for k in ('rps', 'brierActive', 'logErrGivenActive', 'logErr') if k in regionCell(cells, 'all')))
    out: Dict[str, object] = {'name': NAMES.get(name, name), 'scoredOn': list(keys)}
    for R in CELLS:
        sfx = '' if R == 'all' else R
        out[f'activeRows{sfx}'] = metric(f'stories with at least one post, {CELL_WORDS[R]}', regionCell(cells, R).get('activeRows'))
    for key in keys:
        pub = 'logError' if key == 'logErr' else key
        for R in CELLS:
            sfx = '' if R == 'all' else R
            out[f'{pub}{sfx}'] = metric(f'{words[key]}, {CELL_WORDS[R]}', num(regionCell(cells, R).get(key)))
    return out


def areaEntry(area: Optional[dict]) -> dict:
    """The US issue-area share comparison of one score file or rename record.

    @param area: areaShareUS of the research file, or None.
    @returns: {label, actualPosts, tvd: {gbm_med, mix_persist}} with None values when the file holds none.
    @throws: nothing.
    """
    tvd = (area or {}).get('tvd') or {}
    return {'label': ('US issue-area share of the day\'s posts: total variation distance between the forecast mix of '
                      'Arbiter\'s categories and the real mix, lower is better. It does not depend on story keys, but it '
                      'does depend on Arbiter\'s categoriser.'),
            'actualPosts': (area or {}).get('actualPosts'),
            'tvd': {'gbm_med': metric('boosted trees (median): its forecasts summed by category', num(tvd.get('gbm_med'))),
                    'mix_persist': metric('the origin day\'s mix', num(tvd.get('mix_persist')))}}


def restrictionEntry(restriction: Optional[dict]) -> Optional[dict]:
    """Keys dropped after a history rename and their share of each model's predicted posts, per region.

    @param restriction: historyRenameRestriction of the score file, or None.
    @returns: None when no history rename applies, else {label, regions: {R: {keptKeys, excludedKeys, excludedPredictedShare}}}.
    @throws: nothing.
    """
    if not restriction:
        return None
    return {'label': ('Arbiter renamed its stories within the seven days before the origin. Stories that kept an old key '
                      'cannot get a post under it, so they are dropped from every score; this gives how many were dropped '
                      'and their share of each model\'s predicted posts.'),
            'regions': {R: {'keptKeys': c.get('keptKeys'), 'excludedKeys': c.get('excludedKeys'),
                            'excludedPredictedShare': {m: num(v) for m, v in (c.get('excludedPredictedShare') or {}).items()}}
                        for R, c in restriction.items()}}


def continuityEntry(cont: dict) -> dict:
    """Run-map continuity per region, as the scorer recorded it.

    @param cont: keyContinuity of a score file or continuity of a rename record.
    @returns: {R: {forward, history, mapsMissing, pairs: [{from, to, gapDays, share, partialFlag}]}}.
    @throws: nothing.
    """
    return {R: {'forwardRename': bool(c.get('forward')), 'historyRename': bool(c.get('history')), 'mapsMissing': list(c.get('mapsMissing') or []),
                'pairs': [{'from': p.get('from'), 'to': p.get('to'), 'gapDays': p.get('gapDays'), 'keysKept': num(p.get('share')),
                           'partialFlag': bool(p.get('partialFlag'))} for p in c.get('pairs') or []]}
            for R, c in (cont or {}).items()}


def forwardScoreFiles(day: str, research: str) -> List[dict]:
    """Research files for every ledger issue that forecast the day.

    @param day: target day.
    @param research: research repo root.
    @returns: [{issue, kind, score, rename, bridged, bridge, sources}] in issue order; kind is 'by key', 'bridged' or
        'rename, not scored'. Issues with no file yet are left out.
    @throws OSError: when a file is unreadable.
    @throws ValueError: when a bridged score names no bridge file.
    """
    out = []
    for i in issuesFor(day):
        h = i['horizon']['horizonDays']
        base = f'{FORWARD}/results/issue-{i["issueDay"]}'
        relS, relR, relB = (f'{base}/score_{day}_h{h}_T0.json', f'{base}/rename_{day}_h{h}_T0.json',
                            f'{base}/score_bridged_{day}_h{h}_T0.json')
        if os.path.exists(os.path.join(research, relS)):
            out.append({'issue': i, 'kind': 'by key', 'score': loadJson(os.path.join(research, relS)), 'sources': [relS]})
            continue
        if not os.path.exists(os.path.join(research, relR)):
            continue
        rec = {'issue': i, 'kind': 'rename, not scored', 'rename': loadJson(os.path.join(research, relR)), 'sources': [relR]}
        if os.path.exists(os.path.join(research, relB)):
            doc = loadJson(os.path.join(research, relB))
            folder = doc
            if 'folders' in doc:
                want = f'pred_{day}_h{h}_K0.parquet'
                hits = [f for f in doc['folders'].values() if f.get('file') == want] or list(doc['folders'].values())
                folder = hits[0]
            if not doc.get('bridge'):
                raise ValueError(f'{relB} names no bridge file')
            relBr = f'{base}/{doc["bridge"]}'
            rec.update({'kind': 'bridged', 'bridged': folder, 'bridge': loadJson(os.path.join(research, relBr))})
            rec['sources'] += [relB, relBr]
        out.append(rec)
    return out


def forwardByKey(day: str, research: str) -> Optional[tuple]:
    """Stories and score for a day of the forward block, one entry per issue that forecast it.

    A target day can be forecast by two issues (D+1 of one, D+2 of the one before). Each keeps its own rows and
    scores; nothing is averaged across issues. A file scored by key reports the amendment 04 scores before log error;
    a forward-rename day reports the rename record and, when it exists, the bridged score with its unmatched share.

    @param day: target day.
    @param research: research repo root.
    @returns: (stories, score, source files), or None when no score file or rename record exists yet.
    @throws OSError: when a source file is unreadable.
    """
    found = forwardScoreFiles(day, research)
    if not found:
        return None
    actual = actualCounts(research, day)
    forecasts, per, sources = [], [], []
    for f in found:
        i, h = f['issue'], f['issue']['horizon']
        sources += f['sources']
        head = {'issueDay': i['issueDay'], 'horizonDays': h['horizonDays'], 'lagFromOriginDays': h.get('lagFromOriginDays'),
                'horizonCountedFrom': h.get('horizonCountedFrom'), 'kind': f['kind'],
                'modelName': NAMES.get(i['forecast']['model'].split('|')[0], i['forecast']['model'])}
        if f['kind'] == 'by key':
            sc = f['score']
            forecasts.append({**head, 'model': i['forecast']['model'],
                              'regions': {R: storyRows(h['regions'][R]['top'], actual) for R in REGIONS}})
            models = {name: forwardModel(name, m, voidReason(sc, name)) for name, m in sc['models'].items()}
            fit = sc.get('hurdleFit') or {}
            per.append({**head, 'source': f['sources'][0], 'models': models,
                        'secondaryReferences': {name: forwardReference(name, c) for name, c in (sc.get('secondaryReferences') or {}).items()},
                        'secondaryVoid': {name: m['secondaryVoid'] for name, m in models.items() if m.get('secondaryVoid')},
                        'areaShareUS': areaEntry(sc.get('areaShareUS')),
                        'historyRenameRestriction': restrictionEntry(sc.get('historyRenameRestriction')),
                        'keyContinuity': continuityEntry(sc.get('keyContinuity')),
                        'hurdleFit': {'converged': fit.get('converged'), 'alphaAtBound': fit.get('alphaAtBound')} if fit else None,
                        'tsbBeta': sc.get('tsbBeta'),
                        'newStoryShare': sc.get('newShare'), 'degradedTarget': sc.get('degradedTarget'),
                        'countsTowardBlock': True})
            continue
        rec = f['rename']
        entry = {**head, 'source': f['sources'][0], 'rule': rec.get('rule'), 'keyContinuity': continuityEntry(rec.get('continuity')),
                 'areaShareUS': areaEntry(rec.get('areaShareUS')), 'countsTowardBlock': False}
        if f['kind'] == 'bridged':
            sc, br = f['bridged'], f['bridge']
            rows = {R: bridgedRows(h['regions'][R]['top'], br, actual) for R in REGIONS}
            hl = EI.FORWARD_MODEL if EI.FORWARD_MODEL in sc['unmatched'] else next(iter(sc['unmatched']))
            byR = sc['unmatched'][hl]['byRegion']
            forecasts.append({**head, 'model': i['forecast']['model'], 'bridgeRule': br.get('rule'),
                              'unmatchedShareOfPredicted': {R: num(byR[R].get('unmatchedShareOfPredicted')) for R in REGIONS},
                              'regions': rows})
            entry.update({'models': bridgedModels(sc), 'bridge': bridgeSummary(br, sc, hl)})
        else:
            forecasts.append({**head, 'model': i['forecast']['model'],
                              'regions': {R: [{**r, 'actual': None, 'inRange': None,
                                               'label': 'not scored: Arbiter renamed its stories after the reading this forecast started from, and no bridged score exists yet'}
                                              for r in h['regions'][R]['top']] for R in REGIONS}})
            entry['models'] = None
        per.append(entry)
    kinds = sorted({f['kind'] for f in found})
    kind = kinds[0] if len(kinds) == 1 else 'mixed'
    byKey = [f for f in found if f['kind'] == 'by key']
    if byKey:
        top, topSource = actualTop(byKey[-1]['score']), 'score file'
    else:
        matched = {m['bestKey'] for f in found if f['kind'] == 'bridged' for m in f['bridge']['mapping'] if m['matched']}
        top, topSource = computedTop(research, actual, matched, f'actual {shortDay(day)} story; compared through the bridge only'), 'computed'
    stories = {'day': day, 'kind': kind,
               'reading': 'T0: posts published on the day that Arbiter\'s reading of the day assigns to the story' if kind == 'by key'
               else 'T0. Each forecast says how it was scored: by story key, through the bridge across Arbiter\'s rename, or not scored',
               'forecasts': forecasts, 'actualTop10': top, 'actualTop10Source': topSource}
    if any(f['kind'] != 'by key' for f in found):
        stories['bridgeNote'] = ('Arbiter renamed its stories after the reading at least one of these forecasts started from, so '
                                 'its stories did not keep their keys. A bridged forecast follows each story to the story of the '
                                 'target day holding at least half of its posts. A row with no such match is not scored, and the '
                                 'share of the forecast that could not be matched sits on every row. A bridged day does not count '
                                 'toward the 14 days of its horizon.')
    notes = ['Each issue that forecast this day has its own entry in byIssue. A day can be D+1 of one issue and D+2 of the issue before; the two are kept apart and never averaged.',
             'D+n counts days from the issue day; the lag counts days from the reading the forecast starts from.']
    if any(p.get('historyRenameRestriction') for p in per):
        notes.append('A history rename applies: see historyRenameRestriction for the stories dropped from every score and their share of predicted posts.')
    if any(pp['partialFlag'] for p in per for c in (p.get('keyContinuity') or {}).values() for pp in c['pairs']):
        notes.append('A pair of run maps kept between 25 and 50 percent of keys, which may be a partial rename; amendment 04 flags it for a sensitivity report.')
    score = {'day': day, 'kind': kind, 'reading': 'T0',
             'logErrorDefinition': SCORE_DEFINITIONS['logError'],
             'scoreDefinitions': SCORE_DEFINITIONS,
             'scoreOrder': 'stories with a post, ranked probability score, Brier score on any post and log error given a post come before log error',
             'headline': EI.FORWARD_MODEL, 'references': list(REFERENCES),
             'secondaryReferences': {k: NAMES[k] for k in REFERENCE_SCORES}, 'byIssue': per, 'notes': notes}
    return stories, score, sources + [f'{LIVE_DATA}/vint.parquet', f'{LIVE_DATA}/nk_meta.parquet']


def outcomeReadme(day: str, kind: str, forward: bool = False) -> str:
    """Plain-prose README for an outcome folder.

    @param day: target day.
    @param kind: 'by key', 'bridged', 'rename, not scored' or 'mixed'.
    @param forward: True for a day of the forward block (amendment 04 scores, one entry per issue).
    @returns: markdown text.
    @throws: nothing.
    """
    lines = [f'# Outcome {day}', '',
             '`stories.json` puts each forecast story beside the number of posts Arbiter\'s reading of the day assigned to it, '
             'and lists the day\'s real top 10 per region. `score.json` scores every model against the references, with the '
             'GitHub push time of the issue that forecast this day. `MANIFEST.json` holds the sha256 of every file here, and '
             '`sources` in it names the research files the numbers came from, with their sha256.', '']
    if kind == 'bridged' and not forward:
        lines += ['Arbiter renamed every story on 30 September, so no forecast story kept its key. Each forecast story was followed '
                  'to the 30 September story holding at least half of its 28 September posts, a rule fixed before any score was '
                  'computed. Stories with no such match are excluded and counted. Every number here says "bridged" and gives the '
                  'unmatched share beside it.', '']
    if forward:
        lines += ['Each issue that forecast this day is scored on its own. A day can be D+1 of one issue (two days after the reading '
                  'it starts from, lag 2) and D+2 of the issue before (lag 3); the two are never averaged. For each model, '
                  '`score.json` gives the number of stories that got a post, then the ranked probability score, the Brier score on '
                  'whether a story got any post and the log error on the stories that did, and only then the log error over every '
                  'story. "Every story dies" forecasts no posts, but its predictive distribution is a climatology of earlier days, '
                  'so its first three scores are labelled climatology. A model whose frozen forecast could not be reproduced has '
                  'those scores marked void. The US issue-area share compares the forecast mix of Arbiter\'s categories with the '
                  'real one and does not depend on story keys.', '']
        if kind != 'by key':
            lines += ['Arbiter renamed its stories after the reading at least one forecast started from. That forecast is scored only '
                      'through the bridge, every bridged number gives the unmatched share beside it, and it does not count toward the '
                      '14 days of its horizon. Until the bridged score exists, the forecast is listed as not scored.', '']
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
        fh.write(outcomeReadme(day, stories['kind'], forward=day not in ('2026-09-28', '2026-09-30')))
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
