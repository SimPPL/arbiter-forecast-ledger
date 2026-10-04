"""Tests for the unit layer: one commit per category, narrative, predicted post, account and hour.

Fixtures are synthetic and live in temporary directories: a git ledger with one forward issue (two targets, D+1 and
D+2, three models), a research tree holding the frozen category input and a FWD-TWEETS-1 issue with clear handles, and a
private repo holding that issue's predictions. Nothing touches the real repositories.

Covers LEDGER-UNIT-COMMITS-PLAN.md section 4: every unit rebuilds from the frozen parquet; a changed number fails verify;
no private handle reaches a public file or commit message; accountRef reproduces from the private salt; an hourly issue
committed after its hour is void; a renamed key carries its bridge record.

Run with a Python that has pandas and pyarrow:  <python> -m unittest discover -s tests -v
"""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, 'tools')
sys.path.insert(0, TOOLS)
import export_units as EX  # noqa: E402
import units_lib as U  # noqa: E402
import verify as V  # noqa: E402
import views as VW  # noqa: E402

PRIVATE_TOOL = os.environ.get('PRIVATE_UNITS_TOOL', os.path.expanduser('~/Documents/simppl/papers/arbiter-prediction-prereg/tools/units.py'))
ISSUE = '2026-10-07'
T1, T2 = '2026-10-08', '2026-10-09'
PRIVATE_HANDLE = 'quietperson_77'
PUBLIC_HANDLE = 'cbsnews'
# Category of each key in the frozen nk_meta input.
CATS = {'US:aaaa000000000001': 'sports', 'US:aaaa000000000002': 'politics', 'US:aaaa000000000003': 'sports',
        'US:aaaa000000000004': 'politics', 'IN:bbbb000000000001': 'politics', 'IN:bbbb000000000002': 'sports'}
# Headline forecast per key for D+1 (D+2 is half of it); ranks follow from these.
YHAT = {'US:aaaa000000000001': 40.0, 'US:aaaa000000000002': 30.0, 'US:aaaa000000000003': 7.5, 'US:aaaa000000000004': -0.2,
        'IN:bbbb000000000001': 12.0, 'IN:bbbb000000000002': 3.0}


def sh(cwd: str, *args: str, env: dict | None = None) -> str:
    """Run git in a directory.

    @param cwd: repo root.
    @param args: git arguments.
    @param env: extra environment.
    @returns: stdout.
    @throws subprocess.CalledProcessError: on failure.
    """
    e = dict(os.environ, **(env or {}))
    return subprocess.run(['git', '-C', cwd, *args], capture_output=True, text=True, check=True, env=e).stdout.strip()


def initRepo(path: str) -> None:
    """A git repo with a local identity and one empty commit.

    @param path: directory.
    @returns: None.
    @throws subprocess.CalledProcessError: on failure.
    """
    os.makedirs(path, exist_ok=True)
    sh(path, 'init', '-q', '-b', 'main')
    sh(path, 'config', 'user.email', 'test@example.org')
    sh(path, 'config', 'user.name', 'test')
    sh(path, 'commit', '-q', '--allow-empty', '-m', 'start')


def predFrame(day: str, h: int, scale: float) -> pd.DataFrame:
    """A frozen K0 prediction table in the forward format.

    @param day: target day.
    @param h: horizon.
    @param scale: multiplier on the headline forecast.
    @returns: rows for gbm_med, persist and zero.
    @throws: nothing.
    """
    rows = []
    for nk, (k, y) in enumerate(YHAT.items()):
        for m in ('gbm_med', 'persist', 'zero'):
            v = y * scale if m == 'gbm_med' else (5.0 + nk if m == 'persist' else 0.0)
            rows.append({'model': m, 'D': 280 + h, 'r': 279, 'lag': h + 1, 'K': 0, 'nk': nk, 'narrativeKey': k,
                         'title': f'story {k[-1]} by @{PUBLIC_HANDLE}' if nk == 0 else f'story {k[-1]}', 'region': k[:2],
                         'yhat': v, 'lo': max(v / 4, 0.0), 'hi': v * 3 + 1, 'pAct': min(0.05 + nk / 10, 0.9), 'x0': 5.0 + nk})
    return pd.DataFrame(rows)


class Fixture(unittest.TestCase):
    """A scratch ledger, research tree and private repo."""

    def setUp(self) -> None:
        """Build the three trees and point the tools at them.

        @returns: None.
        @throws OSError: on a file system failure.
        """
        self.tmp = tempfile.TemporaryDirectory()
        base = self.tmp.name
        self.ledger, self.research, self.private = (os.path.join(base, n) for n in ('ledger', 'research', 'private'))
        self._saved = (EX.LEDGER, V.LEDGER, dict(U.TOP_BY_REGION))
        EX.LEDGER = V.LEDGER = self.ledger
        initRepo(self.ledger)
        folder = os.path.join(self.ledger, 'issues', ISSUE)
        os.makedirs(folder)
        files = {}
        for name, day, h, scale in ((f'pred_{T1}_h1_K0.parquet', T1, 1, 1.0), (f'pred_{T2}_h2_K0.parquet', T2, 2, 0.5)):
            predFrame(day, h, scale).to_parquet(os.path.join(folder, name), index=False)
            files[name] = U.sha256File(os.path.join(folder, name))
        rfolder = 'cleanroom/fwd/forecasts/issue-' + ISSUE
        os.makedirs(os.path.join(self.research, rfolder, 'inputs'))
        meta = pd.DataFrame({'nk': range(len(CATS)), 'narrativeKey': list(CATS), 'category': list(CATS.values()),
                             'region': [k[:2] for k in CATS], 'hub': False, 'title': None})
        meta.to_parquet(os.path.join(self.research, rfolder, 'inputs', 'nk_meta.parquet'), index=False)
        files['inputs/nk_meta.parquet'] = U.sha256File(os.path.join(self.research, rfolder, 'inputs', 'nk_meta.parquet'))
        json.dump({'issueDay': ISSUE, 'origin': '2026-10-06', 'files': files, 'frozenIst': f'{ISSUE}T10:00:00+05:30',
                   'targets': [{'day': T1, 'h': 1}, {'day': T2, 'h': 2}]}, open(os.path.join(folder, 'FREEZE.json'), 'w'))
        horizons = []
        for h, day in ((1, T1), (2, T2)):
            horizons.append({'horizonDays': h, 'horizonCountedFrom': 'issue day', 'lagFromOriginDays': h + 1, 'targetDay': day,
                             'regions': {R: {'top': [{'rank': 1, 'arbiterKey': k, 'title': 't'} for k in YHAT if k[:2] == R][:1]}
                                         for R in ('US', 'IN')}})
        json.dump({'issueDay': ISSUE, 'model': 'gbm_med', 'horizons': horizons}, open(os.path.join(folder, 'forecast.json'), 'w'))
        json.dump({'researchFolder': rfolder, 'files': {}}, open(os.path.join(folder, 'MANIFEST.json'), 'w'))
        sh(self.ledger, 'add', '-A')
        sh(self.ledger, 'commit', '-q', '-m', 'issue')
        self.makeFwdTweets()

    def tearDown(self) -> None:
        """Restore the tools' roots and remove the trees.

        @returns: None.
        @throws: nothing.
        """
        EX.LEDGER, V.LEDGER = self._saved[0], self._saved[1]
        U.TOP_BY_REGION.clear()
        U.TOP_BY_REGION.update(self._saved[2])
        self.tmp.cleanup()

    def makeFwdTweets(self) -> None:
        """A FWD-TWEETS-1 issue: one story, one persona option naming a private account, two accounts (one private).

        @returns: None.
        @throws OSError: on a file system failure.
        """
        key = 'US:aaaa000000000001'
        privForm = 'sha256:' + hashlib.sha256(('researchsalt' + PRIVATE_HANDLE).encode()).hexdigest()
        full = {'region': 'US', 'targetDay': T1, 'whatModels': {'S3': 'narrative summary: google/gemma-4-31b-it reads posts'},
                'narratives': [{'narrativeKey': key, 'title': 'story 1', 'category': 'sports',
                                'what': {'S1': [{'text': f'People will quote {PRIVATE_HANDLE} about the trade', 'p': 0.7,
                                                 'persona': PRIVATE_HANDLE, 'account': privForm, 'public': False,
                                                 'seatModel': 'org/seat-model'}],
                                         'S2': [{'text': 'THE TRADE IS DONE', 'p': 0.6, 'authors': 3, 'posts': 4,
                                                 'exampleAuthor': 'another_private'}],
                                         'S3': {'model': 'google/gemma-4-31b-it', 'options': [{'text': 'The trade is final', 'p': 0.9}]}},
                                'who': {'W1': [{'handle': PRIVATE_HANDLE, 'account': privForm, 'public': False, 'p': 0.3},
                                               {'handle': PUBLIC_HANDLE, 'account': '@' + PUBLIC_HANDLE, 'public': True,
                                                'namedBecause': 'curated public list', 'p': 0.2}],
                                        'W2': [{'handle': PUBLIC_HANDLE, 'account': '@' + PUBLIC_HANDLE, 'public': True,
                                                'namedBecause': 'curated public list', 'p': 0.05}],
                                        'W3': []}}]}
        priv = json.loads(json.dumps(full))
        n = priv['narratives'][0]
        n['what']['S1'][0] = {k: v for k, v in n['what']['S1'][0].items() if k != 'account'}
        n['what']['S1'][0]['persona'] = privForm
        n['what']['S2'][0].pop('exampleAuthor')
        n['what']['S3'] = n['what']['S3']['options']
        for rows in n['who'].values():
            for r in rows:
                r.pop('handle')
        rdir = os.path.join(self.research, 'data', 'fwd-tweets', f'issue-{ISSUE}')
        os.makedirs(os.path.join(rdir, 'paraphrase'))
        json.dump(full, open(os.path.join(rdir, 'predictions-full.json'), 'w'))
        json.dump(priv, open(os.path.join(rdir, 'predictions.json'), 'w'))
        json.dump({'rows': [{'narrativeKey': key, 'original': 'THE TRADE IS DONE', 'paraphrase': 'The trade is done.'}]},
                  open(os.path.join(rdir, 'paraphrase', 's2.json'), 'w'))
        initRepo(self.private)
        pfolder = os.path.join(self.private, 'issues', ISSUE)
        os.makedirs(pfolder)
        json.dump(priv, open(os.path.join(pfolder, 'predictions.json'), 'w'))
        json.dump({'outputs': {'predictions.json': U.sha256File(os.path.join(pfolder, 'predictions.json'))}},
                  open(os.path.join(pfolder, 'MANIFEST.json'), 'w'))
        sh(self.private, 'add', '-A')
        sh(self.private, 'commit', '-q', '-m', 'issue')

    def privateTool(self):
        """Load the private repo's units.py pointed at the scratch private repo.

        @returns: the module.
        @throws unittest.SkipTest: never; a missing tool fails the test.
        """
        spec = importlib.util.spec_from_file_location('private_units', PRIVATE_TOOL)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.REPO = self.private
        return mod

    def publishIssue(self, withPrivate: bool = False) -> list:
        """Commit the issue's units the way commit_units.sh does, with no stamp.

        @param withPrivate: commit the private FWD-TWEETS-1 units first and include the public posts and accounts.
        @returns: the units committed.
        @throws EX.Refused: on a failed check.
        """
        if withPrivate:
            T = self.privateTool()
            pu = T.issueUnits(ISSUE, self.research, create=True)
            T.commitAll(pu)
            T.writeIndex(ISSUE, 'UNITS.json', pu, {'targetDay': T1}, None, [f'issues/{ISSUE}/units/ACCOUNT-SALT.txt'])
        units = EX.issueUnits(ISSUE, self.research, self.private if withPrivate else None)
        units.sort(key=lambda x: EX.COMMIT_ORDER[U.unitKindOf(x[0])])
        EX.scanUnits(units, EX.privateHandles(self.research))
        EX.commitUnits(self.ledger, units)
        EX.writeIndex(self.ledger, 'issue', ISSUE, units, None)
        return units

    def rebuild(self, where: str) -> dict:
        """Run verify's rebuild of one folder in this process.

        @param where: folder relative to the ledger.
        @returns: the JSON result.
        @throws ValueError: when the output is not JSON.
        """
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            V.rebuildFolder(where)
        return json.loads(buf.getvalue().strip().splitlines()[-1])

    def unitsCheck(self, folder: str, name: str = 'UNITS.json') -> V.Report:
        """Run verify's units check of one folder quietly.

        @param folder: absolute folder.
        @param name: index name.
        @returns: the report.
        @throws OSError: on an unreadable file.
        """
        rep = V.Report()
        with contextlib.redirect_stdout(io.StringIO()):
            V.checkUnits(rep, folder, name)
        return rep


class IssueUnits(Fixture):
    """Narrative and category units of an issue."""

    def test_units_and_commit_order(self) -> None:
        """Every category, then every narrative by D+1 rank (US first), each in its own commit, then the index."""
        units = self.publishIssue()
        paths = [p for p, _, _ in units]
        self.assertEqual(paths, [
            f'issues/{ISSUE}/categories/US/sports.json', f'issues/{ISSUE}/categories/US/politics.json',
            f'issues/{ISSUE}/categories/IN/politics.json', f'issues/{ISSUE}/categories/IN/sports.json',
            f'issues/{ISSUE}/narratives/US/sports/aaaa000000000001.json', f'issues/{ISSUE}/narratives/US/politics/aaaa000000000002.json',
            f'issues/{ISSUE}/narratives/US/sports/aaaa000000000003.json', f'issues/{ISSUE}/narratives/US/politics/aaaa000000000004.json',
            f'issues/{ISSUE}/narratives/IN/politics/bbbb000000000001.json', f'issues/{ISSUE}/narratives/IN/sports/bbbb000000000002.json'])
        log = sh(self.ledger, 'log', '--format=%s', '--reverse').split('\n')
        self.assertEqual(log[2], f'predict {ISSUE} US category sports: D+1 48 posts (61.3 percent of forecast posts); '
                                 'D+2 24 posts (61.3 percent of forecast posts)')
        self.assertEqual(log[6], f'predict {ISSUE} US sports "story 1 by @{PUBLIC_HANDLE}": D+1 40 posts [10, 121], any post 0.05; '
                                 'D+2 20 posts [5.0, 61], any post 0.05')
        self.assertTrue(log[-1].startswith(f'units {ISSUE}: index of 10 unit commits (4 category, 6 narrative)'))
        idx = json.load(open(os.path.join(self.ledger, 'issues', ISSUE, 'UNITS.json')))
        self.assertEqual(idx['counts'], {'category': 4, 'narrative': 6})
        for r in idx['units']:
            self.assertEqual(sh(self.ledger, 'diff-tree', '--no-commit-id', '--name-only', '-r', r['commit']), r['path'])

    def test_unit_values(self) -> None:
        """A narrative unit holds both horizons with baselines; a category unit sums its members with negatives at zero."""
        self.publishIssue()
        u = json.load(open(os.path.join(self.ledger, 'issues', ISSUE, 'narratives/US/politics/aaaa000000000004.json')))
        self.assertEqual(u['horizons']['D+1']['posts'], -0.2)
        self.assertEqual(u['horizons']['D+1']['rank'], 4)
        self.assertEqual(u['horizons']['D+1']['baselines'], {'persist': 8.0, 'zero': 0.0})
        self.assertIsNone(u['horizons']['D+2']['pTop10'])
        self.assertEqual(u['derivedFrom'][0]['row'], {'narrativeKey': 'US:aaaa000000000004'})
        c = json.load(open(os.path.join(self.ledger, 'issues', ISSUE, 'categories/US/politics.json')))
        self.assertEqual(c['members'], ['US:aaaa000000000002', 'US:aaaa000000000004'])
        self.assertEqual(c['horizons']['D+1']['posts'], 30.0)
        self.assertEqual(c['horizons']['D+1']['regionPosts'], 77.5)
        self.assertAlmostEqual(c['horizons']['D+1']['share'], 30.0 / 77.5, places=15)
        self.assertEqual(c['horizons']['D+1']['baselines']['persist']['posts'], 6.0 + 8.0)

    def test_rebuild_matches(self) -> None:
        """Every unit rebuilds from the frozen parquet, without the private category map."""
        self.publishIssue()
        res = self.rebuild(f'issues/{ISSUE}')
        self.assertEqual(res['failures'], [])
        self.assertEqual(res['checked'], 10)
        rep = self.unitsCheck(os.path.join(self.ledger, 'issues', ISSUE))
        self.assertEqual((rep.total, rep.failed), (1, 0))

    def test_changed_number_fails(self) -> None:
        """A changed forecast in a unit fails the rebuild, and its sha256 no longer matches UNITS.json."""
        self.publishIssue()
        p = os.path.join(self.ledger, 'issues', ISSUE, 'narratives/US/sports/aaaa000000000003.json')
        u = json.load(open(p))
        u['horizons']['D+1']['posts'] = 7.6
        open(p, 'w').write(U.dumps(u))
        res = self.rebuild(f'issues/{ISSUE}')
        self.assertEqual(res['failures'], [f'issues/{ISSUE}/narratives/US/sports/aaaa000000000003.json differs in horizons'])
        rep = self.unitsCheck(os.path.join(self.ledger, 'issues', ISSUE))
        self.assertEqual(rep.failed, 1)

    def test_changed_parquet_fails(self) -> None:
        """A parquet changed after the units were committed fails the rebuild (derivedFrom holds its sha256)."""
        self.publishIssue()
        path = os.path.join(self.ledger, 'issues', ISSUE, f'pred_{T1}_h1_K0.parquet')
        df = pd.read_parquet(path)
        df.loc[(df['model'] == 'gbm_med') & (df['narrativeKey'] == 'IN:bbbb000000000002'), 'yhat'] = 3.5
        df.to_parquet(path, index=False)
        res = self.rebuild(f'issues/{ISSUE}')
        # Every unit cites the file's sha256, and the region totals move, so all ten differ.
        self.assertEqual(len(res['failures']), 10)
        self.assertIn(f'issues/{ISSUE}/narratives/IN/sports/bbbb000000000002.json differs in derivedFrom, horizons', res['failures'])

    def test_category_partition(self) -> None:
        """Category members must partition the region's keys: a dropped member fails, and so does a narrative it lists."""
        self.publishIssue()
        p = os.path.join(self.ledger, 'issues', ISSUE, 'categories/US/politics.json')
        c = json.load(open(p))
        c['members'] = ['US:aaaa000000000002']
        c['horizons']['D+1']['posts'] = 30.0
        open(p, 'w').write(U.dumps(c))
        fails = self.rebuild(f'issues/{ISSUE}')['failures']
        self.assertIn('US category members cover 3 keys, the parquet holds 4', fails)
        self.assertIn(f'issues/{ISSUE}/narratives/US/politics/aaaa000000000004.json: its category politics does not list it', fails)

    def test_missing_selected_story(self) -> None:
        """A story in the region's top list with no unit fails the rebuild."""
        U.TOP_BY_REGION.update({'US': 2, 'IN': 1})
        units = self.publishIssue()
        self.assertEqual(sum(1 for p, _, _ in units if '/narratives/' in p), 3)
        self.assertEqual(self.rebuild(f'issues/{ISSUE}')['failures'], [])
        U.TOP_BY_REGION.update({'US': 3})
        self.assertEqual(self.rebuild(f'issues/{ISSUE}')['failures'], ['1 selected stories have no unit (first: US US:aaaa000000000003)'])

    def test_selection_reasons(self) -> None:
        """Selection takes the top N by D+1, the published top 10 and FWD-TWEETS-1 keys, with reasons."""
        U.TOP_BY_REGION.update({'US': 1, 'IN': 1})
        data = U.loadIssue(self.ledger, ISSUE, self.research)
        sel = U.selection(data, {'US': ['US:aaaa000000000003', 'US:zzzz']})
        self.assertEqual(sel['US'], {'US:aaaa000000000001': ['US top 1 by the D+1 forecast', f'published top 10 for {T1}',
                                                             f'published top 10 for {T2}'],
                                     'US:aaaa000000000003': ['covered by FWD-TWEETS-1']})
        self.assertEqual(sel['IN'], {'IN:bbbb000000000001': ['IN top 1 by the D+1 forecast', f'published top 10 for {T1}',
                                                             f'published top 10 for {T2}']})

    def test_two_units_in_one_commit_fail(self) -> None:
        """A unit commit that holds another path fails the units check."""
        self.publishIssue()
        idxp = os.path.join(self.ledger, 'issues', ISSUE, 'UNITS.json')
        idx = json.load(open(idxp))
        extra = os.path.join(self.ledger, 'issues', ISSUE, 'narratives/US/sports/extra.json')
        open(extra, 'w').write('{}\n')
        target = os.path.join(self.ledger, idx['units'][0]['path'])
        open(target, 'a').write(' ')
        sh(self.ledger, 'add', '-A')
        sh(self.ledger, 'commit', '-q', '-m', 'two at once')
        c = sh(self.ledger, 'rev-parse', 'HEAD')
        idx['units'][0].update({'commit': c, 'sha256': U.sha256File(target)})
        idx['units'].append({'path': f'issues/{ISSUE}/narratives/US/sports/extra.json', 'unit': 'narrative',
                             'sha256': U.sha256File(extra), 'commit': c})
        open(idxp, 'w').write(U.dumps(idx))
        rep = self.unitsCheck(os.path.join(self.ledger, 'issues', ISSUE))
        self.assertEqual(rep.failed, 1)

    def test_unlisted_unit_fails(self) -> None:
        """A unit file that UNITS.json does not list fails the units check."""
        self.publishIssue()
        open(os.path.join(self.ledger, 'issues', ISSUE, 'categories/US/added.json'), 'w').write('{}\n')
        self.assertEqual(self.unitsCheck(os.path.join(self.ledger, 'issues', ISSUE)).failed, 1)

    def test_never_rewritten_and_resumes(self) -> None:
        """Re-running commits nothing new; a unit with other content on disk refuses."""
        units = self.publishIssue()
        n = int(sh(self.ledger, 'rev-list', '--count', 'HEAD'))
        self.assertEqual(EX.commitUnits(self.ledger, units), 0)
        self.assertEqual(int(sh(self.ledger, 'rev-list', '--count', 'HEAD')), n)
        path, u, msg = units[4]
        u2 = json.loads(json.dumps(u))
        u2['horizons']['D+1']['posts'] = 1.0
        with self.assertRaises(EX.Refused):
            EX.commitUnits(self.ledger, [(path, u2, msg)])

    def test_added_selection_reason_is_skipped_not_refused(self) -> None:
        """A committed unit that differs only in why it was selected is left as committed: no rewrite, no refusal.

        The FWD-TWEETS-1 keys join the selection after the private issue lands, which adds a reason to units
        already published that morning. The forecast is unchanged, so the unit stands and the run goes on.
        """
        units = self.publishIssue()
        n = int(sh(self.ledger, 'rev-list', '--count', 'HEAD'))
        path, u, msg = units[4]
        before = open(os.path.join(self.ledger, path), encoding='utf-8').read()
        u2 = json.loads(json.dumps(u))
        u2['selectedBecause'] = list(u2.get('selectedBecause', [])) + ['FWD-TWEETS-1 key']
        self.assertEqual(EX.commitUnits(self.ledger, [(path, u2, msg)]), 0)
        self.assertEqual(int(sh(self.ledger, 'rev-list', '--count', 'HEAD')), n)
        self.assertEqual(open(os.path.join(self.ledger, path), encoding='utf-8').read(), before)
        # a changed number with a changed reason is still a different forecast and still refuses
        u3 = json.loads(json.dumps(u2))
        u3['horizons']['D+1']['posts'] = 1.0
        with self.assertRaises(EX.Refused):
            EX.commitUnits(self.ledger, [(path, u3, msg)])

    def test_manifest_ignores_unit_layer(self) -> None:
        """MANIFEST.json checks leave the unit layer to UNITS.json."""
        self.assertTrue(V.isUnitLayer('narratives/US/sports/a.json'))
        self.assertTrue(V.isUnitLayer('UNITS.json.ots'))
        self.assertFalse(V.isUnitLayer('forecast.json'))
        self.assertFalse(V.isUnitLayer('pred_x.parquet'))

    def test_early_issue_refused(self) -> None:
        """Issues before 2026-10-04 were single commits and are refused without --force-early."""
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = EX.main(['issue', '2026-10-03', '--research', self.research])
        self.assertEqual(code, 2)
        self.assertIn('before 2026-10-04 were published as single commits', out.getvalue())


class PrivateHandles(Fixture):
    """No private handle reaches a public file or message; accountRef reproduces from the private salt."""

    def test_account_ref_vector(self) -> None:
        """accountRef is sha256(lower(handle) + ':' + salt), with or without '@'."""
        want = 'sha256:' + hashlib.sha256(b'quietperson_77:abc').hexdigest()
        self.assertEqual(U.accountRef('@QuietPerson_77', 'abc'), want)
        self.assertEqual(U.accountRef('quietperson_77', 'abc'), want)
        self.assertNotEqual(U.accountRef('quietperson_77', 'abd'), want)

    def test_public_units_and_refs(self) -> None:
        """Public post and account units carry accountRef from the private salt, the paraphrase and a redacted sentence."""
        units = self.publishIssue(withPrivate=True)
        kinds = [U.unitKindOf(p) for p, _, _ in units]
        self.assertEqual(kinds, ['category'] * 4 + ['narrative'] * 6 + ['post'] * 3 + ['account'] * 2)
        salt = open(os.path.join(self.private, 'issues', ISSUE, 'units', 'ACCOUNT-SALT.txt')).read().strip()
        ref = 'sha256:' + hashlib.sha256(f'{PRIVATE_HANDLE}:{salt}'.encode()).hexdigest()
        acc = {u['accountRef']: u for p, u, _ in units if U.unitKindOf(p) == 'account'}
        self.assertEqual(sorted(acc), sorted([ref, '@' + PUBLIC_HANDLE]))
        self.assertEqual(acc[ref]['chances'], {'W1': 0.3, 'W2': None, 'W3': None})
        self.assertEqual(acc['@' + PUBLIC_HANDLE]['chances'], {'W1': 0.2, 'W2': 0.05, 'W3': None})
        posts = {u['optionId']: u for p, u, _ in units if U.unitKindOf(p) == 'post'}
        self.assertEqual(posts['S1-01']['sentence'], 'People will quote [a private account] about the trade')
        self.assertEqual(posts['S1-01']['redacted'], 1)
        self.assertEqual(posts['S1-01']['personaRef'], ref)
        self.assertEqual(posts['S2-01']['sentence'], 'The trade is done.')
        self.assertEqual(posts['S3-01']['model'], 'google/gemma-4-31b-it')
        T = self.privateTool()
        self.assertEqual(T.check(ISSUE, self.research, self.ledger), [])

    def test_late_private_units_get_next_index(self) -> None:
        """FWD-TWEETS-1 units committed after the issue's UNITS.json go into UNITS-2.json, and verify reads both."""
        self.publishIssue()
        self.publishIssue(withPrivate=True)
        first = json.load(open(os.path.join(self.ledger, 'issues', ISSUE, 'UNITS.json')))
        second = json.load(open(os.path.join(self.ledger, 'issues', ISSUE, 'UNITS-2.json')))
        self.assertEqual(first['counts'], {'category': 4, 'narrative': 6})
        self.assertEqual(second['counts'], {'post': 3, 'account': 2})
        self.assertEqual(second['follows'], [f'issues/{ISSUE}/UNITS.json'])
        rep = self.unitsCheck(os.path.join(self.ledger, 'issues', ISSUE))
        self.assertEqual((rep.total, rep.failed), (1, 0))
        self.assertEqual(self.rebuild(f'issues/{ISSUE}')['failures'], [])
        n = int(sh(self.ledger, 'rev-list', '--count', 'HEAD'))
        self.publishIssue(withPrivate=False)
        self.assertEqual(int(sh(self.ledger, 'rev-list', '--count', 'HEAD')), n)
        self.assertFalse(os.path.exists(os.path.join(self.ledger, 'issues', ISSUE, 'UNITS-3.json')))

    def test_no_private_handle_anywhere_public(self) -> None:
        """Scan every committed public file and message for every handle of the issue: none appears."""
        self.publishIssue(withPrivate=True)
        texts = __import__('scan_public').publicTexts(self.ledger, None, True)
        self.assertGreater(len(texts), 20)
        self.assertEqual(U.privateHandleScan(texts, EX.privateHandles(self.research)), [])
        self.assertEqual(sorted(EX.privateHandles(self.research)), ['another_private', PRIVATE_HANDLE])

    def test_scan_catches_a_leak(self) -> None:
        """A handle in a unit, a message or a committed file is caught; a public handle is not."""
        handles = EX.privateHandles(self.research)
        with self.assertRaises(EX.Refused):
            EX.scanUnits([('a.json', {'x': 1}, f'predict about @{PRIVATE_HANDLE.upper()}')], handles)
        with self.assertRaises(EX.Refused):
            EX.scanUnits([('a.json', {'x': f'{PRIVATE_HANDLE}'}, 'ok')], handles)
        EX.scanUnits([('a.json', {'x': f'{PRIVATE_HANDLE}x @{PUBLIC_HANDLE}'}, 'ok')], handles)
        open(os.path.join(self.ledger, 'leak.md'), 'w').write(f'hello @{PRIVATE_HANDLE}\n')
        sh(self.ledger, 'add', 'leak.md')
        sh(self.ledger, 'commit', '-q', '-m', 'leak')
        texts = __import__('scan_public').publicTexts(self.ledger, None, True)
        self.assertEqual(U.privateHandleScan(texts, handles), [('leak.md', PRIVATE_HANDLE)])

    def test_private_check_catches_changed_public_ref(self) -> None:
        """The private check fails when a public account unit's accountRef differs from its private unit."""
        self.publishIssue(withPrivate=True)
        d = os.path.join(self.ledger, 'issues', ISSUE, 'accounts', 'US', 'aaaa000000000001')
        name = next(n for n in os.listdir(d) if n.startswith('sha256-'))
        u = json.load(open(os.path.join(d, name)))
        u['accountRef'] = U.accountRef(PRIVATE_HANDLE, 'wrong salt')
        open(os.path.join(d, name), 'w').write(U.dumps(u))
        T = self.privateTool()
        self.assertEqual(T.check(ISSUE, self.research, self.ledger), [f'public accounts/{name} differs from its private unit'])


class Outcomes(Fixture):
    """Outcome units: by key, through a bridge, and the views."""

    def actual(self) -> tuple:
        """Real counts and live meta for T1.

        @returns: (posts by key, meta).
        @throws: nothing.
        """
        actual = pd.Series({'US:aaaa000000000001': 99, 'US:aaaa000000000002': 0, 'US:aaaa000000000003': 7, 'US:new1': 120,
                            'IN:bbbb000000000001': 4, 'IN:bbbb000000000002': 0})
        meta = pd.DataFrame({'narrativeKey': list(actual.index), 'region': [k[:2] for k in actual.index], 'hub': False,
                             'category': ['sports', 'politics', 'sports', 'politics', 'politics', 'sports']})
        return actual, meta

    def build(self, scoring: dict) -> list:
        """Outcome units of T1 for the fixture issue.

        @param scoring: the issue's scoring record.
        @returns: [(path, unit, message)].
        @throws KeyError: on a missing column.
        """
        data = U.loadIssue(self.ledger, ISSUE, self.research)
        actual, meta = self.actual()
        cats = U.outcomeCategoryUnits(T1, [data], {ISSUE: scoring}, actual, meta)
        nars = U.outcomeNarrativeUnits(T1, [data], {ISSUE: {}}, {ISSUE: scoring}, actual, meta)
        return cats + nars

    def test_by_key_scores(self) -> None:
        """Real count, rank among stories with a post (ties share the better rank), log error, Brier, range."""
        units = self.build({'kind': 'by key', 'mapping': None, 'rule': None})
        u = next(u for p, u, _ in units if u.get('arbiterKey') == 'US:aaaa000000000001')
        e = u['byIssue'][0]
        self.assertEqual(u['real'], {'posts': 99, 'rank': 2, 'storiesWithPost': 3})
        self.assertEqual(e['scoredActual'], 99)
        self.assertAlmostEqual(e['logError'], abs(__import__('math').log(100 / 41)), places=12)
        self.assertAlmostEqual(e['brierAnyPost'], (0.05 - 1) ** 2, places=12)
        self.assertTrue(e['inRange80'])
        self.assertAlmostEqual(e['baselines']['persist']['logError'], abs(__import__('math').log(100 / 6)), places=12)
        z = next(u for p, u, _ in units if u.get('arbiterKey') == 'US:aaaa000000000004')
        self.assertEqual(z['real'], {'posts': 0, 'rank': None, 'storiesWithPost': 3})
        self.assertEqual(z['byIssue'][0]['logError'], 0.0)
        c = next(u for p, u, _ in units if u['unit'] == 'category outcome' and u['region'] == 'US' and u['category'] == 'politics')
        self.assertEqual(c['real'], {'posts': 120, 'regionPosts': 226, 'share': 120 / 226})
        self.assertAlmostEqual(c['byIssue'][0]['shareError'], abs(30 / 77.5 - 120 / 226), places=12)
        self.assertEqual(c['byIssue'][0]['realPostsOfMembers'], 0.0)
        msg = next(m for p, u, m in units if u.get('arbiterKey') == 'US:aaaa000000000003')
        self.assertEqual(msg, f'score {T1} US sports "story 3": D+1 of {ISSUE} forecast 7.5, real 7, log error 0.06, rank 3')

    def test_bridge_record(self) -> None:
        """A renamed key is scored through its bridge and carries the bridge record; an unmatched key is not scored."""
        mapping = {'US:aaaa000000000001': {'narrativeKey': 'US:aaaa000000000001', 'matched': True, 'bestKey': 'US:new1', 'bestShare': 0.8},
                   'US:aaaa000000000002': {'narrativeKey': 'US:aaaa000000000002', 'matched': False, 'bestKey': None, 'bestShare': 0.1}}
        units = self.build({'kind': 'bridged', 'mapping': mapping, 'rule': 'half of the posts'})
        u = next(u for p, u, _ in units if u.get('arbiterKey') == 'US:aaaa000000000001')
        e = u['byIssue'][0]
        self.assertEqual(e['bridge'], {'to': 'US:new1', 'share': 0.8, 'toPosts': 120, 'toRank': 1, 'rule': 'half of the posts'})
        self.assertEqual(e['scoredActual'], 120)
        v = next(u for p, u, _ in units if u.get('arbiterKey') == 'US:aaaa000000000002')['byIssue'][0]
        self.assertIsNone(v['scoredActual'])
        self.assertEqual(v['bridge']['note'], 'unmatched by the bridge, so not scored')
        self.assertIsNone(v['logError'])
        w = next(u for p, u, _ in units if u.get('arbiterKey') == 'US:aaaa000000000003')['byIssue'][0]
        self.assertIsNone(w['scoredActual'])
        for p, u, _ in units:
            self.assertEqual(json.loads(U.dumps(U.rebuildOutcomeUnit({ISSUE: U.IssueData(self.ledger, ISSUE)}, json.loads(U.dumps(u))))),
                             json.loads(U.dumps(u)), p)

    def test_outcome_commit_rebuild_and_views(self) -> None:
        """Outcome units commit one by one, rebuild in verify, and the views link the forecast and score commits."""
        self.publishIssue()
        os.makedirs(os.path.join(self.ledger, 'outcomes', T1))
        units = self.build({'kind': 'by key', 'mapping': None, 'rule': None})
        EX.linkIssueUnits(units)
        EX.commitUnits(self.ledger, units)
        EX.writeIndex(self.ledger, 'outcome', T1, units, None)
        res = self.rebuild(f'outcomes/{T1}')
        self.assertEqual(res['failures'], [])
        self.assertEqual(res['checked'], len(units))
        idx = {r['path']: r['commit'] for r in json.load(open(os.path.join(self.ledger, 'issues', ISSUE, 'UNITS.json')))['units']}
        u = json.load(open(os.path.join(self.ledger, 'outcomes', T1, 'narratives/US/sports/aaaa000000000001.json')))
        ip = f'issues/{ISSUE}/narratives/US/sports/aaaa000000000001.json'
        self.assertEqual(u['byIssue'][0]['issueUnit'], {'path': ip, 'commit': idx[ip]})
        page = open(os.path.join(self.ledger, 'by-narrative', 'US', 'aaaa000000000001.md')).read()
        self.assertIn(f'| {ISSUE} | {T1} | D+1 | 40 | [10, 121] | 0.05 | 99 | 0.892 | 2.813 | 2 | [forecast {idx[ip][:7]}]', page)
        self.assertIn(f'| {ISSUE} | {T2} | D+2 | 20 | [5.0, 61] | 0.05 | waiting |', page)
        board = open(os.path.join(self.ledger, 'SCOREBOARD.md')).read()
        self.assertIn('| D+1 | 6 | ', board)
        p = os.path.join(self.ledger, 'outcomes', T1, 'narratives/US/sports/aaaa000000000003.json')
        x = json.load(open(p))
        x['byIssue'][0]['logError'] = 0.01
        open(p, 'w').write(U.dumps(x))
        self.assertEqual(self.rebuild(f'outcomes/{T1}')['failures'], [f'outcomes/{T1}/narratives/US/sports/aaaa000000000003.json differs from its rebuild'])


class Hourly(Fixture):
    """Hourly units: refused once the hour begins; verify marks a late hourly issue void."""

    def table(self) -> str:
        """A table with one hourly object for 14:00 on T1.

        @returns: its path.
        @throws OSError: on a write failure.
        """
        p = os.path.join(self.tmp.name, 'hourly.json')
        json.dump([{'asOf': f'{T1}T14:00Z', 'region': 'US', 'narrative': {'key': 'US:aaaa000000000001', 'title': 'story 1'},
                    'volume': {'forecastEndOfDay': 50}}], open(p, 'w'))
        return p

    def commitHour(self, when: str) -> None:
        """Commit hourly/<T1>/14/UNITS.json with a given committer time.

        @param when: ISO time.
        @returns: None.
        @throws subprocess.CalledProcessError: on failure.
        """
        units = EX.hourlyUnits(T1, 14, self.table())
        self.assertEqual(units[0][0], f'hourly/{T1}/14/US/aaaa000000000001.json')
        EX.commitUnits(self.ledger, units)
        rel = f'hourly/{T1}/14/UNITS.json'
        open(os.path.join(self.ledger, rel), 'w').write(U.dumps(EX.indexObject(self.ledger, 'hourly', f'{T1}T14', units)))
        sh(self.ledger, 'add', rel)
        sh(self.ledger, 'commit', '-q', '-m', 'hour', env={'GIT_COMMITTER_DATE': when, 'GIT_AUTHOR_DATE': when})

    def hourLines(self) -> list:
        """verify's hourly lines, offline.

        @returns: printed lines.
        @throws: nothing.
        """
        buf = io.StringIO()
        rep = V.Report()
        with contextlib.redirect_stdout(buf):
            V.checkHourly(rep, None, 'offline')
        return buf.getvalue().strip().splitlines()

    def test_late_hour_is_void(self) -> None:
        """An hourly issue committed after its hour began is flagged void."""
        self.commitHour(f'{T1}T14:00:05+00:00')
        lines = self.hourLines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith('FAIL'))
        self.assertIn('VOID: commit time only', lines[0])

    def test_early_hour_passes(self) -> None:
        """An hourly issue committed before its hour passes, labelled as the weaker commit clock."""
        self.commitHour(f'{T1}T13:59:00+00:00')
        lines = self.hourLines()
        self.assertTrue(lines[0].startswith('PASS'))
        self.assertIn(f'{T1}T13:59:00Z, before {T1}T14:00:00Z', lines[0])

    def test_export_refuses_once_hour_began(self) -> None:
        """The exporter refuses an hourly issue at or after its hour, and an object for another hour."""
        with self.assertRaises(EX.Refused):
            EX.runHourly(T1, 14, self.table(), False, None, self.research, now=dt.datetime(2026, 10, 8, 14, 0, tzinfo=dt.timezone.utc))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(EX.runHourly(T1, 14, self.table(), False, None, self.research,
                                          now=dt.datetime(2026, 10, 8, 13, 59, tzinfo=dt.timezone.utc)), 0)
        with self.assertRaises(EX.Refused):
            EX.hourlyUnits(T1, 15, self.table())


if __name__ == '__main__':
    unittest.main()
