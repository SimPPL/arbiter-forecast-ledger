"""Write outcomes/<day>/TABLE.md: each forecast's top 10 beside the real count and the real rank.

Two steps, so an outsider can redo the second without our research data:

  ranks   (needs --research and pandas) reads the research repo's live tables and writes
          outcomes/<day>/ranks.json: for every story a forecast listed, the real post count and the
          real rank among the region's stories that day (non-hub keys with at least one post, ranked
          by count, ties sharing the better rank), plus how many stories had a post.
  render  (standard library only) reads stories.json, score.json and ranks.json and writes
          TABLE.md. Without ranks.json a rank is shown only for stories in the real top 10.

Both steps add their file to MANIFEST.json, so tools/verify.py keeps passing.

Usage:
  <python with pandas> tools/outcome_table.py <day> --research <path>   # ranks, then render
  python3 tools/outcome_table.py <day>                                  # render only
Exit codes: 0 written, 2 refused (no outcome folder, or a computed rank disagrees with stories.json).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from typing import Dict, List, Optional

LEDGER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGIONS = ('US', 'IN')
REGION_NAMES = {'US': 'United States', 'IN': 'India'}
LIVE_DATA = 'data/tomorrow_tournament/live'
sys.path.insert(0, os.path.join(LEDGER, 'tools'))
import export_issue as EI  # noqa: E402  (standard library only)


def sha256(path: str) -> str:
    """Hash one file.

    @param path: file path.
    @returns: hex sha256 of the file's bytes.
    @throws OSError: when the file is unreadable.
    """
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def addToManifest(folder: str, name: str) -> None:
    """Record a derived file's sha256 in the folder's MANIFEST.json.

    @param folder: outcome folder.
    @param name: file name inside the folder.
    @returns: None.
    @throws OSError: when MANIFEST.json is absent or unwritable.
    """
    path = os.path.join(folder, 'MANIFEST.json')
    man = json.load(open(path, encoding='utf-8'))
    man['files'][name] = sha256(os.path.join(folder, name))
    man['files'] = dict(sorted(man['files'].items()))
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(man, fh, indent=1, ensure_ascii=False)
        fh.write('\n')


def forecastKeys(stories: dict) -> Dict[str, set]:
    """Keys whose real rank the table needs, per region: the forecast story or the story it was bridged to.

    @param stories: parsed stories.json.
    @returns: {region: set of Arbiter keys}.
    @throws KeyError: when stories.json lacks forecasts.
    """
    out: Dict[str, set] = {R: set() for R in REGIONS}
    for f in stories['forecasts']:
        kind = forecastKind(stories, f)
        if kind == 'rename, not scored':
            continue
        for R in REGIONS:
            for r in f['regions'][R]:
                k = r.get('bridgedTo') if kind == 'bridged' else r['arbiterKey']
                if k:
                    out[R].add(k)
    return out


def forecastKind(stories: dict, f: dict) -> str:
    """How one forecast of an outcome was scored: 'by key', 'bridged' or 'rename, not scored'.

    @param stories: parsed stories.json (the 28 and 30 Sep files carry the kind only at the top).
    @param f: one entry of stories['forecasts'].
    @returns: the forecast's kind.
    @throws KeyError: when neither carries a kind.
    """
    return f.get('kind', stories['kind'])


def horizonText(f: dict) -> str:
    """Heading text for a forecast's horizon with both clocks, e.g. "D+1 (lag 2)".

    The lag comes from the entry itself or, for outcomes written before amendment 04, from the issue's forecast.json.

    @param f: one entry of stories['forecasts'].
    @returns: the horizon text.
    @throws: nothing; without a lag the old wording is returned.
    """
    lag, countedFrom = f.get('lagFromOriginDays'), f.get('horizonCountedFrom')
    if lag is None or countedFrom is None:
        try:
            fc = json.load(open(os.path.join(LEDGER, 'issues', f['issueDay'], 'forecast.json'), encoding='utf-8'))
            h = next(x for x in fc['horizons'] if x['horizonDays'] == f['horizonDays'])
            lag, countedFrom = h['lagFromOriginDays'], h['horizonCountedFrom']
        except (OSError, KeyError, StopIteration, ValueError):
            return f"{f['horizonDays']} day{'s' if f['horizonDays'] != 1 else ''} ahead"
    return EI.horizonLabel(f['horizonDays'], lag, countedFrom)


def modelName(score: dict, f: dict, model: str) -> str:
    """Plain name of the forecast's model from score.json, for either score layout.

    @param score: parsed score.json (models at the top for 28 and 30 Sep, byIssue for later days).
    @param f: one entry of stories['forecasts'].
    @param model: model id.
    @returns: the plain name, or the id when score.json has none.
    @throws: nothing.
    """
    if f.get('modelName'):
        return f['modelName']
    if model in (score.get('models') or {}):
        return score['models'][model].get('name', model)
    for p in score.get('byIssue') or []:
        if p.get('issueDay') == f['issueDay'] and p.get('horizonDays') == f['horizonDays']:
            return ((p.get('models') or {}).get(model) or {}).get('name', model)
    return model


def writeRanks(day: str, research: str) -> int:
    """Compute real ranks from the research live tables and write ranks.json.

    @param day: target day.
    @param research: research repo root.
    @returns: 0 on success, 2 when a computed top-10 rank disagrees with stories.json.
    @throws OSError: when a research table is absent.
    """
    sys.path.insert(0, os.path.join(LEDGER, 'tools'))
    import pandas as pd  # noqa: F401  (export_outcome needs it)
    import export_outcome as EO
    folder = os.path.join(LEDGER, 'outcomes', day)
    stories = json.load(open(os.path.join(folder, 'stories.json'), encoding='utf-8'))
    actual = EO.actualCounts(research, day)
    meta = pd.read_parquet(os.path.join(research, LIVE_DATA, 'nk_meta.parquet')).drop_duplicates('narrativeKey').set_index('narrativeKey')
    need = forecastKeys(stories)
    out = {'day': day, 'rankRule': 'rank among the region\'s non-hub stories with at least one post that day, by post count; tied stories share the better rank',
           'regions': {}}
    for R in REGIONS:
        pool = {k: int(actual[k]) for k in actual.index
                if k in meta.index and meta.at[k, 'region'] == R and not bool(meta.at[k, 'hub']) and int(actual[k]) > 0}
        counts = sorted(pool.values(), reverse=True)

        def rankOf(n: int) -> Optional[int]:
            return None if n <= 0 else 1 + sum(1 for c in counts if c > n)

        rows = {}
        for k in sorted(need[R]):
            n = int(actual.get(k, 0))
            rows[k] = {'posts': n, 'rank': rankOf(n)}
        for t in stories['actualTop10'][R]:
            r = rankOf(int(t['posts']))
            if r is not None and r > t['rank']:
                print(f'refused: {R} {t["arbiterKey"]} listed at rank {t["rank"]} but ranks {r} by count', file=sys.stderr)
                return 2
        out['regions'][R] = {'storiesWithPosts': len(pool), 'stories': rows}
    with open(os.path.join(folder, 'ranks.json'), 'w', encoding='utf-8') as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)
        fh.write('\n')
    addToManifest(folder, 'ranks.json')
    return 0


def fmtCount(x: Optional[float]) -> str:
    """Format a post count for a table cell.

    @param x: count, possibly fractional (a forecast) or None.
    @returns: the count with thousands separators, one decimal under 10.
    @throws: nothing.
    """
    if x is None:
        return 'not scored'
    if abs(x - round(x)) < 1e-9 or x >= 10:
        return f'{round(x):,}'
    return f'{x:.1f}'


def cell(text: str) -> str:
    """Escape a value for a Markdown table cell.

    @param text: raw text.
    @returns: text with pipes escaped and line breaks removed.
    @throws: nothing.
    """
    return ' '.join(str(text).split()).replace('|', '\\|')


def render(day: str) -> str:
    """Build TABLE.md for one outcome folder.

    @param day: target day.
    @returns: the Markdown text.
    @throws OSError: when stories.json or score.json is absent.
    """
    folder = os.path.join(LEDGER, 'outcomes', day)
    stories = json.load(open(os.path.join(folder, 'stories.json'), encoding='utf-8'))
    score = json.load(open(os.path.join(folder, 'score.json'), encoding='utf-8'))
    rpath = os.path.join(folder, 'ranks.json')
    ranks = json.load(open(rpath, encoding='utf-8')) if os.path.exists(rpath) else None
    bridgedTop = stories.get('actualTop10Source', 'computed' if stories['kind'] == 'bridged' else 'score file') == 'computed'
    anyBridged = any(forecastKind(stories, f) != 'by key' for f in stories['forecasts'])
    top10Rank = {R: {t['arbiterKey']: t['rank'] for t in stories['actualTop10'][R]} for R in REGIONS}

    def realRank(R: str, key: Optional[str]) -> str:
        if key is None:
            return 'not scored'
        if ranks is not None:
            r = ranks['regions'][R]['stories'].get(key)
            if r is not None:
                return 'no posts' if r['rank'] is None else f"{r['rank']:,} of {ranks['regions'][R]['storiesWithPosts']:,}"
        return str(top10Rank[R][key]) if key in top10Rank[R] else 'outside the top 10'

    lines = [f'# Predicted against real, {day}', '']
    if anyBridged and stories.get('bridgeNote'):
        lines += [stories['bridgeNote'], '']
    elif anyBridged:
        lines += [f"Arbiter renamed every story on {day}, so no forecast story kept its key. We followed each forecast story to the "
                  f"story of that day holding at least half of its posts from the reading the forecast started from, a rule we fixed before "
                  f"computing any score. A row with no such match is not scored. The share of the forecast that could not be matched "
                  f"sits on every row, because the matched rows are the stories that kept going and are not a random slice of the day.", '']
    else:
        lines += ['Each row is one story from the forecast\'s top 10, with the forecast number of posts, the 80 percent range, '
                  'the number of posts Arbiter\'s own reading of the day assigned to that story, and where that put the story among '
                  'all the stories in its country that day.', '']
    lines += ['Built by `tools/outcome_table.py` from `stories.json`, `score.json` and `ranks.json` in this folder. '
              'A real rank of "9 of 96" means the story was the ninth busiest of the 96 stories in that country with at least one post that day.', '']
    for f in stories['forecasts']:
        model = f['model'].split('|')[0]
        mname = modelName(score, f, model)
        kind = forecastKind(stories, f)
        lines += [f"## Issue {f['issueDay']}, {horizonText(f)}, {mname}", '']
        if kind == 'rename, not scored':
            lines += ['Arbiter renamed its stories after the reading this forecast started from, so no row can be scored by key. '
                      'The rows are scored once the bridged score exists.', '']
        for R in REGIONS:
            lines += [f'### {REGION_NAMES[R]}', '']
            if kind == 'bridged':
                if 'unmatchedShareOfPredicted' in f:
                    un = f['unmatchedShareOfPredicted'].get(R)
                else:
                    un = score['models'][model][f'logError{R}'].get('unmatchedShareOfPredicted')
                unTxt = 'n/a' if un is None else f'{100 * un:.1f} percent of predicted posts unmatched'
                lines += ['| our rank | story | forecast | 80 percent range | real posts (bridged) | real rank (bridged) | in range | bridge | unmatched share, this region\'s forecast |',
                          '|---|---|---|---|---|---|---|---|---|']
                for r in f['regions'][R]:
                    if r.get('bridged'):
                        a = r['actualBridged']
                        inR = 'yes' if r['lo'] - 1e-9 <= a <= r['hi'] + 1e-9 else 'no'
                        if r.get('storiesSharingTarget', 1) > 1:
                            # The real count belongs to the merged story, so one source's range is the wrong yardstick.
                            inR = 'not judged, merged'
                        share = f"matched, {100 * r['bridgeShare']:.0f} percent of its posts"
                        if r.get('storiesSharingTarget', 1) > 1:
                            share += f", shared with {r['storiesSharingTarget'] - 1} other forecast stor{'y' if r['storiesSharingTarget'] == 2 else 'ies'}"
                        lines.append(f"| {r['rank']} | {cell(r['title'])} | {fmtCount(r['posts'])} | {fmtCount(r['lo'])} to {fmtCount(r['hi'])} | "
                                     f"{fmtCount(a)} | {realRank(R, r['bridgedTo'])} | {inR} | {share} | {unTxt} |")
                    else:
                        lines.append(f"| {r['rank']} | {cell(r['title'])} | {fmtCount(r['posts'])} | {fmtCount(r['lo'])} to {fmtCount(r['hi'])} | "
                                     f"not scored | not scored | not scored | unmatched | {unTxt} |")
            elif kind == 'rename, not scored':
                lines += ['| our rank | story | forecast | 80 percent range | real posts | real rank | in range |',
                          '|---|---|---|---|---|---|---|']
                for r in f['regions'][R]:
                    lines.append(f"| {r['rank']} | {cell(r['title'])} | {fmtCount(r['posts'])} | {fmtCount(r['lo'])} to {fmtCount(r['hi'])} | "
                                 f"not scored | not scored | not scored |")
            else:
                lines += ['| our rank | story | forecast | 80 percent range | real posts | real rank | in range |',
                          '|---|---|---|---|---|---|---|']
                for r in f['regions'][R]:
                    inR = 'yes' if r.get('inRange') else 'no'
                    lines.append(f"| {r['rank']} | {cell(r['title'])} | {fmtCount(r['posts'])} | {fmtCount(r['lo'])} to {fmtCount(r['hi'])} | "
                                 f"{fmtCount(r['actual'])} | {realRank(R, r['arbiterKey'])} | {inR} |")
            lines.append('')
    for R in REGIONS:
        listed = set()
        for f in stories['forecasts']:
            kind = forecastKind(stories, f)
            if kind == 'rename, not scored':
                continue
            for r in f['regions'][R]:
                listed.add(r.get('bridgedTo') if kind == 'bridged' else r['arbiterKey'])
        lines += [f'## The real top 10, {REGION_NAMES[R]}', '']
        if bridgedTop:
            lines += ['| real rank | story | real posts | a forecast story was bridged to it | in our top 10 |', '|---|---|---|---|---|']
            for t in stories['actualTop10'][R]:
                lines.append(f"| {t['rank']} | {cell(t['title'])} | {fmtCount(t['posts'])} | {'yes' if t.get('bridgedFromForecast') else 'no'} | "
                             f"{'yes' if t['arbiterKey'] in listed else 'no'} |")
        else:
            lines += ['| real rank | story | real posts | known the day before | in our top 10 |', '|---|---|---|---|---|']
            for t in stories['actualTop10'][R]:
                lines.append(f"| {t['rank']} | {cell(t['title'])} | {fmtCount(t['posts'])} | {'yes' if t.get('liveAtOrigin') else 'no, a new story'} | "
                             f"{'yes' if t['arbiterKey'] in listed else 'no'} |")
        lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def main(argv: Optional[List[str]] = None) -> int:
    """Command-line entry.

    @param argv: arguments, or None for sys.argv.
    @returns: exit code.
    @throws OSError: when a file cannot be read or written.
    """
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('day')
    ap.add_argument('--research', help='research repo root; when given, ranks.json is recomputed first')
    a = ap.parse_args(argv)
    folder = os.path.join(LEDGER, 'outcomes', a.day)
    if not os.path.isdir(folder):
        print(f'refused: no outcome folder for {a.day}', file=sys.stderr)
        return 2
    if a.research:
        rc = writeRanks(a.day, a.research)
        if rc:
            return rc
    with open(os.path.join(folder, 'TABLE.md'), 'w', encoding='utf-8') as fh:
        fh.write(render(a.day))
    addToManifest(folder, 'TABLE.md')
    print(f'wrote outcomes/{a.day}/TABLE.md')
    return 0


if __name__ == '__main__':
    sys.exit(main())
