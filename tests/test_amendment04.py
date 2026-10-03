"""Tests for the ledger tools after amendment 04 of TOMORROW-FWD-1.

Fixtures are synthetic: a scratch ledger with three issues and a scratch research tree holding live tables, a by-key
D+1 score, a D+2 score of the same target from the issue before (with void models), a forward-rename record with a
bridged score, and a rename record with no bridged score yet.

Run with a Python that has pandas and pyarrow (tools/export_outcome.py needs them):
  <python> -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

import pandas as pd

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools')
sys.path.insert(0, TOOLS)
import export_issue as EI  # noqa: E402
import export_outcome as EO  # noqa: E402
import outcome_table as OT  # noqa: E402

DAY = '2026-10-08'      # forecast by issue 2026-10-07 at D+1 and by issue 2026-10-06 at D+2
RDAY = '2026-10-09'     # forecast by issue 2026-10-08 at D+1 (bridged) and by issue 2026-10-07 at D+2 (no bridge yet)
KEYS = {'US': ['US:a', 'US:b', 'US:c'], 'IN': ['IN:a', 'IN:b', 'IN:c']}
NEW_KEYS = {'US': ['US:n1', 'US:n2'], 'IN': ['IN:n1', 'IN:n2']}


def dayIndex(day: str) -> int:
    """Days since 2026-01-01, the research tables' day index.

    @param day: YYYY-MM-DD.
    @returns: the index.
    @throws ValueError: on a malformed day.
    """
    return (pd.Timestamp(day) - pd.Timestamp('2026-01-01')).days


def topRows(region: str) -> list:
    """forecast.json top rows for one region.

    @param region: 'US' or 'IN'.
    @returns: three rows ranked 1 to 3.
    @throws: nothing.
    """
    return [{'rank': i + 1, 'title': f'story {k}', 'arbiterKey': k, 'posts': 10.0 - 3 * i, 'lo': 2.0, 'hi': 20.0, 'chance': None}
            for i, k in enumerate(KEYS[region])]


def horizon(issueDay: str, h: int, target: str) -> dict:
    """One forecast.json horizon counted from the issue day.

    @param issueDay: issue day.
    @param h: days from the issue day.
    @param target: target day.
    @returns: the horizon object.
    @throws: nothing.
    """
    return {'horizonDays': h, 'horizonCountedFrom': 'issue day', 'lagFromOriginDays': h + 1, 'targetDay': target,
            'targetStartsUtc': f'{target}T00:00:00Z', 'frozenBeforeTargetStart': True,
            'regions': {R: {'liveStories': 3, 'top': topRows(R)} for R in ('US', 'IN')}}


def cell(base: float, rows: int = 3, active: int = 2, top10: int = 1) -> dict:
    """A score-file cell with every amendment 04 score.

    @param base: value every score is derived from, so cells differ by issue.
    @param rows: rows.
    @param active: stories with a post.
    @param top10: top-10 hits.
    @returns: the cell.
    @throws: nothing.
    """
    return {'rows': rows, 'logErr': base, 'cover80': 0.5, 'top10All': top10, 'activeRows': active,
            'brierActive': base / 10, 'rps': base * 2, 'logErrGivenActive': base + 0.5}


def scoreFile(base: float, voidGbm: bool = False, badReplayVolols: bool = False) -> dict:
    """A research score file as fwd_score.py writes it after amendment 04.

    @param base: offset that makes this file's numbers distinct.
    @param voidGbm: gbm_med carries secondaryVoid.
    @param badReplayVolols: volols's replay check fails.
    @returns: the score object.
    @throws: nothing.
    """
    models = {}
    for j, m in enumerate(('gbm_med', 'persist', 'zero', 'volols')):
        b = base + j / 100
        models[m] = {'all': cell(b, rows=6, active=4), 'US': cell(b + 0.001), 'IN': cell(b + 0.002)}
    if voidGbm:
        for R in ('all', 'US', 'IN'):
            for k in ('rps', 'brierActive', 'logErrGivenActive'):
                models['gbm_med'][R].pop(k)
        models['gbm_med']['secondaryVoid'] = 'replay does not reproduce the frozen forecast'
    replay = {m: {'maxAbsDiff': 0.0, 'ok': True} for m in models}
    if voidGbm:
        replay['gbm_med'] = {'maxAbsDiff': 0.4, 'ok': False}
    if badReplayVolols:
        replay['volols'] = {'maxAbsDiff': 0.2, 'ok': False}
    ref = lambda b: {'rows': 6, 'activeRows': 4, 'brierActive': b}  # noqa: E731
    return {
        'targetDay': DAY, 'origin': '2026-10-06', 'K': 0, 'models': models,
        'actualTop10': {R: [{'narrativeKey': KEYS[R][0], 'title': f'story {KEYS[R][0]}', 'posts': 40, 'liveAtOrigin': True},
                            {'narrativeKey': NEW_KEYS[R][0], 'title': 'new story', 'posts': 30, 'liveAtOrigin': False}] for R in ('US', 'IN')},
        'newShare': {'US': 0.6, 'IN': 0.5}, 'degradedTarget': {'US': False, 'IN': False},
        'keyContinuity': {R: {'pairs': [{'from': '2026-10-05', 'to': '2026-10-06', 'gapDays': 1, 'share': 0.4, 'partialFlag': True}],
                              'forward': False, 'history': True, 'mapsMissing': []} for R in ('US', 'IN')},
        'historyRenameRestriction': {'US': {'keptKeys': 3, 'excludedKeys': 2,
                                            'excludedPredictedShare': {'gbm_med': 0.0061, 'persist': 0.0, 'zero': 0.0, 'volols': 0.25}}},
        'secondaryReferences': {
            # surv_age carries a stray rps here so the test sees it dropped: it is registered for Brier only.
            'surv_age': {R: dict(ref(base + 0.1), rps=99.0) for R in ('all', 'US', 'IN')},
            'tsb': {R: ref(base + 0.2) for R in ('all', 'US', 'IN')},
            'hurdle_nb': {R: dict(ref(base + 0.3), rps=base + 3, logErrGivenActive=base + 4, logErr=base + 5) for R in ('all', 'US', 'IN')},
        },
        'replayCheck': replay,
        'hurdleFit': {'converged': True, 'alphaAtBound': True},
        'tsbBeta': 0.5,
        'areaShareUS': {'actualPosts': 900, 'tvd': {'gbm_med': base + 0.7, 'mix_persist': base + 0.9}},
    }


def renameRecord(h: int) -> dict:
    """A forward-rename record as fwd_score.py writes it.

    @param h: horizon.
    @returns: the record.
    @throws: nothing.
    """
    return {'file': f'pred_{RDAY}_h{h}_K0.parquet', 'targetDay': RDAY, 'horizon': h, 'K': 0,
            'continuity': {R: {'pairs': [{'from': '2026-10-07', 'to': '2026-10-08', 'gapDays': 1, 'share': 0.0, 'partialFlag': False}],
                               'forward': True, 'history': False, 'mapsMissing': []} for R in ('US', 'IN')},
            'rule': 'amendment 04 section 3: production rename after the origin; not scored by key',
            'areaShareUS': {'actualPosts': 800, 'tvd': {'gbm_med': 0.31 + h / 100, 'mix_persist': 0.52}}}


def bridgedScore() -> dict:
    """A bridged score in the 30 Sep shape, with the bridge file named beside it.

    @returns: the score object.
    @throws: nothing.
    """
    un = lambda sp: {'keys': 6, 'keysUnmatched': 2, 'unmatchedShareOfPredicted': sp, 'unmatchedShareOfKeys': 2 / 6,  # noqa: E731
                     'byRegion': {'US': {'unmatchedShareOfPredicted': 0.25}, 'IN': {'unmatchedShareOfPredicted': 0.125}}}
    mdl = lambda e: {'male': e, 'male_US': e + 0.1, 'male_IN': e + 0.2, 'rows': 4, 'rowsMerged': 1,  # noqa: E731
                     'perDay': [{'region': 'US', 'hits': 1}, {'region': 'IN', 'hits': 2}]}
    return {'targetDay': RDAY, 'bridge': f'bridge_{RDAY}_h1.json',
            'folders': {'issue-2026-10-08': {
                'file': f'pred_{RDAY}_h1_K0.parquet',
                'unmatched': {'gbm_med': un(0.2), 'persist': un(0.1), 'zero': un(None), 'volols': un(0.3)},
                'dayPostsCoveredByMatchedTargets': {'US': {'dayPosts': 100.0, 'shareInMatchedTargets': 0.4}},
                'models': {'gbm_med': mdl(1.0), 'persist': mdl(1.5), 'zero': mdl(1.7), 'volols': mdl(1.2)}}}}


def bridgeFile() -> dict:
    """A bridge file: US:a and IN:a matched to new keys, the rest unmatched.

    @returns: the bridge object.
    @throws: nothing.
    """
    mapping = []
    for R in ('US', 'IN'):
        for k in KEYS[R]:
            hit = k.endswith(':a')
            mapping.append({'narrativeKey': k, 'region': R, 'members': 10, 'bestKey': NEW_KEYS[R][1] if hit else None,
                            'bestShare': 0.8 if hit else 0.1, 'matched': hit})
    return {'rule': 'test bridge rule', 'fromDay': '2026-10-07', 'toDay': RDAY, 'minShare': 0.5, 'keysMatched': 2, 'mapping': mapping}


def writeJson(path: str, obj: object) -> None:
    """Write JSON, creating the folder.

    @param path: file path.
    @param obj: object.
    @returns: None.
    @throws OSError: on a file system failure.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(obj, fh, indent=1)


class Fixture(unittest.TestCase):
    """Scratch ledger and research tree, with the tools pointed at them."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = os.path.join(self.tmp.name, 'ledger')
        self.research = os.path.join(self.tmp.name, 'research')
        self._saved = (EO.LEDGER, OT.LEDGER)
        EO.LEDGER = OT.LEDGER = self.ledger
        issues = {'2026-10-06': [horizon('2026-10-06', 2, DAY)],
                  '2026-10-07': [horizon('2026-10-07', 1, DAY), horizon('2026-10-07', 2, RDAY)],
                  '2026-10-08': [horizon('2026-10-08', 1, RDAY)]}
        for I, hs in issues.items():
            writeJson(os.path.join(self.ledger, 'issues', I, 'forecast.json'),
                      {'issueDay': I, 'model': 'gbm_med', 'origin': I, 'note': None, 'horizons': hs})
        live = os.path.join(self.research, EO.LIVE_DATA)
        os.makedirs(live)
        meta, vint = [], []
        nk = 0
        for R in ('US', 'IN'):
            for k in KEYS[R] + NEW_KEYS[R]:
                nk += 1
                meta.append({'nk': nk, 'narrativeKey': k, 'region': R, 'hub': False, 'title': f'story {k}'})
                for d, n in ((DAY, {':a': 40, ':b': 1, ':c': 0, ':n1': 30, ':n2': 0}), (RDAY, {':a': 0, ':b': 0, ':c': 0, ':n1': 5, ':n2': 25})):
                    vint.append({'nk': nk, 'pday': dayIndex(d), 'f': dayIndex(d), 'n': n[k[k.index(':'):]]})
        pd.DataFrame(meta).to_parquet(os.path.join(live, 'nk_meta.parquet'))
        pd.DataFrame(vint).to_parquet(os.path.join(live, 'vint.parquet'))
        res = os.path.join(self.research, EO.FORWARD, 'results')
        writeJson(os.path.join(res, 'issue-2026-10-07', f'score_{DAY}_h1_T0.json'), scoreFile(0.2))
        writeJson(os.path.join(res, 'issue-2026-10-06', f'score_{DAY}_h2_T0.json'), scoreFile(0.6, voidGbm=True, badReplayVolols=True))
        writeJson(os.path.join(res, 'issue-2026-10-08', f'rename_{RDAY}_h1_T0.json'), renameRecord(1))
        writeJson(os.path.join(res, 'issue-2026-10-08', f'score_bridged_{RDAY}_h1_T0.json'), bridgedScore())
        writeJson(os.path.join(res, 'issue-2026-10-08', f'bridge_{RDAY}_h1.json'), bridgeFile())
        writeJson(os.path.join(res, 'issue-2026-10-07', f'rename_{RDAY}_h2_T0.json'), renameRecord(2))

    def tearDown(self) -> None:
        EO.LEDGER, OT.LEDGER = self._saved
        self.tmp.cleanup()

    def writeOutcome(self, day: str) -> tuple:
        """Build an outcome with forwardByKey and write stories.json and score.json into the scratch ledger.

        @param day: target day.
        @returns: (stories, score).
        @throws AssertionError: when nothing is built.
        """
        built = EO.forwardByKey(day, self.research)
        self.assertIsNotNone(built)
        stories, score, _ = built
        out = os.path.join(self.ledger, 'outcomes', day)
        writeJson(os.path.join(out, 'stories.json'), stories)
        writeJson(os.path.join(out, 'score.json'), score)
        return stories, score


class TwoIssuesOneDay(Fixture):
    """A target day forecast at D+1 by one issue and at D+2 by the issue before."""

    def test_entries_kept_apart_not_averaged(self) -> None:
        stories, score = self.writeOutcome(DAY)
        self.assertEqual([(p['issueDay'], p['horizonDays'], p['lagFromOriginDays']) for p in score['byIssue']],
                         [('2026-10-06', 2, 3), ('2026-10-07', 1, 2)])
        self.assertEqual([(f['issueDay'], f['horizonDays']) for f in stories['forecasts']], [('2026-10-06', 2), ('2026-10-07', 1)])
        h2, h1 = score['byIssue']
        self.assertEqual(h1['models']['persist']['rps']['value'], round((0.2 + 0.01) * 2, 3))
        self.assertEqual(h2['models']['persist']['rps']['value'], round((0.6 + 0.01) * 2, 3))
        self.assertEqual(h1['models']['gbm_med']['logError']['value'], 0.2)
        self.assertEqual(h2['models']['gbm_med']['logError']['value'], 0.6)
        self.assertNotIn('models', score)

    def test_scores_before_log_error(self) -> None:
        _, score = self.writeOutcome(DAY)
        keys = list(score['byIssue'][1]['models']['gbm_med'])
        first = keys.index('logError')
        for k in ('activeRows', 'activeRowsUS', 'activeRowsIN', 'rps', 'rpsUS', 'rpsIN', 'brierActive', 'brierActiveIN',
                  'logErrGivenActive', 'logErrGivenActiveUS'):
            self.assertLess(keys.index(k), first, k)
        m = score['byIssue'][1]['models']['gbm_med']
        self.assertEqual(m['activeRows']['value'], 4)
        self.assertEqual(m['activeRowsUS']['value'], 2)
        self.assertEqual(m['brierActiveUS']['value'], round(0.201 / 10, 3))
        self.assertEqual(m['logErrGivenActiveIN']['value'], round(0.202 + 0.5, 3))
        self.assertIn('rps', score['scoreDefinitions'])

    def test_zero_labelled_climatology(self) -> None:
        _, score = self.writeOutcome(DAY)
        z = score['byIssue'][1]['models']['zero']
        for k in ('rps', 'brierActiveUS', 'logErrGivenActiveIN'):
            self.assertIn('climatology', z[k]['label'])
        self.assertNotIn('climatology', z['logError']['label'])
        self.assertIn('climatology', z['distributionNote'])
        self.assertIsNone(z['top10HitsUS']['value'])
        self.assertNotIn('climatology', score['byIssue'][1]['models']['persist']['rps']['label'])

    def test_references(self) -> None:
        _, score = self.writeOutcome(DAY)
        self.assertEqual(score['references'], ['persist', 'zero', 'volols'])
        refs = score['byIssue'][1]['secondaryReferences']
        self.assertEqual(refs['surv_age']['name'], 'age-conditioned survival')
        self.assertEqual(refs['hurdle_nb']['name'], 'hurdle negative binomial')
        self.assertEqual(refs['tsb']['name'], EO.NAMES['tsb'])
        for name in ('surv_age', 'tsb'):
            self.assertEqual(refs[name]['scoredOn'], ['brierActive'])
            self.assertFalse(any(k.startswith(('rps', 'logErr')) for k in refs[name]), name)
        self.assertEqual(refs['surv_age']['brierActive']['value'], 0.3)
        self.assertEqual(refs['hurdle_nb']['rps']['value'], 3.2)
        self.assertEqual(refs['hurdle_nb']['logErrGivenActiveUS']['value'], 4.2)
        self.assertEqual(refs['hurdle_nb']['logError']['value'], 5.2)
        self.assertLess(list(refs['hurdle_nb']).index('rps'), list(refs['hurdle_nb']).index('logError'))

    def test_area_share_and_history_rename(self) -> None:
        _, score = self.writeOutcome(DAY)
        p = score['byIssue'][1]
        self.assertEqual(p['areaShareUS']['actualPosts'], 900)
        self.assertEqual(p['areaShareUS']['tvd']['gbm_med']['value'], 0.9)
        self.assertEqual(p['areaShareUS']['tvd']['mix_persist']['value'], 1.1)
        self.assertIn('categoriser', p['areaShareUS']['label'])
        r = p['historyRenameRestriction']['regions']['US']
        self.assertEqual((r['keptKeys'], r['excludedKeys']), (3, 2))
        self.assertEqual(r['excludedPredictedShare']['gbm_med'], 0.006)
        self.assertEqual(r['excludedPredictedShare']['volols'], 0.25)
        self.assertNotIn('IN', p['historyRenameRestriction']['regions'])
        self.assertTrue(p['countsTowardBlock'])
        self.assertTrue(any('25 and 50 percent' in n for n in score['notes']))

    def test_void(self) -> None:
        _, score = self.writeOutcome(DAY)
        h2, h1 = score['byIssue']
        g = h2['models']['gbm_med']
        self.assertIn('replay', g['secondaryVoid'])
        self.assertIsNone(g['rps']['value'])
        self.assertIsNone(g['brierActiveUS']['value'])
        self.assertEqual(g['rps']['void'], g['secondaryVoid'])
        self.assertEqual(g['logError']['value'], 0.6)
        v = h2['models']['volols']
        self.assertIn('largest difference 0.2', v['secondaryVoid'])
        self.assertIsNone(v['rpsIN']['value'])
        self.assertEqual(sorted(h2['secondaryVoid']), ['gbm_med', 'volols'])
        self.assertNotIn('secondaryVoid', h2['models']['persist'])
        self.assertEqual(h2['models']['persist']['rps']['value'], round(0.61 * 2, 3))
        self.assertEqual(h1['secondaryVoid'], {})

    def test_missing_replay_check_is_void(self) -> None:
        sc = scoreFile(0.2)
        sc.pop('replayCheck')
        self.assertIn('no replay check', EO.voidReason(sc, 'persist'))
        self.assertIsNone(EO.voidReason(scoreFile(0.2), 'persist'))

    def test_non_cell_keys_tolerated(self) -> None:
        m = {'all': cell(0.1), 'secondaryVoid': 'text', 'US': cell(0.2)}
        out = EO.forwardModel('gbm_med', m, None)
        self.assertIsNone(out['rpsIN']['value'])
        self.assertEqual(out['rpsUS']['value'], 0.4)
        self.assertEqual(EO.regionCell(m, 'secondaryVoid'), {})

    def test_stories_by_key(self) -> None:
        stories, _ = self.writeOutcome(DAY)
        self.assertEqual(stories['kind'], 'by key')
        row = stories['forecasts'][1]['regions']['US'][0]
        self.assertEqual((row['arbiterKey'], row['actual'], row['inRange']), ('US:a', 40, False))
        self.assertEqual(stories['actualTop10Source'], 'score file')

    def test_table_headings_carry_lag(self) -> None:
        self.writeOutcome(DAY)
        text = OT.render(DAY)
        self.assertIn('## Issue 2026-10-06, D+2 (lag 3), boosted trees (median)', text)
        self.assertIn('## Issue 2026-10-07, D+1 (lag 2), boosted trees (median)', text)
        self.assertIn('| 1 | story US:a | 10 | 2 to 20 | 40 | 1 | no |', text)


class RenameDay(Fixture):
    """A target day with a forward rename: one issue bridged, one with no bridged score yet."""

    def test_bridged_and_unscored(self) -> None:
        stories, score = self.writeOutcome(RDAY)
        self.assertEqual(stories['kind'], 'mixed')
        nb, br = score['byIssue']
        self.assertEqual((nb['issueDay'], nb['kind'], nb['models']), ('2026-10-07', 'rename, not scored', None))
        self.assertEqual((br['issueDay'], br['kind']), ('2026-10-08', 'bridged'))
        self.assertFalse(nb['countsTowardBlock'])
        self.assertFalse(br['countsTowardBlock'])
        self.assertTrue(br['keyContinuity']['US']['forwardRename'])
        self.assertEqual(br['areaShareUS']['tvd']['gbm_med']['value'], 0.32)
        self.assertEqual(nb['areaShareUS']['tvd']['gbm_med']['value'], 0.33)

    def test_every_bridged_number_has_unmatched_share(self) -> None:
        _, score = self.writeOutcome(RDAY)
        g = score['byIssue'][1]['models']['gbm_med']
        self.assertEqual(g['logError']['value'], 1.0)
        self.assertIn('20.0 percent of its predicted posts unmatched', g['logError']['label'])
        self.assertEqual(g['logError']['unmatchedShareOfPredicted'], 0.2)
        self.assertEqual(g['logErrorUS']['unmatchedShareOfPredicted'], 0.25)
        self.assertEqual(g['top10HitsIN']['value'], 2)
        for m in score['byIssue'][1]['models'].values():
            for k, v in m.items():
                if isinstance(v, dict) and 'label' in v:
                    self.assertIn('unmatchedShareOfKeys', v, k)
                    self.assertIn('bridged', v['label'], k)
        z = score['byIssue'][1]['models']['zero']
        self.assertIn('33.3 percent of keys unmatched', z['logError']['label'])
        self.assertIsNone(z['top10HitsUS']['value'])
        self.assertEqual(score['byIssue'][1]['bridge']['forecastKeysUnmatched'], 2)

    def test_bridged_rows(self) -> None:
        stories, _ = self.writeOutcome(RDAY)
        fb = stories['forecasts'][1]
        self.assertEqual(fb['unmatchedShareOfPredicted'], {'US': 0.25, 'IN': 0.125})
        a, b = fb['regions']['US'][0], fb['regions']['US'][1]
        self.assertEqual((a['bridged'], a['bridgedTo'], a['actualBridged']), (True, 'US:n2', 25))
        self.assertEqual(a['label'], "bridged actual: posts on 9 Oct in the story that holds most of this story's 7 Oct posts")
        self.assertEqual((b['bridged'], b['actualBridged']), (False, None))
        self.assertTrue(all(r['actual'] is None for r in stories['forecasts'][0]['regions']['IN']))
        self.assertEqual(stories['actualTop10Source'], 'computed')
        top = stories['actualTop10']['US']
        self.assertEqual([(t['arbiterKey'], t['posts'], t['bridgedFromForecast']) for t in top], [('US:n2', 25, True), ('US:n1', 5, False)])

    def test_table(self) -> None:
        self.writeOutcome(RDAY)
        text = OT.render(RDAY)
        self.assertIn('## Issue 2026-10-07, D+2 (lag 3), boosted trees (median)', text)
        self.assertIn('## Issue 2026-10-08, D+1 (lag 2), boosted trees (median)', text)
        self.assertIn('| 1 | story US:a | 10 | 2 to 20 | not scored | not scored | not scored |', text)
        self.assertIn('25.0 percent of predicted posts unmatched', text)
        self.assertIn('12.5 percent of predicted posts unmatched', text)
        self.assertIn('| 1 | story US:n2 | 25 | yes | yes |', text)
        self.assertIn('does not count toward the 14 days', text)

    def test_bridged_score_without_bridge_file_refused(self) -> None:
        p = os.path.join(self.research, EO.FORWARD, 'results', 'issue-2026-10-08', f'score_bridged_{RDAY}_h1_T0.json')
        doc = bridgedScore()
        doc.pop('bridge')
        writeJson(p, doc)
        with self.assertRaises(ValueError):
            EO.forwardByKey(RDAY, self.research)

    def test_nothing_yet(self) -> None:
        self.assertIsNone(EO.forwardByKey('2026-10-10', self.research))


class LegacyCompatibility(Fixture):
    """Outcomes written before amendment 04 render with the lag read from the issue."""

    def test_heading_from_issue_forecast(self) -> None:
        writeJson(os.path.join(self.ledger, 'issues', '2026-09-27', 'forecast.json'),
                  {'issueDay': '2026-09-27', 'horizons': [{'horizonDays': 2, 'horizonCountedFrom': 'origin reading',
                                                           'lagFromOriginDays': 2, 'targetDay': '2026-09-28'}]})
        f = {'issueDay': '2026-09-27', 'horizonDays': 2, 'model': 'gbm|K0'}
        self.assertEqual(OT.horizonText(f), '2 days ahead (lag 2)')
        self.assertEqual(OT.horizonText({**f, 'issueDay': '2026-01-01'}), '2 days ahead')

    def test_bridge_labels_unchanged_for_30_sep(self) -> None:
        br = {'fromDay': '2026-09-28', 'toDay': '2026-09-30', 'minShare': 0.5,
              'mapping': [{'narrativeKey': 'US:a', 'bestKey': 'US:z', 'bestShare': 0.7, 'matched': True},
                          {'narrativeKey': 'US:b', 'bestKey': None, 'bestShare': 0.1, 'matched': False}]}
        rows = EO.bridgedRows(topRows('US')[:2], br, pd.Series({'US:z': 7}))
        self.assertEqual(rows[0]['label'], "bridged actual: posts on 30 Sep in the story that holds most of this story's 28 Sep posts")
        self.assertEqual(rows[1]['label'], 'unmatched by the bridge: no 30 Sep story holds half of its 28 Sep posts, so it is not scored')
        self.assertEqual(rows[0]['actualBridged'], 7)


class IssueReadme(unittest.TestCase):
    """The issue README gives both clocks and the 3 October rule."""

    def test_lag_beside_horizon(self) -> None:
        fc = {'frozenUtc': '2026-10-07T07:00:00Z', 'frozenIst': 'x', 'origin': '2026-10-06', 'model': 'gbm_med', 'note': None,
              'horizons': [horizon('2026-10-07', 1, DAY), horizon('2026-10-07', 2, RDAY)]}
        text = EI.issueReadme('2026-10-07', fc, [])
        self.assertIn('- D+1 (lag 2): target 2026-10-08', text)
        self.assertIn('- D+2 (lag 3): target 2026-10-09', text)
        self.assertNotIn('scored once', text)
        fc['horizons'].append({**horizon('2026-10-07', 3, '2026-10-10')})
        self.assertIn('- D+3 (lag 4): target 2026-10-10, frozen before that day began (UTC). This three-day target', EI.issueReadme('2026-10-07', fc, []))

    def test_legacy_notes_cite_3_october(self) -> None:
        for note in (v['note'] for v in EI.LEGACY.values()):
            self.assertNotIn('one and three days', note)
            self.assertIn('3 October 2026', note)
            self.assertIn('at most two days ahead', note)
        self.assertEqual(EI.horizonLabel(2, 2, 'origin reading'), '2 days ahead (lag 2)')
        self.assertEqual(EI.horizonLabel(1, 2, 'origin reading'), '1 day ahead (lag 2)')


if __name__ == '__main__':
    unittest.main()
