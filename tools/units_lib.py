"""Build the unit layer of the ledger: one small file per narrative, category, predicted post and account.

The unit files are the readable layer over the frozen parquet; the parquet stays the scoring population. Every
narrative and category unit carries `derivedFrom` (the frozen file, its sha256 and the rows it was built from), so
tools/verify.py can rebuild each unit from the parquet and fail on any difference.

Paths, relative to the ledger root:
  issues/<I>/categories/<region>/<category>.json
  issues/<I>/narratives/<region>/<category>/<key hex>.json
  issues/<I>/posts/<region>/<key hex>/<optionId>.json          (FWD-TWEETS-1)
  issues/<I>/accounts/<region>/<key hex>/<accountRef>.json     (FWD-TWEETS-1)
  outcomes/<T>/categories/... and outcomes/<T>/narratives/...  (the same units, scored)
  hourly/<day>/<HH>/<region>/<key>.json                        (once HOURLY-1 is registered)

Needs pandas and pyarrow (the parquet reader). Rebuilding a unit uses only the published folder, except the
key-to-category map of a forward issue, which comes from the frozen private input inputs/nk_meta.parquet whose
sha256 FREEZE.json holds; each category unit lists its member keys so the sums can be re-added by anyone.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

LEDGER = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
REGIONS = ('US', 'IN')
# US first, India second (3 Oct rule); the unit layer covers each region's busiest forecast stories.
TOP_BY_REGION = {'US': 50, 'IN': 20}
PAGE_TOP = 10
UNITS_FROM_ISSUE = '2026-10-04'
UNITS_FROM_OUTCOME = '2026-10-03'
UNIT_DIRS = ('categories', 'narratives', 'posts', 'accounts')
INDEX_NAMES = ('UNITS.json', 'UNITS.json.ots', 'OUTCOMES-UNITS.json', 'OUTCOMES-UNITS.json.ots')
PRED_RE = re.compile(r'^pred_(\d{4}-\d{2}-\d{2})_h(\d+)_K0\.parquet$')
PTOP10_NOTE = ('The frozen forecast holds no chance of reaching the region\'s top 10, so pTop10 is null; it was not '
               'added after the freeze because a number made later could not be shown to predate the target day.')
SHARE_NOTE = ('share is the category\'s forecast posts over the region\'s forecast posts, both summed over stories '
              'live at the origin with negative forecasts counted as zero (the rule of fwd_score.areaShareUS)')


def sha256File(path: str) -> str:
    """Hash a file.

    @param path: file path.
    @returns: hex sha256 of its bytes.
    @throws OSError: when unreadable.
    """
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def slug(text: Optional[str]) -> str:
    """A category name as a path segment.

    @param text: category name, or None.
    @returns: lower-case words joined by hyphens; 'uncategorised' for an empty name.
    @throws: nothing.
    """
    s = re.sub(r'[^a-z0-9]+', '-', str(text or '').lower()).strip('-')
    return s or 'uncategorised'


def keyHex(key: str) -> str:
    """File name stem of a narrative key: the part after the region prefix.

    @param key: Arbiter key such as 'US:0aeba461cad123cd'.
    @returns: '0aeba461cad123cd'.
    @throws: nothing.
    """
    return key.split(':', 1)[1] if ':' in key else key


def dumps(obj: object) -> str:
    """Serialise a unit the one way every tool writes it.

    @param obj: unit object.
    @returns: JSON text with a trailing newline.
    @throws TypeError: when the object holds a value JSON cannot hold.
    """
    return json.dumps(obj, indent=1, ensure_ascii=False, allow_nan=False) + '\n'


def num(x: object) -> Optional[float]:
    """A parquet number as a plain float, keeping missing values as None.

    @param x: value read from the parquet.
    @returns: float, or None for NaN or None.
    @throws: nothing.
    """
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def horizonName(h: int, countedFrom: str) -> str:
    """The horizon label used as a key in unit files.

    @param h: horizon in days.
    @param countedFrom: 'issue day' or 'origin reading' (forecast.json horizonCountedFrom).
    @returns: 'D+1', or '2 days ahead' for an issue counted from its origin reading.
    @throws: nothing.
    """
    return f'D+{h}' if countedFrom == 'issue day' else f'{h} day{"" if h == 1 else "s"} ahead'


def readJson(path: str) -> dict:
    """Read a JSON file.

    @param path: file path.
    @returns: parsed object.
    @throws OSError: when unreadable.
    @throws ValueError: when not JSON.
    """
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


class IssueData:
    """The frozen tables of one published issue folder, read once."""

    def __init__(self, ledger: str, issueDay: str, categoryOf: Optional[Dict[str, str]] = None,
                 keyOf: Optional[Dict[int, Tuple[str, str]]] = None) -> None:
        """Load forecast.json, FREEZE.json and every K = 0 parquet the freeze lists.

        @param ledger: ledger root.
        @param issueDay: issue folder name.
        @param categoryOf: {narrativeKey: category} from the frozen nk_meta input; None when the parquet carries a
            category column (legacy issues) or no category is known.
        @param keyOf: {nk: (narrativeKey, title)} for legacy parquets that hold only the integer nk.
        @returns: None.
        @throws FileNotFoundError: when the folder or a listed parquet is absent.
        """
        self.ledger, self.issueDay = ledger, issueDay
        self.folder = os.path.join(ledger, 'issues', issueDay)
        self.forecast = readJson(os.path.join(self.folder, 'forecast.json'))
        self.freeze = readJson(os.path.join(self.folder, 'FREEZE.json'))
        self.model = self.forecast['model'].split('|')[0]
        self.countedFrom = {h['targetDay']: h.get('horizonCountedFrom', 'issue day') for h in self.forecast['horizons']}
        self.lagOf = {h['targetDay']: h.get('lagFromOriginDays') for h in self.forecast['horizons']}
        self.targets: List[dict] = []
        for name in sorted(self.freeze['files']):
            m = PRED_RE.match(name)
            if not m:
                continue
            path = os.path.join(self.folder, name)
            df = pd.read_parquet(path)
            df['model'] = df['model'].astype(str)
            keyed = 'narrativeKey' in df.columns
            if not keyed:
                keyOf = keyOf or {}
                df['narrativeKey'] = [keyOf.get(int(n), (f'nk:{int(n)}', None))[0] for n in df['nk']]
                df['title'] = [keyOf.get(int(n), (None, None))[1] for n in df['nk']]
            if 'category' in df.columns:
                df['category'] = df['category'].astype(str)
            elif categoryOf is not None:
                df['category'] = [categoryOf.get(k, 'uncategorised') for k in df['narrativeKey']]
            else:
                df['category'] = 'uncategorised'
            df['category'] = [c if c and c != 'nan' else 'uncategorised' for c in df['category']]
            self.targets.append({'file': name, 'sha256': sha256File(path), 'day': m.group(1), 'h': int(m.group(2)),
                                 'rowKey': 'narrativeKey' if keyed else 'nk', 'df': df})
        self.targets.sort(key=lambda t: (t['h'], t['day']))
        if not self.targets:
            raise FileNotFoundError(f'issues/{issueDay}: FREEZE.json lists no K0 parquet')

    def horizonOf(self, t: dict) -> str:
        """Horizon label of one target.

        @param t: entry of self.targets.
        @returns: e.g. 'D+1'.
        @throws: nothing.
        """
        return horizonName(t['h'], self.countedFrom.get(t['day'], 'issue day'))

    def rows(self, t: dict, region: str, model: Optional[str] = None) -> pd.DataFrame:
        """Rows of one target for a region and model, ranked as fwd_freeze ranks them (yhat down, nk up).

        @param t: entry of self.targets.
        @param region: 'US' or 'IN'.
        @param model: model name (default: the headline model).
        @returns: the ranked rows with a 'rank' column starting at 1.
        @throws: nothing.
        """
        df = t['df']
        g = df[(df['model'] == (model or self.model)) & (df['region'] == region)]
        g = g.sort_values(['yhat', 'nk'], ascending=[False, True], kind='stable').copy()
        g['rank'] = range(1, len(g) + 1)
        return g


def selection(data: IssueData, extra: Optional[Dict[str, Iterable[str]]] = None) -> Dict[str, Dict[str, List[str]]]:
    """Which keys get a narrative unit, and why.

    The top TOP_BY_REGION keys of the first target by the headline forecast, every key of each target's published
    top 10 (forecast.json, what the Tomorrow page shows), and every key a FWD-TWEETS-1 issue covers.

    @param data: the issue's tables.
    @param extra: {region: keys} from FWD-TWEETS-1, or None.
    @returns: {region: {key: [reasons]}} for keys present in the parquet.
    @throws: nothing.
    """
    out: Dict[str, Dict[str, List[str]]] = {R: {} for R in REGIONS}
    first = data.targets[0]
    for R in REGIONS:
        g = data.rows(first, R)
        present = set(g['narrativeKey'])
        for k in g['narrativeKey'].head(TOP_BY_REGION[R]):
            out[R].setdefault(k, []).append(f'{R} top {TOP_BY_REGION[R]} by the {data.horizonOf(first)} forecast')
        for h in data.forecast['horizons']:
            for row in (h['regions'].get(R) or {}).get('top', [])[:PAGE_TOP]:
                k = row['arbiterKey']
                if k in present:
                    reason = f'published top 10 for {h["targetDay"]}'
                    if reason not in out[R].setdefault(k, []):
                        out[R][k].append(reason)
        for k in (extra or {}).get(R, []):
            if k in present:
                out[R].setdefault(k, []).append('covered by FWD-TWEETS-1')
    return out


def narrativeHorizon(data: IssueData, t: dict, region: str, key: str) -> dict:
    """The forecast of one key for one target, with its baselines.

    @param data: the issue's tables.
    @param t: entry of data.targets.
    @param region: region.
    @param key: narrative key.
    @returns: {targetDay, lag, posts, lo80, hi80, pAnyPost, pTop10, asHeldLatestDay, rank, baselines}.
    @throws KeyError: when the key is absent from the headline rows.
    """
    g = data.rows(t, region)
    row = g[g['narrativeKey'] == key]
    if row.empty:
        raise KeyError(f'{key} not in {t["file"]}')
    r = row.iloc[0]
    df = t['df']
    base = {}
    for m in sorted(set(df['model'])):
        if m == data.model:
            continue
        b = df[(df['model'] == m) & (df['narrativeKey'] == key)]
        if not b.empty:
            base[m] = num(b.iloc[0]['yhat'])
    return {'targetDay': t['day'], 'lag': data.lagOf.get(t['day']), 'posts': num(r['yhat']), 'lo80': num(r['lo']),
            'hi80': num(r['hi']), 'pAnyPost': num(r.get('pAct')), 'pTop10': None, 'asHeldLatestDay': num(r.get('x0')),
            'rank': int(r['rank']), 'storiesForecast': int(len(g)), 'baselines': base}


def rowRef(data: IssueData, t: dict, key: str, nk: int, models: str = 'every model') -> dict:
    """derivedFrom entry for one key in one frozen file.

    @param data: the issue's tables.
    @param t: entry of data.targets.
    @param key: narrative key.
    @param nk: integer key of the row.
    @param models: which model rows were read.
    @returns: {file, sha256, row}.
    @throws: nothing.
    """
    row = {'narrativeKey': key} if t['rowKey'] == 'narrativeKey' else {'nk': int(nk)}
    return {'file': f'issues/{data.issueDay}/{t["file"]}', 'sha256': t['sha256'], 'row': row, 'models': models}


def narrativeUnit(data: IssueData, region: str, key: str, reasons: List[str]) -> dict:
    """One narrative unit of an issue.

    @param data: the issue's tables.
    @param region: region.
    @param key: narrative key.
    @param reasons: why the key has a unit.
    @returns: the unit object.
    @throws KeyError: when the key is absent from the first target.
    """
    first = data.targets[0]
    g = data.rows(first, region)
    r = g[g['narrativeKey'] == key].iloc[0]
    horizons, derived = {}, []
    for t in data.targets:
        gg = data.rows(t, region)
        if key not in set(gg['narrativeKey']):
            continue
        horizons[data.horizonOf(t)] = narrativeHorizon(data, t, region, key)
        derived.append(rowRef(data, t, key, int(gg[gg['narrativeKey'] == key].iloc[0]['nk'])))
    return {'unit': 'narrative forecast', 'issueDay': data.issueDay, 'region': region, 'arbiterKey': key,
            'title': r['title'] if isinstance(r['title'], str) else None, 'category': str(r['category']),
            'model': data.model, 'selectedBecause': reasons, 'horizons': horizons, 'pTop10Note': PTOP10_NOTE,
            'derivedFrom': derived}


def categoryTotals(data: IssueData, t: dict, region: str, model: str) -> Dict[str, Tuple[float, List[str]]]:
    """Forecast posts per category for one target, model and region, with the member keys.

    @param data: the issue's tables.
    @param t: entry of data.targets.
    @param region: region.
    @param model: model name.
    @returns: {category: (posts, sorted member keys)}; posts are summed with math.fsum in key order.
    @throws: nothing.
    """
    df = t['df']
    g = df[(df['model'] == model) & (df['region'] == region)]
    out: Dict[str, Tuple[float, List[str]]] = {}
    for cat, gc in g.groupby('category'):
        gc = gc.sort_values('narrativeKey')
        out[str(cat)] = (math.fsum(max(float(y), 0.0) for y in gc['yhat']), list(gc['narrativeKey']))
    return out


def regionTotal(t: dict, region: str, model: str) -> float:
    """Forecast posts of a region for one target and model, negative forecasts counted as zero.

    Summed with math.fsum over the rows in key order, so it does not depend on how keys are grouped into categories.

    @param t: entry of IssueData.targets.
    @param region: region.
    @param model: model name.
    @returns: the total.
    @throws: nothing.
    """
    df = t['df']
    g = df[(df['model'] == model) & (df['region'] == region)].sort_values('narrativeKey')
    return math.fsum(max(float(y), 0.0) for y in g['yhat'])


def categoryUnits(data: IssueData, region: str) -> List[dict]:
    """Every category unit of an issue for one region, busiest first by the first target.

    @param data: the issue's tables.
    @param region: region.
    @returns: unit objects in commit order.
    @throws: nothing.
    """
    per = {}
    for t in data.targets:
        head = categoryTotals(data, t, region, data.model)
        tot = regionTotal(t, region, data.model)
        base = {m: categoryTotals(data, t, region, m) for m in sorted(set(t['df']['model'])) if m != data.model}
        btot = {m: regionTotal(t, region, m) for m in base}
        per[data.horizonOf(t)] = (t, head, tot, base, btot)
    firstH = data.horizonOf(data.targets[0])
    cats = sorted(per[firstH][1], key=lambda c: (-per[firstH][1][c][0], c))
    units = []
    for cat in cats:
        horizons, derived, members = {}, [], None
        for hname, (t, head, tot, base, btot) in per.items():
            if cat not in head:
                continue
            posts, keys = head[cat]
            members = keys if members is None else members
            horizons[hname] = {'targetDay': t['day'], 'lag': data.lagOf.get(t['day']), 'posts': posts,
                               'regionPosts': tot, 'share': posts / tot if tot > 0 else None, 'stories': len(keys),
                               'baselines': {m: {'posts': b.get(cat, (0.0, []))[0],
                                                 'share': (b.get(cat, (0.0, []))[0] / btot[m]) if btot[m] > 0 else None}
                                             for m, b in base.items()}}
            derived.append({'file': f'issues/{data.issueDay}/{t["file"]}', 'sha256': t['sha256'],
                            'row': {'region': region, 'narrativeKey': 'members'}, 'models': 'every model'})
        units.append({'unit': 'category forecast', 'issueDay': data.issueDay, 'region': region, 'category': cat,
                      'model': data.model, 'members': members or [], 'horizons': horizons, 'shareRule': SHARE_NOTE,
                      'categorySource': categorySource(data), 'derivedFrom': derived})
    return units


def categorySource(data: IssueData) -> dict:
    """Where the key-to-category map of an issue came from.

    @param data: the issue's tables.
    @returns: {file, sha256, public}.
    @throws: nothing.
    """
    files = data.freeze['files']
    for name in ('inputs/nk_meta.parquet', 'input:data/tomorrow_tournament/live/nk_meta.parquet'):
        if name in files:
            return {'file': name, 'sha256': files[name], 'public': False,
                    'note': 'a frozen input of the research repo; FREEZE.json holds its sha256'}
    return {'file': None, 'sha256': None, 'public': False, 'note': 'no category input listed in FREEZE.json'}


def short(text: Optional[str], n: int = 70) -> str:
    """A title cut for a commit message.

    @param text: title or None.
    @param n: most characters kept.
    @returns: the title, with an ellipsis when cut, and with double quotes made single.
    @throws: nothing.
    """
    s = ' '.join(str(text or 'untitled').replace('"', "'").split())
    return s if len(s) <= n else s[:n - 3].rstrip() + '...'


def fmt(x: Optional[float]) -> str:
    """A count for a commit message.

    @param x: number or None.
    @returns: whole number at 10 and above, one decimal below, 'none' for None.
    @throws: nothing.
    """
    if x is None:
        return 'none'
    return f'{x:.0f}' if abs(x) >= 10 else f'{x:.1f}'


def narrativeMessage(u: dict) -> str:
    """Commit message of an issue narrative unit.

    @param u: narrative unit.
    @returns: one line such as 'predict 2026-10-04 US sports "Title": D+1 120 posts [40, 300], any post 0.62; D+2 ...'.
    @throws: nothing.
    """
    parts = []
    for h, v in u['horizons'].items():
        p = f'{h} {fmt(v["posts"])} posts [{fmt(v["lo80"])}, {fmt(v["hi80"])}]'
        if v.get('pAnyPost') is not None:
            p += f', any post {v["pAnyPost"]:.2f}'
        parts.append(p)
    return f'predict {u["issueDay"]} {u["region"]} {u["category"]} "{short(u["title"])}": ' + '; '.join(parts)


def categoryMessage(u: dict) -> str:
    """Commit message of an issue category unit.

    @param u: category unit.
    @returns: one line.
    @throws: nothing.
    """
    parts = [f'{h} {fmt(v["posts"])} posts ({100 * (v["share"] or 0):.1f} percent of forecast posts)'
             for h, v in u['horizons'].items()]
    return f'predict {u["issueDay"]} {u["region"]} category {u["category"]}: ' + '; '.join(parts)


def buildIssueUnits(data: IssueData, extra: Optional[Dict[str, Iterable[str]]] = None) -> List[Tuple[str, dict, str]]:
    """Every narrative and category unit of one issue, in commit order.

    Order: categories (US then India, busiest first), then narratives by the first target's rank (US then India).

    @param data: the issue's tables.
    @param extra: FWD-TWEETS-1 keys per region, or None.
    @returns: [(path relative to the ledger, unit object, commit message)].
    @throws KeyError: when a selected key is absent.
    """
    out = []
    base = f'issues/{data.issueDay}'
    for R in REGIONS:
        for u in categoryUnits(data, R):
            out.append((f'{base}/categories/{R}/{slug(u["category"])}.json', u, categoryMessage(u)))
    sel = selection(data, extra)
    first = data.targets[0]
    for R in REGIONS:
        g = data.rows(first, R)
        rankOf = dict(zip(g['narrativeKey'], g['rank']))
        for k in sorted(sel[R], key=lambda k: (rankOf[k], k)):
            u = narrativeUnit(data, R, k, sel[R][k])
            out.append((f'{base}/narratives/{R}/{slug(u["category"])}/{keyHex(k)}.json', u, narrativeMessage(u)))
    return out


def rebuildIssueUnit(data: IssueData, path: str, unit: dict) -> dict:
    """Rebuild one published narrative or category unit from the frozen parquet, for verification.

    A category unit's member list is taken from the unit (the key-to-category map is a private frozen input); the
    rebuild then re-adds the parquet rows of those members, and the caller checks the member lists partition the
    region's keys.

    @param data: the issue's tables.
    @param path: unit path relative to the ledger.
    @param unit: the published unit.
    @returns: the rebuilt unit, to compare with the published one.
    @throws KeyError: when the unit names a key the parquet lacks.
    """
    if unit['unit'] == 'narrative forecast':
        u = narrativeUnit(data, unit['region'], unit['arbiterKey'], unit['selectedBecause'])
        # The key-to-category map is a private frozen input; verify checks it against the category units instead.
        u['category'] = unit['category']
        return u
    if unit['unit'] == 'category forecast':
        return categoryFromMembers(data, unit['region'], unit['category'], unit['members'])
    raise ValueError(f'{path}: unknown unit kind {unit.get("unit")}')


def categoryFromMembers(data: IssueData, region: str, category: str, members: List[str]) -> dict:
    """A category unit rebuilt from a given member list, for checks made without the private category map.

    Region totals do not depend on the category map, so they are rebuilt in full; only the members' own sums use the
    list.

    @param data: the issue's tables.
    @param region: region.
    @param category: category name.
    @param members: member keys.
    @returns: the category unit.
    @throws KeyError: when no parquet row matches the members.
    """
    override = {k: category for k in members}
    saved = []
    for t in data.targets:
        saved.append(t['df']['category'].copy())
        df = t['df']
        mask = df['region'] == region
        df.loc[mask, 'category'] = [override.get(k, '\x00other') for k in df.loc[mask, 'narrativeKey']]
    try:
        units = {u['category']: u for u in categoryUnits(data, region)}
    finally:
        for t, s in zip(data.targets, saved):
            t['df']['category'] = s
    if category not in units:
        raise KeyError(f'{data.issueDay} {region} {category}: no rows for its members')
    return units[category]


def frozenCategoryMap(research: str, researchFolder: str, freeze: dict) -> Optional[Dict[str, str]]:
    """Key-to-category map from the frozen nk_meta input of a forward issue, checked against FREEZE.json.

    @param research: research repo root.
    @param researchFolder: the issue's folder in the research repo (MANIFEST.json researchFolder).
    @param freeze: parsed FREEZE.json.
    @returns: {narrativeKey: category}, or None when the issue lists no such input.
    @throws ValueError: when the input's sha256 differs from FREEZE.json.
    """
    want = freeze['files'].get('inputs/nk_meta.parquet')
    if not want:
        return None
    path = os.path.join(research, researchFolder, 'inputs', 'nk_meta.parquet')
    got = sha256File(path)
    if got != want:
        raise ValueError(f'{path} sha256 {got[:16]} differs from FREEZE.json {want[:16]}')
    meta = pd.read_parquet(path).drop_duplicates('narrativeKey')
    return {k: (str(c) if c is not None and str(c) != 'nan' else 'uncategorised') for k, c in zip(meta['narrativeKey'], meta['category'])}


def liveKeyMap(research: str) -> Dict[int, Tuple[str, str]]:
    """{nk: (narrativeKey, title)} from the research live tables, for legacy parquets keyed by nk.

    @param research: research repo root.
    @returns: the map.
    @throws OSError: when nk_meta is absent.
    """
    meta = pd.read_parquet(os.path.join(research, 'data/tomorrow_tournament/live/nk_meta.parquet')).drop_duplicates('nk')
    return {int(n): (k, t if isinstance(t, str) else None) for n, k, t in zip(meta['nk'], meta['narrativeKey'], meta['title'])}


def loadIssue(ledger: str, issueDay: str, research: Optional[str]) -> IssueData:
    """Load an issue with its category map (forward) or key map (legacy) from the research repo.

    @param ledger: ledger root.
    @param issueDay: issue day.
    @param research: research repo root, or None (then categories come only from the parquet).
    @returns: IssueData.
    @throws ValueError: when a frozen input's hash differs.
    """
    folder = os.path.join(ledger, 'issues', issueDay)
    man = readJson(os.path.join(folder, 'MANIFEST.json'))
    freeze = readJson(os.path.join(folder, 'FREEZE.json'))
    cat = frozenCategoryMap(research, man['researchFolder'], freeze) if research else None
    keyOf = None
    first = next((n for n in sorted(freeze['files']) if PRED_RE.match(n)), None)
    if first and research:
        cols = pd.read_parquet(os.path.join(folder, first)).columns
        if 'narrativeKey' not in cols:
            keyOf = liveKeyMap(research)
    return IssueData(ledger, issueDay, cat, keyOf)


def fwdTweetsKeys(research: Optional[str], issueDay: str) -> Dict[str, List[str]]:
    """Narrative keys a FWD-TWEETS-1 issue covers, per region.

    @param research: research repo root, or None.
    @param issueDay: issue day.
    @returns: {region: keys}; empty when the issue has no FWD-TWEETS-1 file.
    @throws ValueError: when the file is not JSON.
    """
    out: Dict[str, List[str]] = {R: [] for R in REGIONS}
    if not research:
        return out
    p = os.path.join(research, 'data', 'fwd-tweets', f'issue-{issueDay}', 'predictions.json')
    if not os.path.exists(p):
        return out
    d = readJson(p)
    for n in d['narratives']:
        out.setdefault(d.get('region', 'US'), []).append(n['narrativeKey'])
    return out


def writeUnits(ledger: str, units: List[Tuple[str, dict, str]]) -> List[str]:
    """Write unit files, refusing to overwrite one that differs.

    @param ledger: ledger root.
    @param units: [(path, unit, message)].
    @returns: paths written or already identical.
    @throws FileExistsError: when a unit file exists with other content.
    """
    done = []
    for path, u, _ in units:
        full = os.path.join(ledger, path)
        text = dumps(u)
        if os.path.exists(full):
            with open(full, encoding='utf-8') as fh:
                if fh.read() != text:
                    raise FileExistsError(f'{path} exists with other content; a unit is never rewritten')
        else:
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, 'w', encoding='utf-8') as fh:
                fh.write(text)
        done.append(path)
    return done


def unitKindOf(path: str) -> str:
    """Unit type from its path.

    @param path: path relative to the ledger.
    @returns: 'category', 'narrative', 'post', 'account' or 'hour'.
    @throws ValueError: on a path outside the unit layer.
    """
    parts = path.split('/')
    if parts[0] == 'hourly':
        return 'hour'
    if len(parts) > 2 and parts[2] in UNIT_DIRS:
        return {'categories': 'category', 'narratives': 'narrative', 'posts': 'post', 'accounts': 'account'}[parts[2]]
    raise ValueError(f'{path} is not a unit path')


def isUnitLayer(rel: str) -> bool:
    """Whether a path inside an issue or outcome folder belongs to the unit layer (checked by UNITS.json, not MANIFEST.json).

    @param rel: path relative to the folder.
    @returns: True for unit directories and unit indexes.
    @throws: nothing.
    """
    return rel in INDEX_NAMES or rel.split('/')[0] in UNIT_DIRS


def privateHandleScan(texts: Iterable[Tuple[str, str]], handles: Iterable[str]) -> List[Tuple[str, str]]:
    """Find any private handle in public text.

    A handle matches as a whole word, case-insensitive, with or without '@'.

    @param texts: (where, text) pairs: file paths and commit messages.
    @param handles: lower-case private handles.
    @returns: [(where, handle)] for every hit.
    @throws: nothing.
    """
    hs = sorted({h.lower().lstrip('@') for h in handles if h and len(h.lstrip('@')) >= 1}, key=len, reverse=True)
    if not hs:
        return []
    hits = []
    for i in range(0, len(hs), 500):
        rx = re.compile(r'(?<![A-Za-z0-9_])(' + '|'.join(re.escape(h) for h in hs[i:i + 500]) + r')(?![A-Za-z0-9_])', re.IGNORECASE)
        for where, text in texts if isinstance(texts, list) else list(texts):
            for m in rx.finditer(text):
                hits.append((where, m.group(1).lower()))
    return sorted(set(hits))


def redactHandles(text: Optional[str], handles: Iterable[str]) -> Tuple[Optional[str], int]:
    """Replace every private handle in a public sentence.

    @param text: sentence, or None.
    @param handles: lower-case private handles.
    @returns: (text with each handle replaced by '[a private account]', number replaced).
    @throws: nothing.
    """
    if not text:
        return text, 0
    hs = sorted({h.lower().lstrip('@') for h in handles if h}, key=len, reverse=True)
    n = 0
    for i in range(0, len(hs), 500):
        rx = re.compile(r'@?(?<![A-Za-z0-9_])(' + '|'.join(re.escape(h) for h in hs[i:i + 500]) + r')(?![A-Za-z0-9_])', re.IGNORECASE)
        text, k = rx.subn('[a private account]', text)
        n += k
    return text, n


def accountRef(handle: str, salt: str) -> str:
    """Public reference of an account that does not pass PublicAccounts.decide.

    @param handle: handle, any case, with or without '@'.
    @param salt: the issue's salt, kept only in the private repo.
    @returns: 'sha256:' + sha256(lower(handle) + ':' + salt).
    @throws: nothing.
    """
    h = handle.lower().lstrip('@')
    if ':' in h and h.split(':', 1)[0] in ('twitter', 'x'):
        h = h.split(':', 1)[1]
    return 'sha256:' + hashlib.sha256(f'{h}:{salt}'.encode('utf-8')).hexdigest()


def refFile(ref: str) -> str:
    """File name stem for an accountRef.

    @param ref: '@handle' or 'sha256:<hex>'.
    @returns: 'handle' or 'sha256-<hex>'.
    @throws: nothing.
    """
    return ref.lstrip('@') if ref.startswith('@') else ref.replace(':', '-')


def check(cond: bool, msg: str) -> None:
    """Raise when a condition fails.

    @param cond: condition.
    @param msg: message.
    @returns: None.
    @throws ValueError: when cond is false.
    """
    if not cond:
        raise ValueError(msg)



# ---------------------------------------------------------------- outcomes

REAL_NOTE = ('real counts are the posts published on the day that Arbiter\'s reading of the day (T0) assigns to the story, '
             'from the research repo\'s private tables; the forecast side and every score are rebuilt from the frozen parquet')


def logError(actual: Optional[float], forecast: Optional[float]) -> Optional[float]:
    """|log(1 + actual) - log(1 + forecast)|, the ledger's main score.

    @param actual: real count, or None when unscored.
    @param forecast: forecast count (negative values count as zero), or None.
    @returns: the error, or None when either side is missing.
    @throws: nothing.
    """
    if actual is None or forecast is None:
        return None
    return abs(math.log1p(float(actual)) - math.log1p(max(float(forecast), 0.0)))


def brier(p: Optional[float], actual: Optional[float]) -> Optional[float]:
    """(P(any post) - 1 if the story got a post, else 0) squared.

    @param p: stated chance, or None.
    @param actual: real count, or None.
    @returns: the Brier score, or None when either side is missing.
    @throws: nothing.
    """
    if p is None or actual is None:
        return None
    return (float(p) - (1.0 if actual > 0 else 0.0)) ** 2


def realRanks(actual: pd.Series, meta: pd.DataFrame, region: str) -> Tuple[Dict[str, int], int]:
    """Real rank of each story in a region on the day: non-hub stories with at least one post, ties share the better rank.

    @param actual: posts by key on the day.
    @param meta: live nk_meta with narrativeKey, region, hub.
    @param region: region.
    @returns: ({key: rank}, number of stories with a post).
    @throws KeyError: when meta lacks a column.
    """
    m = meta.drop_duplicates('narrativeKey').set_index('narrativeKey')
    keys = [k for k in actual.index if k in m.index and m.at[k, 'region'] == region and not bool(m.at[k, 'hub']) and actual[k] > 0]
    s = pd.Series({k: float(actual[k]) for k in keys}, dtype=float)
    if s.empty:
        return {}, 0
    r = s.rank(method='min', ascending=False)
    return {k: int(v) for k, v in r.items()}, int(len(s))


def realCategoryTotals(actual: pd.Series, meta: pd.DataFrame, region: str) -> Dict[str, int]:
    """Real posts per category in a region on the day, over non-hub stories (the rule of fwd_score.areaShareUS).

    @param actual: posts by key.
    @param meta: live nk_meta with narrativeKey, region, hub, category.
    @param region: region.
    @returns: {category: posts}.
    @throws KeyError: when meta lacks a column.
    """
    m = meta.drop_duplicates('narrativeKey').set_index('narrativeKey')
    out: Dict[str, int] = {}
    for k, v in actual.items():
        if k in m.index and m.at[k, 'region'] == region and not bool(m.at[k, 'hub']):
            c = m.at[k, 'category']
            c = str(c) if c is not None and str(c) != 'nan' else 'uncategorised'
            out[c] = out.get(c, 0) + int(v)
    return out


def scoredIssueEntry(data: IssueData, t: dict, region: str, key: str, scoring: dict, actual: pd.Series,
                     ranks: Dict[str, int]) -> dict:
    """One issue's forecast of a key for the target day, scored.

    @param data: the issue's tables.
    @param t: the issue's target entry for the day.
    @param region: region.
    @param key: narrative key.
    @param scoring: {'kind': 'by key' | 'bridged' | 'not scored', 'mapping': {key: bridge row} | None, 'rule': str | None}.
    @param actual: posts by key on the day.
    @param ranks: real ranks of the region.
    @returns: the byIssue entry.
    @throws KeyError: when the key is absent from the target's rows.
    """
    fc = narrativeHorizon(data, t, region, key)
    g = data.rows(t, region)
    nk = int(g[g['narrativeKey'] == key].iloc[0]['nk'])
    kind, bridge, scored = scoring['kind'], None, None
    if kind == 'by key':
        scored = int(actual.get(key, 0))
    elif kind == 'bridged':
        m = (scoring.get('mapping') or {}).get(key)
        if m and m.get('matched'):
            to = m['bestKey']
            scored = int(actual.get(to, 0))
            bridge = {'to': to, 'share': m.get('bestShare'), 'toPosts': scored, 'toRank': ranks.get(to), 'rule': scoring.get('rule')}
        else:
            bridge = {'to': None, 'share': (m or {}).get('bestShare'), 'toPosts': None, 'toRank': None, 'rule': scoring.get('rule'),
                      'note': 'unmatched by the bridge, so not scored'}
    entry = {'issueDay': data.issueDay, 'horizon': data.horizonOf(t), 'scoring': kind, 'bridge': bridge, 'forecast': fc,
             'scoredActual': scored, 'logError': logError(scored, fc['posts']), 'brierAnyPost': brier(fc['pAnyPost'], scored),
             'inRange80': (None if scored is None or fc['lo80'] is None else bool(fc['lo80'] - 1e-9 <= scored <= fc['hi80'] + 1e-9)),
             'baselines': {m: {'posts': v, 'logError': logError(scored, v)} for m, v in fc['baselines'].items()},
             'issueUnit': None, 'derivedFrom': rowRef(data, t, key, nk)}
    return entry


def outcomeNarrativeUnits(day: str, issues: List[IssueData], extras: Dict[str, Dict[str, List[str]]], scoring: Dict[str, dict],
                          actual: pd.Series, meta: pd.DataFrame) -> List[Tuple[str, dict, str]]:
    """Narrative outcome units of a target day: one per selected key, with one entry per issue that forecast it.

    @param day: target day.
    @param issues: every issue that forecast the day, oldest first.
    @param extras: {issueDay: FWD-TWEETS-1 keys per region}.
    @param scoring: {issueDay: scoring record} (see scoredIssueEntry).
    @param actual: posts by key on the day.
    @param meta: live nk_meta.
    @returns: [(path, unit, message)] in commit order (US then India, by real rank, then by forecast rank).
    @throws KeyError: on a missing column.
    """
    out = []
    for R in REGIONS:
        ranks, nWith = realRanks(actual, meta, R)
        keys: Dict[str, dict] = {}
        for data in issues:
            t = next((x for x in data.targets if x['day'] == day), None)
            if t is None:
                continue
            for k in selection(data, extras.get(data.issueDay)).get(R, {}):
                keys.setdefault(k, {'entries': [], 'cat': None, 'title': None, 'firstRank': None})
            for k in list(keys):
                g = data.rows(t, R)
                row = g[g['narrativeKey'] == k]
                if row.empty:
                    continue
                rec = keys[k]
                if any(e['issueDay'] == data.issueDay for e in rec['entries']):
                    continue
                rec['entries'].append(scoredIssueEntry(data, t, R, k, scoring[data.issueDay], actual, ranks))
                rec['cat'] = str(row.iloc[0]['category'])
                rec['title'] = row.iloc[0]['title'] if isinstance(row.iloc[0]['title'], str) else rec['title']
                rec['firstRank'] = int(row.iloc[0]['rank']) if rec['firstRank'] is None else min(rec['firstRank'], int(row.iloc[0]['rank']))
        def order(k: str) -> tuple:
            rk = ranks.get(k)
            return (rk is None, rk or 0, keys[k]['firstRank'] or 0, k)
        for k in sorted(keys, key=order):
            rec = keys[k]
            if not rec['entries']:
                continue
            rec['entries'].sort(key=lambda e: e['issueDay'], reverse=True)
            u = {'unit': 'narrative outcome', 'day': day, 'region': R, 'arbiterKey': k, 'title': rec['title'], 'category': rec['cat'],
                 'real': {'posts': int(actual.get(k, 0)), 'rank': ranks.get(k), 'storiesWithPost': nWith}, 'realNote': REAL_NOTE,
                 'byIssue': rec['entries']}
            out.append((f'outcomes/{day}/narratives/{R}/{slug(rec["cat"])}/{keyHex(k)}.json', u, outcomeNarrativeMessage(u)))
    return out


def outcomeNarrativeMessage(u: dict) -> str:
    """Commit message of a narrative outcome unit.

    @param u: unit.
    @returns: one line such as 'score 2026-10-04 US sports "Title": D+1 of 2026-10-03 forecast 120, real 412, log error 1.23, rank 3'.
    @throws: nothing.
    """
    parts = []
    for e in u['byIssue']:
        head = f'{e["horizon"]} of {e["issueDay"]} forecast {fmt(e["forecast"]["posts"])}'
        if e['scoredActual'] is None:
            parts.append(f'{head}, not scored ({e["scoring"]}{", unmatched" if e["scoring"] == "bridged" else ""})')
            continue
        via = f' via bridge to {keyHex(e["bridge"]["to"])}' if e.get('bridge') and e['bridge'].get('to') else ''
        parts.append(f'{head}, real {e["scoredActual"]}{via}, log error {e["logError"]:.2f}')
    scored = [e for e in u['byIssue'] if e['scoredActual'] is not None]
    tail = ''
    if scored:
        e = scored[0]
        rank = u['real']['rank'] if e['scoring'] == 'by key' else (e.get('bridge') or {}).get('toRank')
        tail = f', rank {rank}' if rank else ', no post'
    return f'score {u["day"]} {u["region"]} {u["category"]} "{short(u["title"])}": ' + '; '.join(parts) + tail


def outcomeCategoryUnits(day: str, issues: List[IssueData], scoring: Dict[str, dict], actual: pd.Series,
                         meta: pd.DataFrame) -> List[Tuple[str, dict, str]]:
    """Category outcome units of a target day.

    @param day: target day.
    @param issues: every issue that forecast the day.
    @param scoring: {issueDay: scoring record}.
    @param actual: posts by key on the day.
    @param meta: live nk_meta.
    @returns: [(path, unit, message)], US then India, busiest real category first.
    @throws KeyError: on a missing column.
    """
    out = []
    for R in REGIONS:
        real = realCategoryTotals(actual, meta, R)
        regionReal = sum(real.values())
        per: Dict[str, List[dict]] = {}
        for data in issues:
            t = next((x for x in data.targets if x['day'] == day), None)
            if t is None:
                continue
            h = data.horizonOf(t)
            for u in categoryUnits(data, R):
                if h not in u['horizons']:
                    continue
                v = u['horizons'][h]
                rp = real.get(u['category'], 0)
                rs = rp / regionReal if regionReal > 0 else None
                kind = scoring[data.issueDay]['kind']
                mem = math.fsum(float(actual.get(k, 0)) for k in u['members']) if kind == 'by key' else None
                per.setdefault(u['category'], []).append({
                    'issueDay': data.issueDay, 'horizon': h, 'scoring': kind,
                    'forecast': {'posts': v['posts'], 'share': v['share'], 'regionPosts': v['regionPosts'], 'stories': v['stories']},
                    'realPostsOfMembers': mem,
                    'logError': logError(rp, v['posts']),
                    'shareError': None if (rs is None or v['share'] is None) else abs(v['share'] - rs),
                    'baselines': {m: {'posts': b['posts'], 'share': b['share'], 'logError': logError(rp, b['posts']),
                                      'shareError': None if (rs is None or b['share'] is None) else abs(b['share'] - rs)}
                                  for m, b in v['baselines'].items()},
                    'members': u['members'], 'issueUnit': None,
                    'derivedFrom': u['derivedFrom'][list(u['horizons']).index(h)]})
        cats = sorted(set(per) | set(real), key=lambda c: (-real.get(c, 0), c))
        for c in cats:
            entries = sorted(per.get(c, []), key=lambda e: e['issueDay'], reverse=True)
            u = {'unit': 'category outcome', 'day': day, 'region': R, 'category': c,
                 'real': {'posts': real.get(c, 0), 'regionPosts': regionReal, 'share': (real.get(c, 0) / regionReal) if regionReal > 0 else None},
                 'realNote': REAL_NOTE + '; real totals include stories that did not exist at the origin, so compare shares first',
                 'byIssue': entries}
            out.append((f'outcomes/{day}/categories/{R}/{slug(c)}.json', u, outcomeCategoryMessage(u)))
    return out


def outcomeCategoryMessage(u: dict) -> str:
    """Commit message of a category outcome unit.

    @param u: unit.
    @returns: one line.
    @throws: nothing.
    """
    rs = u['real']['share']
    head = f'score {u["day"]} {u["region"]} category {u["category"]}: real {u["real"]["posts"]} posts ({100 * (rs or 0):.1f} percent)'
    parts = [f'{e["horizon"]} of {e["issueDay"]} forecast {100 * (e["forecast"]["share"] or 0):.1f} percent'
             + (f', share error {100 * e["shareError"]:.1f} points' if e['shareError'] is not None else '') for e in u['byIssue']]
    return head + ('; ' + '; '.join(parts) if parts else '; no forecast')


def rebuildOutcomeUnit(issues: Dict[str, IssueData], unit: dict) -> dict:
    """Rebuild an outcome unit's forecast side and scores from the frozen parquet and the unit's own real counts.

    @param issues: {issueDay: IssueData} for every issue the unit names.
    @param unit: the published outcome unit.
    @returns: the rebuilt unit, to compare with the published one.
    @throws KeyError: when an issue or key is missing.
    """
    day, R = unit['day'], unit['region']
    if unit['unit'] == 'narrative outcome':
        entries = []
        for e in unit['byIssue']:
            data = issues[e['issueDay']]
            t = next(x for x in data.targets if x['day'] == day)
            actual = pd.Series({}, dtype=float)
            ranks: Dict[str, int] = {}
            mapping = None
            if e['scoring'] == 'by key':
                actual = pd.Series({unit['arbiterKey']: e['scoredActual']})
            elif e['scoring'] == 'bridged' and e['bridge'] and e['bridge'].get('to'):
                actual = pd.Series({e['bridge']['to']: e['scoredActual']})
                ranks = {e['bridge']['to']: e['bridge']['toRank']} if e['bridge']['toRank'] is not None else {}
                mapping = {unit['arbiterKey']: {'matched': True, 'bestKey': e['bridge']['to'], 'bestShare': e['bridge']['share']}}
            elif e['scoring'] == 'bridged':
                mapping = {unit['arbiterKey']: {'matched': False, 'bestShare': (e['bridge'] or {}).get('share')}}
            rule = (e.get('bridge') or {}).get('rule')
            ne = scoredIssueEntry(data, t, R, unit['arbiterKey'], {'kind': e['scoring'], 'mapping': mapping, 'rule': rule}, actual, ranks)
            ne['issueUnit'] = e.get('issueUnit')
            entries.append(ne)
        return {**unit, 'byIssue': entries}
    if unit['unit'] == 'category outcome':
        rp, regionReal = unit['real']['posts'], unit['real']['regionPosts']
        rs = (rp / regionReal) if regionReal > 0 else None
        entries = []
        for e in unit['byIssue']:
            data = issues[e['issueDay']]
            t = next(x for x in data.targets if x['day'] == day)
            h = data.horizonOf(t)
            cu = categoryFromMembers(data, R, unit['category'], e['members'])
            if h not in cu['horizons']:
                raise KeyError(f'{e["issueDay"]} has no {unit["category"]} forecast for {day}')
            v = cu['horizons'][h]
            entries.append({**e, 'forecast': {'posts': v['posts'], 'share': v['share'], 'regionPosts': v['regionPosts'], 'stories': v['stories']},
                            'logError': logError(rp, v['posts']),
                            'shareError': None if (rs is None or v['share'] is None) else abs(v['share'] - rs),
                            'baselines': {m: {'posts': b['posts'], 'share': b['share'], 'logError': logError(rp, b['posts']),
                                              'shareError': None if (rs is None or b['share'] is None) else abs(b['share'] - rs)}
                                          for m, b in v['baselines'].items()},
                            'derivedFrom': cu['derivedFrom'][list(cu['horizons']).index(h)]})
        return {**unit, 'real': {**unit['real'], 'share': rs}, 'byIssue': entries}
    raise ValueError(f'unknown outcome unit kind {unit.get("unit")}')
