"""Export one frozen forecast issue from the research repo into issues/<issueDay>/.

The frozen files are copied byte for byte, so the sha256 values in FREEZE.json and the
OpenTimestamps proof in FREEZE.json.ots still verify. forecast.json is a readable summary of the
headline model (top 10 stories per region per horizon). MANIFEST.json holds the sha256 of every
file in the folder and the research commit the freeze came from.

Usage: python3 tools/export_issue.py <issueDay> [--research <path>] [--force]
Exit codes: 0 written, 2 refused (source missing, uncommitted, already exported, or a hash differs).
Standard library only, Python 3.9 or later.
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
from typing import Dict, List, Optional

LEDGER = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DEFAULT_RESEARCH = os.path.expanduser('~/Documents/simppl/papers/narrative-reach-sim')
FORWARD = 'cleanroom/2026-10-01-tomorrow-forward/forecasts'
TOURNAMENT = 'cleanroom/2026-09-28-tomorrow-tournament/forecasts'
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
TOP_N = 10
# Left by `ots upgrade` and macOS; git ignores them too.
IGNORED = ('.ots.bak', '.DS_Store')
# The unit layer is committed after MANIFEST.json and indexed by UNITS.json, so the manifest leaves it out.
UNIT_DIRS = ('categories', 'narratives', 'posts', 'accounts')
UNIT_INDEXES = ('UNITS.json', 'UNITS.json.ots', 'OUTCOMES-UNITS.json', 'OUTCOMES-UNITS.json.ots')

# Issues frozen before the forward block began. Their folders are named by target day in the
# research repo, so the ledger maps them by the UTC date of the freeze.
LEGACY: Dict[str, dict] = {
    '2026-09-27': {'folder': f'{TOURNAMENT}/pilot-2026-09-28', 'model': 'gbm|K0',
                   'note': ('Pilot, two days ahead of the reading it started from, issued before the horizon rule of 3 October 2026 '
                            '(forecasts hourly, for the next day, and at most two days ahead). '
                            'Published unchanged. This copy reached the ledger on 2 October and its OpenTimestamps proof was '
                            'made on 29 September, both after the target day, so the stamp and push checks in tools/verify.py '
                            'fail for it. The only record that it was made before 28 September is the push to our private '
                            'research repo at 21:19:29 UTC on 27 September, which an outsider cannot check.')},
    '2026-09-29': {'folder': f'{TOURNAMENT}/live-2026-09-30', 'model': 'gbm|K0',
                   'note': ('Two days ahead of the reading it started from, issued before the horizon rule of 3 October 2026 '
                            '(forecasts hourly, for the next day, and at most two days ahead). '
                            'Published unchanged. This copy reached the ledger on 2 October, after the target day, so the push '
                            'check in tools/verify.py fails for it. The OpenTimestamps proof, anchored in Bitcoin on 29 September, '
                            'is the public clock that shows it was made before 30 September.')},
}
FORWARD_MODEL = 'gbm_med'


def sha256(path: str) -> str:
    """Hash a file.

    @param path: file path.
    @returns: hex sha256 of the file's bytes.
    @throws OSError: when the file cannot be read.
    """
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def git(research: str, *args: str) -> subprocess.CompletedProcess:
    """Run git in the research repo.

    @param research: research repo root.
    @param args: git arguments.
    @returns: the completed process (text output).
    @throws OSError: when git is not installed.
    """
    return subprocess.run(['git', '-C', research, *args], capture_output=True, text=True)


def sourceFor(issueDay: str, research: str) -> dict:
    """Locate the research folder and headline model for an issue day.

    @param issueDay: UTC date of the freeze, YYYY-MM-DD.
    @param research: research repo root.
    @returns: {'folder', 'model', 'note'} with folder relative to the research root.
    @throws FileNotFoundError: when no frozen folder exists for the day.
    """
    if issueDay in LEGACY:
        src = dict(LEGACY[issueDay])
    else:
        src = {'folder': f'{FORWARD}/issue-{issueDay}', 'model': FORWARD_MODEL, 'note': None}
    if not os.path.exists(os.path.join(research, src['folder'], 'FREEZE.json')):
        raise FileNotFoundError(f'no FREEZE.json in {src["folder"]}')
    return src


def frozenUtc(freeze: dict) -> dt.datetime:
    """Freeze time in UTC from FREEZE.json.

    @param freeze: parsed FREEZE.json.
    @returns: aware UTC datetime.
    @throws KeyError: when frozenIst is absent.
    """
    return dt.datetime.fromisoformat(freeze['frozenIst']).astimezone(dt.timezone.utc)


def targetsOf(freeze: dict) -> List[dict]:
    """Target days and horizons in days counted from the issue day.

    @param freeze: parsed FREEZE.json.
    @returns: list of {'day', 'h'} sorted by day.
    @throws KeyError: when targets are absent.
    """
    return sorted(({'day': t['day'], 'h': int(t['h'])} for t in freeze['targets']), key=lambda t: t['day'])


def regionsOf(doc: dict, model: str) -> Dict[str, dict]:
    """Per-region block of the headline model, in either research format.

    @param doc: parsed forecast_<day>.json.
    @param model: model name as stored in the file.
    @returns: {region: {'liveStories', 'top'}}.
    @throws KeyError: when the model is absent.
    """
    m = doc['models'][model]
    regions = m['regions'] if 'regions' in m else m
    out = {}
    for region in sorted(regions):
        r = regions[region]
        out[region] = {'liveStories': r.get('liveKeys', r.get('liveNarratives')), 'top': r['top'][:TOP_N]}
    return out


def storyRow(row: dict) -> dict:
    """Public row for one forecast story.

    @param row: one entry of a research 'top' list.
    @returns: rank, title, Arbiter key, forecast posts and the 80 percent range.
    @throws KeyError: when a field is absent.
    """
    return {'rank': int(row['rank']), 'title': row['title'], 'arbiterKey': row['narrativeKey'],
            'posts': row['posts'], 'lo': row['lo80'], 'hi': row['hi80'], 'chance': row.get('word')}


def buildForecast(issueDay: str, freeze: dict, src: dict, folder: str, commit: str) -> dict:
    """Readable summary of the frozen forecast.

    @param issueDay: ledger issue day.
    @param freeze: parsed FREEZE.json.
    @param src: source description from sourceFor.
    @param folder: absolute research folder.
    @param commit: research commit that added FREEZE.json.
    @returns: the forecast.json object.
    @throws FileNotFoundError: when a forecast file for a target is absent.
    """
    fz = frozenUtc(freeze)
    horizons = []
    for t in targetsOf(freeze):
        doc = json.load(open(os.path.join(folder, f'forecast_{t["day"]}.json'), encoding='utf-8'))
        origin = freeze.get('origin') or freeze['targets'][0].get('origin')
        lag = (dt.date.fromisoformat(t['day']) - dt.date.fromisoformat(origin)).days
        horizons.append({'horizonDays': t['h'], 'horizonCountedFrom': 'origin reading' if src['note'] else 'issue day',
                         'lagFromOriginDays': lag, 'targetDay': t['day'], 'targetStartsUtc': f'{t["day"]}T00:00:00Z',
                         'frozenBeforeTargetStart': fz < dt.datetime.fromisoformat(f'{t["day"]}T00:00:00+00:00'),
                         'regions': regionsOf(doc, src['model'])})
        for r in horizons[-1]['regions'].values():
            r['top'] = [storyRow(x) for x in r['top']]
    return {
        'issueDay': issueDay,
        'frozenUtc': fz.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'frozenIst': freeze['frozenIst'],
        'origin': freeze.get('origin') or freeze['targets'][0].get('origin'),
        'model': src['model'],
        'unit': 'An Arbiter story (narrative key) on X, regions US and IN. "posts" is the forecast count of posts '
                'published on the target day (UTC) that Arbiter will assign to the story; lo and hi bound the 80 '
                'percent range.',
        'note': src['note'],
        'source': {'repo': 'narrative-reach-sim (private research repo)', 'folder': src['folder'], 'freezeCommit': commit},
        'horizons': horizons,
    }


def horizonLabel(horizonDays: int, lag: int, countedFrom: str) -> str:
    """Name a horizon with both clocks: days from the issue day and readings from the origin.

    @param horizonDays: horizon as stored in forecast.json.
    @param lag: days from the origin reading to the target (lagFromOriginDays).
    @param countedFrom: 'issue day' or 'origin reading' (horizonCountedFrom).
    @returns: e.g. "D+2 (lag 3)", or "2 days ahead (lag 2)" for an issue counted from its origin reading.
    @throws: nothing.
    """
    if countedFrom == 'issue day':
        return f'D+{horizonDays} (lag {lag})'
    return f'{horizonDays} day{"" if horizonDays == 1 else "s"} ahead (lag {lag})'


def issueReadme(issueDay: str, forecast: dict, unpublished: List[str]) -> str:
    """Plain-prose README for the issue folder.

    @param issueDay: ledger issue day.
    @param forecast: the forecast.json object.
    @param unpublished: frozen files listed in FREEZE.json but not published.
    @returns: markdown text.
    @throws: nothing.
    """
    lines = [f'# Issue {issueDay}', '',
             f'Frozen {forecast["frozenUtc"]} ({forecast["frozenIst"]} in India) from Arbiter\'s reading of '
             f'{forecast["origin"]}, with the model `{forecast["model"]}`.', '']
    for h in forecast['horizons']:
        when = 'before' if h['frozenBeforeTargetStart'] else 'after'
        label = horizonLabel(h['horizonDays'], h['lagFromOriginDays'], h['horizonCountedFrom'])
        if h['horizonCountedFrom'] != 'issue day':
            label = label.replace(' ahead', ' ahead of the origin reading', 1)
        line = f'- {label}: target {h["targetDay"]}, frozen {when} that day began (UTC).'
        if h['horizonCountedFrom'] == 'issue day' and h['horizonDays'] >= 3:
            line += ' This three-day target was frozen before the horizon rule of 3 October 2026 and is scored once.'
        lines.append(line)
    lines += ['', 'D+n is the number of days from the issue day to the target day. The lag is the number of days from the '
              'reading the forecast starts from to the target day, so D+1 (lag 2) is the second day after that reading.'
              if any(h['horizonCountedFrom'] == 'issue day' for h in forecast['horizons']) else
              'The lag is the number of days from the reading the forecast starts from to the target day.']
    lines += ['', '`forecast.json` lists the top 10 stories per region for each target. `FREEZE.json` and '
              '`FREEZE.json.ots` are copied byte for byte from the research repo, with every forecast file they hash. '
              '`MANIFEST.json` holds the sha256 of every file here.', '']
    if forecast['note']:
        lines += [forecast['note'], '']
    if unpublished:
        lines += ['FREEZE.json also hashes these model inputs, which stay private because they are internal Arbiter '
                  'tables: ' + ', '.join(f'`{u}`' for u in unpublished) + '. The forecast files themselves are all here.', '']
    if issueDay < '2026-10-03':
        lines += ['This issue carries no predicted posts. Predicted posts begin with issue 2026-10-03.', '']
    return '\n'.join(lines)


def export(issueDay: str, research: str, force: bool = False) -> int:
    """Write issues/<issueDay>/ from the research repo.

    @param issueDay: UTC date of the freeze.
    @param research: research repo root.
    @param force: overwrite an existing ledger folder.
    @returns: exit code (0 written, 2 refused).
    @throws OSError: on a file system failure.
    """
    try:
        src = sourceFor(issueDay, research)
    except FileNotFoundError as e:
        print(f'refused: {e}')
        return 2
    folder = os.path.join(research, src['folder'])
    freeze = json.load(open(os.path.join(folder, 'FREEZE.json'), encoding='utf-8'))
    if frozenUtc(freeze).date().isoformat() != issueDay:
        print(f'refused: freeze is dated {frozenUtc(freeze).date()} UTC, not {issueDay}')
        return 2
    if git(research, 'status', '--porcelain', '--untracked-files=no', '--', src['folder']).stdout.strip():
        print(f'refused: {src["folder"]} has uncommitted changes in the research repo')
        return 2
    commit = git(research, 'log', '-1', '--diff-filter=A', '--format=%H', '--', f'{src["folder"]}/FREEZE.json').stdout.strip()
    if not commit:
        print('refused: FREEZE.json is not committed in the research repo')
        return 2
    published, unpublished = [], []
    for name, digest in freeze['files'].items():
        if name.startswith('input:') or name.startswith('inputs/'):
            unpublished.append(name.split(':', 1)[-1])
            continue
        if sha256(os.path.join(folder, name)) != digest:
            print(f'refused: {name} no longer matches FREEZE.json')
            return 2
        published.append(name)
    for name in ['FREEZE.json', 'FREEZE.json.ots', *published]:
        rel = f'{src["folder"]}/{name}'
        if os.path.exists(os.path.join(research, rel)) and git(research, 'ls-files', '--error-unmatch', rel).returncode != 0:
            print(f'refused: {rel} is not committed in the research repo')
            return 2
    out = os.path.join(LEDGER, 'issues', issueDay)
    if os.path.exists(out) and not force:
        print(f'refused: {out} exists (use --force to rewrite it)')
        return 2
    os.makedirs(out, exist_ok=True)
    # A proof already in the ledger for the same FREEZE.json may have been upgraded here; keep it.
    keepProof = (os.path.exists(os.path.join(out, 'FREEZE.json.ots')) and os.path.exists(os.path.join(out, 'FREEZE.json'))
                 and sha256(os.path.join(out, 'FREEZE.json')) == sha256(os.path.join(folder, 'FREEZE.json')))
    for name in ['FREEZE.json', 'FREEZE.json.ots', *published]:
        if name == 'FREEZE.json.ots' and keepProof:
            continue
        if os.path.exists(os.path.join(folder, name)):
            shutil.copyfile(os.path.join(folder, name), os.path.join(out, name))
    forecast = buildForecast(issueDay, freeze, src, folder, commit)
    with open(os.path.join(out, 'forecast.json'), 'w', encoding='utf-8') as fh:
        json.dump(forecast, fh, indent=1, ensure_ascii=False)
        fh.write('\n')
    with open(os.path.join(out, 'README.md'), 'w', encoding='utf-8') as fh:
        fh.write(issueReadme(issueDay, forecast, unpublished))
    writeManifest(out, {'kind': 'issue', 'issueDay': issueDay, 'researchFolder': src['folder'],
                        'researchFreezeCommit': commit,
                        'researchHead': git(research, 'rev-parse', 'HEAD').stdout.strip(),
                        'freezeSha256': sha256(os.path.join(out, 'FREEZE.json')),
                        'frozenFilesNotPublished': {u: freeze['files'].get(u, freeze['files'].get(f'input:{u}')) for u in unpublished}})
    print(f'wrote {os.path.relpath(out, LEDGER)}: {len(published)} frozen files, FREEZE.json, '
          f'{"FREEZE.json.ots" if os.path.exists(os.path.join(out, "FREEZE.json.ots")) else "no stamp yet"}')
    return 0


def writeManifest(out: str, meta: dict) -> None:
    """Write MANIFEST.json with the sha256 of every other file in the folder.

    @param out: folder path.
    @param meta: fields recorded beside the hashes.
    @returns: None.
    @throws OSError: on a file system failure.
    """
    files = {}
    for root, _, names in os.walk(out):
        for n in sorted(names):
            p = os.path.join(root, n)
            rel = os.path.relpath(p, out)
            if rel != 'MANIFEST.json' and not rel.endswith(IGNORED) and rel not in UNIT_INDEXES and rel.split(os.sep)[0] not in UNIT_DIRS:
                files[rel] = sha256(p)
    meta = dict(meta)
    meta.setdefault('exportedUtc', dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
    meta['files'] = dict(sorted(files.items()))
    with open(os.path.join(out, 'MANIFEST.json'), 'w', encoding='utf-8') as fh:
        json.dump(meta, fh, indent=1, ensure_ascii=False)
        fh.write('\n')


def refreshManifest(folder: str) -> None:
    """Re-hash a folder into its existing MANIFEST.json, keeping the recorded fields.

    Used after `ots upgrade` completes a stamp: the proof file grows, the stamped file does not change.

    @param folder: issue, outcome or pre-registration folder.
    @returns: None.
    @throws FileNotFoundError: when the folder has no MANIFEST.json.
    """
    path = os.path.join(folder, 'MANIFEST.json')
    meta = json.load(open(path, encoding='utf-8'))
    meta.pop('files', None)
    meta['refreshedUtc'] = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    writeManifest(folder, meta)


def refreshReadme(folder: str) -> None:
    """Rewrite an exported issue's README.md from its forecast.json, without touching any frozen file.

    A legacy issue's note in forecast.json is replaced by the current LEGACY note first, so wording rules that change
    after an issue was exported (such as the horizon rule of 3 October 2026) reach the folder. MANIFEST.json is then
    re-hashed, keeping its recorded fields.

    @param folder: issues/<issueDay> folder.
    @returns: None.
    @throws FileNotFoundError: when forecast.json or MANIFEST.json is absent.
    """
    fpath = os.path.join(folder, 'forecast.json')
    forecast = json.load(open(fpath, encoding='utf-8'))
    manifest = json.load(open(os.path.join(folder, 'MANIFEST.json'), encoding='utf-8'))
    issueDay = forecast['issueDay']
    if issueDay in LEGACY and forecast.get('note') != LEGACY[issueDay]['note']:
        forecast['note'] = LEGACY[issueDay]['note']
        with open(fpath, 'w', encoding='utf-8') as fh:
            json.dump(forecast, fh, indent=1, ensure_ascii=False)
            fh.write('\n')
    unpublished = list(manifest.get('frozenFilesNotPublished', {}))
    with open(os.path.join(folder, 'README.md'), 'w', encoding='utf-8') as fh:
        fh.write(issueReadme(issueDay, forecast, unpublished))
    refreshManifest(folder)


def main(argv: Optional[List[str]] = None) -> int:
    """Command-line entry.

    @param argv: arguments (defaults to sys.argv).
    @returns: exit code.
    @throws SystemExit: on bad arguments.
    """
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('issueDay')
    ap.add_argument('--research', default=DEFAULT_RESEARCH)
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--refresh-manifest', action='store_true', help='treat the argument as a folder and only re-hash it')
    ap.add_argument('--refresh-readme', action='store_true',
                    help='treat the argument as an issue folder; rewrite README.md (and a legacy note in forecast.json), then re-hash')
    a = ap.parse_args(argv)
    if a.refresh_manifest:
        refreshManifest(os.path.abspath(a.issueDay))
        return 0
    if a.refresh_readme:
        refreshReadme(os.path.abspath(a.issueDay))
        return 0
    return export(a.issueDay, os.path.abspath(a.research), a.force)


if __name__ == '__main__':
    sys.exit(main())
