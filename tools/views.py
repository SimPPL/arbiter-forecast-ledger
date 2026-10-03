"""Regenerate the views that grow every day: by-narrative/, by-category/, by-hour/ and SCOREBOARD.md.

Every view is derived from committed unit files and score files and is never edited by hand. Each row links to the
commit of the unit it comes from, so a reader can open the history of one story or one category and see each
forecast and its score as its own commit.

Usage: python3 tools/views.py            # rewrite every view in place
Standard library only.
"""
from __future__ import annotations

import json
import os
import re
import statistics
import sys
from typing import Dict, List, Optional, Tuple

LEDGER = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
REPO_URL = 'https://github.com/SimPPL/arbiter-forecast-ledger'
REGION_NAMES = {'US': 'United States', 'IN': 'India'}
MODEL_WORDS = {'gbm_med': 'boosted trees', 'gbm': 'boosted trees', 'persist': "yesterday's count", 'zero': 'every story dies',
               'volols': 'one-variable regression'}


def readJson(path: str) -> dict:
    """Read a JSON file.

    @param path: file path.
    @returns: parsed object.
    @throws OSError: when unreadable.
    """
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


def link(commit: Optional[str], text: str) -> str:
    """A markdown link to a commit.

    @param commit: full commit hash, or None.
    @param text: link text.
    @returns: the link, or a dash when there is no commit.
    @throws: nothing.
    """
    return f'[{text} {commit[:7]}]({REPO_URL}/commit/{commit})' if commit else '-'


def f3(x: Optional[float]) -> str:
    """A score with three decimals.

    @param x: number or None.
    @returns: text.
    @throws: nothing.
    """
    return '-' if x is None else f'{x:.3f}'


def cnt(x: Optional[float]) -> str:
    """A post count.

    @param x: number or None.
    @returns: whole number at 10 and above, one decimal below.
    @throws: nothing.
    """
    if x is None:
        return '-'
    return f'{x:.0f}' if abs(x) >= 10 else f'{x:.1f}'


def pct(x: Optional[float]) -> str:
    """A share in percent.

    @param x: share or None.
    @returns: text.
    @throws: nothing.
    """
    return '-' if x is None else f'{100 * x:.1f}%'


def pts(x: Optional[float]) -> str:
    """A difference of shares in percentage points.

    @param x: difference or None.
    @returns: text.
    @throws: nothing.
    """
    return '-' if x is None else f'{100 * x:.1f} points'


def indexes(ledger: str) -> List[Tuple[str, dict]]:
    """Every unit row of every committed index, oldest folder first.

    @param ledger: ledger root.
    @returns: [(kind, row)] where kind is 'issue' or 'outcome' and row holds path, unit, sha256 and commit.
    @throws OSError: when an index is unreadable.
    """
    out = []
    for kind, top, name in (('issue', 'issues', 'UNITS.json'), ('outcome', 'outcomes', 'OUTCOMES-UNITS.json')):
        root = os.path.join(ledger, top)
        if not os.path.isdir(root):
            continue
        for d in sorted(os.listdir(root)):
            n = 1
            while True:
                p = os.path.join(root, d, name if n == 1 else name.replace('.json', f'-{n}.json'))
                if not os.path.exists(p):
                    break
                out += [(kind, r) for r in readJson(p)['units']]
                n += 1
    return out


def narrativeRows(ledger: str, rows: List[Tuple[str, dict]]) -> Dict[Tuple[str, str], dict]:
    """Collect forecast and score rows per narrative.

    @param ledger: ledger root.
    @param rows: index rows.
    @returns: {(region, key): {'title', 'category', 'rows': {(issueDay, targetDay): {...}}}}.
    @throws OSError: when a unit file is unreadable.
    """
    out: Dict[Tuple[str, str], dict] = {}
    for kind, r in rows:
        if r['unit'] != 'narrative':
            continue
        u = readJson(os.path.join(ledger, r['path']))
        rec = out.setdefault((u['region'], u['arbiterKey']), {'title': u.get('title'), 'category': u.get('category'), 'rows': {}})
        rec['title'] = u.get('title') or rec['title']
        if kind == 'issue':
            for h, v in u['horizons'].items():
                x = rec['rows'].setdefault((u['issueDay'], v['targetDay']), {})
                x.update({'horizon': h, 'forecast': v['posts'], 'lo': v['lo80'], 'hi': v['hi80'], 'p': v['pAnyPost'],
                          'forecastCommit': r['commit']})
        else:
            for e in u['byIssue']:
                x = rec['rows'].setdefault((e['issueDay'], u['day']), {})
                x.setdefault('horizon', e['horizon'])
                x.setdefault('forecast', e['forecast']['posts'])
                x.setdefault('lo', e['forecast']['lo80'])
                x.setdefault('hi', e['forecast']['hi80'])
                x.setdefault('p', e['forecast']['pAnyPost'])
                x.update({'real': e['scoredActual'], 'logError': e['logError'], 'rank': u['real']['rank'] if e['scoring'] == 'by key'
                          else (e.get('bridge') or {}).get('toRank'), 'scoring': e['scoring'], 'scoreCommit': r['commit'],
                          'persistLogError': (e['baselines'].get('persist') or {}).get('logError')})
    return out


def narrativePage(region: str, key: str, rec: dict) -> str:
    """One by-narrative page.

    @param region: region.
    @param key: narrative key.
    @param rec: collected rows.
    @returns: markdown.
    @throws: nothing.
    """
    lines = [f'# {rec["title"] or key}', '',
             f'`{key}`, {REGION_NAMES.get(region, region)}, category {rec["category"]}. One row per issue that forecast this '
             'story for a day, with the forecast and the score each linked to its own commit. Generated by `tools/views.py`; '
             'do not edit by hand.', '',
             '| issue | target day | horizon | forecast posts | 80 percent range | chance of any post | real posts | log error | '
             "yesterday's count log error | real rank | forecast | score |",
             '|---|---|---|---|---|---|---|---|---|---|---|---|']
    for (i, t), x in sorted(rec['rows'].items(), key=lambda kv: (kv[0][1], kv[0][0])):
        real = cnt(x.get('real')) if x.get('real') is not None else ('not scored' if 'scoreCommit' in x else 'waiting')
        if x.get('scoring') == 'bridged' and x.get('real') is not None:
            real += ' (bridged)'
        lines.append(f'| {i} | {t} | {x.get("horizon", "-")} | {cnt(x.get("forecast"))} | [{cnt(x.get("lo"))}, {cnt(x.get("hi"))}] | '
                     f'{f3(x.get("p"))[:4] if x.get("p") is not None else "-"} | {real} | {f3(x.get("logError"))} | '
                     f'{f3(x.get("persistLogError"))} | {x.get("rank") or "-"} | {link(x.get("forecastCommit"), "forecast")} | '
                     f'{link(x.get("scoreCommit"), "score")} |')
    return '\n'.join(lines) + '\n'


def categoryRows(ledger: str, rows: List[Tuple[str, dict]]) -> Dict[Tuple[str, str], dict]:
    """Collect forecast and score rows per category.

    @param ledger: ledger root.
    @param rows: index rows.
    @returns: {(region, category): {(issueDay, targetDay): {...}}}.
    @throws OSError: when a unit file is unreadable.
    """
    out: Dict[Tuple[str, str], dict] = {}
    for kind, r in rows:
        if r['unit'] != 'category':
            continue
        u = readJson(os.path.join(ledger, r['path']))
        rec = out.setdefault((u['region'], u['category']), {})
        if kind == 'issue':
            for h, v in u['horizons'].items():
                rec.setdefault((u['issueDay'], v['targetDay']), {}).update(
                    {'horizon': h, 'share': v['share'], 'posts': v['posts'], 'forecastCommit': r['commit']})
        else:
            for e in u['byIssue']:
                x = rec.setdefault((e['issueDay'], u['day']), {})
                x.setdefault('horizon', e['horizon'])
                x.setdefault('share', e['forecast']['share'])
                x.setdefault('posts', e['forecast']['posts'])
                b = e['baselines']
                x.update({'realShare': u['real']['share'], 'realPosts': u['real']['posts'], 'shareError': e['shareError'],
                          'persistShareError': (b.get('persist') or {}).get('shareError'), 'logError': e['logError'],
                          'zeroLogError': (b.get('zero') or {}).get('logError'), 'persistLogError': (b.get('persist') or {}).get('logError'),
                          'scoreCommit': r['commit']})
    return out


def mean(xs: List[Optional[float]]) -> Optional[float]:
    """Mean of the values that exist.

    @param xs: values, None allowed.
    @returns: mean, or None when none exist.
    @throws: nothing.
    """
    v = [x for x in xs if x is not None]
    return sum(v) / len(v) if v else None


def categoryPage(region: str, cat: str, rec: dict) -> str:
    """One by-category page with running means against the baselines.

    @param region: region.
    @param cat: category.
    @param rec: collected rows.
    @returns: markdown.
    @throws: nothing.
    """
    lines = [f'# {cat}, {REGION_NAMES.get(region, region)}', '',
             'Share of the region\'s posts in this Arbiter category: forecast (stories live at the origin) beside real (every '
             'story of the day, new ones included), with the error of the forecast share and of yesterday\'s share. Log '
             'errors compare post totals, where every story dies forecasts zero. Generated by `tools/views.py`; do not edit by hand.', '',
             '| issue | target day | horizon | forecast share | real share | share error | yesterday\'s share error | '
             'forecast posts | real posts | log error | yesterday\'s count log error | every story dies log error | forecast | score |',
             '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    done = []
    for (i, t), x in sorted(rec.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        lines.append(f'| {i} | {t} | {x.get("horizon", "-")} | {pct(x.get("share"))} | {pct(x.get("realShare"))} | '
                     f'{pts(x.get("shareError"))} | {pts(x.get("persistShareError"))} | {cnt(x.get("posts"))} | {cnt(x.get("realPosts"))} | '
                     f'{f3(x.get("logError"))} | {f3(x.get("persistLogError"))} | {f3(x.get("zeroLogError"))} | '
                     f'{link(x.get("forecastCommit"), "forecast")} | {link(x.get("scoreCommit"), "score")} |')
        if 'scoreCommit' in x:
            done.append(x)
    if done:
        lines += ['', f'Running means over {len(done)} scored rows: share error {pts(mean([x["shareError"] for x in done]))} against '
                  f'{pts(mean([x["persistShareError"] for x in done]))} for yesterday\'s share; log error '
                  f'{f3(mean([x["logError"] for x in done]))} against {f3(mean([x["persistLogError"] for x in done]))} for yesterday\'s '
                  f'count and {f3(mean([x["zeroLogError"] for x in done]))} for every story dies.']
    return '\n'.join(lines) + '\n'


def hourPages(ledger: str) -> Dict[str, str]:
    """by-hour/<HH>.md pages from hourly outcome units, once hourly issues exist.

    @param ledger: ledger root.
    @returns: {relative path: markdown}; empty before the first hourly outcome.
    @throws OSError: when a unit is unreadable.
    """
    root = os.path.join(ledger, 'hourly-outcomes')
    if not os.path.isdir(root):
        return {}
    per: Dict[str, List[dict]] = {}
    for dirpath, _, names in os.walk(root):
        for n in sorted(names):
            if n.endswith('.json') and n != 'UNITS.json':
                u = readJson(os.path.join(dirpath, n))
                per.setdefault(str(u.get('hour', '')).zfill(2), []).append(u)
    pages = {}
    for hh, us in sorted(per.items()):
        errs = [u.get('logError') for u in us]
        pages[f'by-hour/{hh}.md'] = (f'# Hour {hh}:00 UTC\n\n{len(us)} scored hourly forecasts; mean log error '
                                     f'{f3(mean(errs))}. Generated by `tools/views.py`.\n')
    return pages


def scoreTable(ledger: str) -> List[dict]:
    """Whole-population scores per target day and issue from outcomes/*/score.json.

    @param ledger: ledger root.
    @returns: rows {day, issueDay, horizon, kind, trees, persist, zero}.
    @throws OSError: when a score file is unreadable.
    """
    rows = []
    root = os.path.join(ledger, 'outcomes')
    for d in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        p = os.path.join(root, d, 'score.json')
        if not os.path.exists(p):
            continue
        s = readJson(p)
        entries = s.get('byIssue') or [{'issueDay': None, 'horizonDays': s.get('horizonDays'), 'kind': s.get('kind'), 'models': s.get('models')}]
        for e in entries:
            m = e.get('models') or {}
            head = 'gbm_med' if 'gbm_med' in m else 'gbm'

            def val(name: str) -> Optional[float]:
                return ((m.get(name) or {}).get('logError') or {}).get('value')
            rows.append({'day': d, 'issueDay': e.get('issueDay'), 'horizon': e.get('horizonDays'), 'kind': e.get('kind'),
                         'trees': val(head), 'persist': val('persist'), 'zero': val('zero')})
    return rows


def median(xs: List[Optional[float]]) -> Optional[float]:
    """Median of the values that exist.

    @param xs: values.
    @returns: median or None.
    @throws: nothing.
    """
    v = [x for x in xs if x is not None]
    return statistics.median(v) if v else None


def scoreboard(ledger: str, rows: List[Tuple[str, dict]]) -> str:
    """SCOREBOARD.md: the benchmark so far, every number beside its baseline.

    @param ledger: ledger root.
    @param rows: index rows.
    @returns: markdown.
    @throws OSError: when a file is unreadable.
    """
    issues = sorted(d for d in os.listdir(os.path.join(ledger, 'issues')) if os.path.isdir(os.path.join(ledger, 'issues', d)))
    withUnits = [d for d in issues if os.path.exists(os.path.join(ledger, 'issues', d, 'UNITS.json'))]
    st = scoreTable(ledger)
    lines = ['# Scoreboard', '',
             'Generated by `tools/views.py` from the committed score files and unit files; do not edit by hand. Log error is the '
             'mean of |log(1 + real posts) minus log(1 + forecast)|, lower is better.', '',
             f'Issues published: {len(issues)} ({len(withUnits)} with one commit per unit). Target days scored: '
             f'{len(set(r["day"] for r in st))}.', '',
             '## Every story live at the origin', '',
             '| target day | issue | horizon | how scored | boosted trees | yesterday\'s count | every story dies |', '|---|---|---|---|---|---|---|']
    for r in st:
        lines.append(f'| {r["day"]} | {r["issueDay"] or "-"} | {r["horizon"] if r["horizon"] is not None else "-"} | {r["kind"]} | '
                     f'{f3(r["trees"])} | {f3(r["persist"])} | {f3(r["zero"])} |')
    byH: Dict[str, List[dict]] = {}
    for r in st:
        if r['kind'] == 'by key':
            byH.setdefault(str(r['horizon']), []).append(r)
    if byH:
        lines += ['', 'Median over days scored by story key, per horizon (bridged days left out, since they cover a selected slice):', '',
                  '| horizon (days) | days | boosted trees | yesterday\'s count | every story dies |', '|---|---|---|---|---|']
        for h, rs in sorted(byH.items()):
            lines.append(f'| {h} | {len(rs)} | {f3(median([r["trees"] for r in rs]))} | {f3(median([r["persist"] for r in rs]))} | '
                         f'{f3(median([r["zero"] for r in rs]))} |')
    nar = [readJson(os.path.join(ledger, r['path'])) for k, r in rows if k == 'outcome' and r['unit'] == 'narrative']
    ents = [e for u in nar for e in u['byIssue'] if e['scoredActual'] is not None]
    lines += ['', '## Stories with their own unit commits', '',
              f'Narratives scored as units: {len(nar)} story-days, {len(ents)} scored forecasts. These are the busiest forecast '
              'stories of each issue (US top 50, India top 20, the published top 10, and every FWD-TWEETS-1 story), a selected '
              'slice, so their errors run higher than the whole population above.', '']
    if ents:
        lines += ['| horizon | forecasts | median log error, boosted trees | yesterday\'s count | every story dies |', '|---|---|---|---|---|']
        for h in sorted({e['horizon'] for e in ents}):
            es = [e for e in ents if e['horizon'] == h]
            lines.append(f'| {h} | {len(es)} | {f3(median([e["logError"] for e in es]))} | '
                         f'{f3(median([(e["baselines"].get("persist") or {}).get("logError") for e in es]))} | '
                         f'{f3(median([(e["baselines"].get("zero") or {}).get("logError") for e in es]))} |')
    posts = [readJson(os.path.join(ledger, r['path'])) for k, r in rows if k == 'outcome' and r['unit'] == 'post']
    accts = [readJson(os.path.join(ledger, r['path'])) for k, r in rows if k == 'outcome' and r['unit'] == 'account']
    lines += ['', '## FWD-TWEETS-1: what will be said, and who will say it', '']
    if posts:
        lines += ['| seat | options | made | mean chance | Brier |', '|---|---|---|---|---|']
        for seat in sorted({p['seat'] for p in posts}):
            ps = [p for p in posts if p['seat'] == seat]
            lines.append(f'| {seat} | {len(ps)} | {sum(1 for p in ps if p["made"])} | {f3(mean([p["chance"] for p in ps]))} | '
                         f'{f3(mean([p["brier"] for p in ps]))} |')
        lines += ['', 'S2, continuation, is the baseline the seats must beat.']
    else:
        lines.append('No predicted post is scored yet. The first FWD-TWEETS-1 issue with unit commits is scored the morning after its target day.')
    lines.append('')
    if accts:
        models = sorted({m for a in accts for m in a['brier']})
        lines += ['| account model | rows | Brier |', '|---|---|---|']
        for m in models:
            lines.append(f'| {m} | {sum(1 for a in accts if a["brier"].get(m) is not None)} | {f3(mean([a["brier"].get(m) for a in accts]))} |')
        lines += ['', 'W1 is ACCT-1; W2, the recency table, is the reference it must beat.']
    else:
        lines.append('No account forecast is scored yet.')
    return '\n'.join(lines) + '\n'


def writeViews(ledger: str) -> List[str]:
    """Rewrite every view from the committed units.

    @param ledger: ledger root.
    @returns: paths written, relative to the ledger.
    @throws OSError: on a file system failure.
    """
    rows = indexes(ledger)
    pages: Dict[str, str] = {}
    for (R, k), rec in narrativeRows(ledger, rows).items():
        pages[f'by-narrative/{R}/{k.split(":", 1)[-1]}.md'] = narrativePage(R, k, rec)
    for (R, c), rec in categoryRows(ledger, rows).items():
        slugC = re.sub(r'[^a-z0-9]+', '-', c.lower()).strip('-') or 'uncategorised'
        pages[f'by-category/{R}/{slugC}.md'] = categoryPage(R, c, rec)
    pages.update(hourPages(ledger))
    pages['SCOREBOARD.md'] = scoreboard(ledger, rows)
    for rel, text in pages.items():
        full = os.path.join(ledger, rel)
        os.makedirs(os.path.dirname(full) or ledger, exist_ok=True)
        with open(full, 'w', encoding='utf-8') as fh:
            fh.write(text)
    return sorted(pages)


if __name__ == '__main__':
    print('\n'.join(writeViews(LEDGER)))
    sys.exit(0)
