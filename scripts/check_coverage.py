#!/usr/bin/env python3
"""How well do Japanese and Chinese Wiktionary cover everyday English?

Downloads the English-language extracts of ja.wiktionary and zh.wiktionary
from kaikki.org, plus an English subtitle frequency list, and reports, for
the most common 1k / 5k / 10k / 20k English words:

  - how many have a usable Japanese / Chinese definition (as a headword,
    or as an inflected form of one: "went" -> go)
  - how many of those have IPA, audio, and example sentences
  - average number of senses
  - broken glosses (unexpanded templates, empty text)
  - for Chinese: how many glosses are written in Simplified vs Traditional
    (only if the `opencc` package is installed; optional)

Standard library only. Usage:

    python3 check_coverage.py                 # download into ./cache, write report.md
    python3 check_coverage.py --ja-url URL    # override a download URL
    python3 check_coverage.py --offline       # use files already in ./cache
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

UA = {'User-Agent': 'english-dictionary-coverage-check/1.0'}
FREQ_URL = 'https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/en/en_50k.txt'

# kaikki.org puts each edition's per-language extract under /<edition>/<language name>/.
# The language name is written in the edition's own language, and zh.wiktionary
# has been extracted under both 英語 and 英语, so try each and fall back to
# scraping the index page for a .jsonl link.
EDITIONS = {
    'ja': {'edition': 'jawiktionary', 'langs': ['英語']},
    'zh': {'edition': 'zhwiktionary', 'langs': ['英語', '英语']},
}

BANDS = [1000, 5000, 10000, 20000]
BROKEN = re.compile(r'Template:|\{\{|\}\}|^\W*$')


def fetch(url: str, dest: Path) -> None:
    print(f'  downloading {url}', file=sys.stderr)
    req = urllib.request.Request(url, headers=UA)
    tmp = dest.with_suffix(dest.suffix + '.part')
    with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, 'wb') as out:
        while chunk := resp.read(1 << 20):
            out.write(chunk)
    tmp.rename(dest)


def find_jsonl_url(edition: str, lang: str) -> str | None:
    base = f'https://kaikki.org/{edition}/{urllib.parse.quote(lang)}/'
    for name in (f'kaikki.org-dictionary-{lang}.jsonl', f'kaikki.org-dictionary-{lang}.jsonl.gz'):
        url = base + urllib.parse.quote(name)
        try:
            urllib.request.urlopen(urllib.request.Request(url, method='HEAD', headers=UA), timeout=30)
            return url
        except Exception:
            pass
    try:
        html = urllib.request.urlopen(urllib.request.Request(base + 'index.html', headers=UA), timeout=60).read().decode('utf-8', 'replace')
    except Exception:
        return None
    m = re.search(r'href="([^"]+\.jsonl(?:\.gz)?)"', html)
    return urllib.parse.urljoin(base, m.group(1)) if m else None


def open_jsonl(path: Path):
    if path.suffix == '.gz':
        return gzip.open(path, 'rt', encoding='utf-8')
    return open(path, encoding='utf-8')


def usable_gloss(sense: dict) -> str | None:
    """The sense's own gloss, if it is a real definition (not a bare form-of)."""
    glosses = sense.get('glosses') or []
    if not glosses:
        return None
    g = glosses[-1].strip()
    if BROKEN.search(g):
        return None
    return g


def load_edition(path: Path, english_only: bool = True):
    heads: dict[str, dict] = {}          # lowercased headword -> stats
    forms: dict[str, set[str]] = defaultdict(set)  # lowercased form -> headwords
    broken = 0
    total_senses = 0
    glosses: list[str] = []
    with open_jsonl(path) as f:
        for line in f:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if english_only and e.get('lang_code') not in (None, 'en'):
                continue
            word = (e.get('word') or '').strip()
            if not word:
                continue
            key = word.lower()
            h = heads.setdefault(key, {'senses': 0, 'ipa': False, 'audio': False, 'example': False, 'pos': set()})
            h['pos'].add(e.get('pos'))
            for s in e.get('senses') or []:
                total_senses += 1
                g = usable_gloss(s)
                if g is None:
                    if s.get('glosses'):
                        broken += 1
                    continue
                h['senses'] += 1
                glosses.append(g)
                if s.get('examples'):
                    h['example'] = True
            for snd in e.get('sounds') or []:
                if snd.get('ipa'):
                    h['ipa'] = True
                if snd.get('audio') or snd.get('mp3_url') or snd.get('ogg_url'):
                    h['audio'] = True
            for fm in e.get('forms') or []:
                form = (fm.get('form') or '').strip().lower()
                if form and ' ' not in form and form != key:
                    forms[form].add(key)
    return heads, forms, broken, total_senses, glosses


def script_mix(glosses: list[str]) -> str:
    try:
        import opencc  # type: ignore
    except ImportError:
        return 'Simplified/Traditional split: skipped (pip install opencc to measure)\n'
    to_s = opencc.OpenCC('t2s')
    to_t = opencc.OpenCC('s2t')
    c = Counter()
    for g in glosses:
        s, t = to_s.convert(g), to_t.convert(g)
        if s == t:
            c['same in both'] += 1
        elif g == s:
            c['Simplified'] += 1
        elif g == t:
            c['Traditional'] += 1
        else:
            c['mixed'] += 1
    n = sum(c.values()) or 1
    return 'Gloss script: ' + ', '.join(f'{k} {v / n:.0%}' for k, v in c.most_common()) + '\n'


def report(name: str, heads, forms, broken, total_senses, glosses, freq: list[str]) -> str:
    out = [f'## {name}\n']
    with_def = {k for k, h in heads.items() if h['senses'] > 0}
    out.append(f'English headwords: {len(heads):,} ({len(with_def):,} with a usable definition). '
               f'Broken glosses: {broken:,} of {total_senses:,} senses ({broken / max(total_senses, 1):.1%}).\n')
    out.append('| Top N words | Covered | as headword | only as a form | IPA | audio | example | avg senses |')
    out.append('|---|---|---|---|---|---|---|---|')
    missing_top = []
    for n in BANDS:
        band = freq[:n]
        direct = [w for w in band if w in with_def]
        via_form = [w for w in band if w not in with_def and any(l in with_def for l in forms.get(w, ()))]
        covered = len(direct) + len(via_form)
        hs = [heads[w] for w in direct]
        pct = lambda k: f'{sum(h[k] for h in hs) / max(len(hs), 1):.0%}'
        avg = sum(h['senses'] for h in hs) / max(len(hs), 1)
        out.append(f'| {n:,} | {covered / n:.1%} | {len(direct):,} | {len(via_form):,} | '
                   f'{pct("ipa")} | {pct("audio")} | {pct("example")} | {avg:.1f} |')
        if n == BANDS[0]:
            missing_top = [w for w in band if w not in direct and w not in via_form]
    out.append('')
    out.append(f'Missing from the top {BANDS[0]:,} ({len(missing_top)}): ' + ', '.join(missing_top[:150]) + '\n')
    if name.startswith('Chinese'):
        out.append(script_mix(glosses))
    return '\n'.join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--cache', default='cache')
    ap.add_argument('--ja-url')
    ap.add_argument('--zh-url')
    ap.add_argument('--offline', action='store_true')
    ap.add_argument('--out', default='report.md')
    args = ap.parse_args()
    cache = Path(args.cache)
    cache.mkdir(exist_ok=True)

    freq_path = cache / 'en_50k.txt'
    if not freq_path.exists() and not args.offline:
        fetch(FREQ_URL, freq_path)
    freq = []
    for line in freq_path.read_text(encoding='utf-8').splitlines():
        w = line.split(' ')[0].strip().lower()
        if w and w.isascii() and re.fullmatch(r"[a-z][a-z'\-]*", w):
            freq.append(w)

    sections = ['# English coverage in Japanese and Chinese Wiktionary\n',
                f'Frequency list: FrequencyWords 2018 English subtitles ({len(freq):,} words after filtering). '
                'It counts word forms, so "went" and "dogs" appear on their own; those are counted as '
                'covered when they are listed as a form of a defined headword.\n']
    for code, label in (('ja', 'Japanese (ja.wiktionary)'), ('zh', 'Chinese (zh.wiktionary)')):
        override = getattr(args, f'{code}_url')
        existing = sorted(cache.glob(f'{code}-english.jsonl*'))
        if existing:
            path = existing[0]
        elif args.offline:
            sections.append(f'## {label}\n\nNo cached file.\n')
            continue
        else:
            url = override
            if not url:
                cfg = EDITIONS[code]
                for lang in cfg['langs']:
                    url = find_jsonl_url(cfg['edition'], lang)
                    if url:
                        break
            if not url:
                sections.append(f'## {label}\n\nCould not find the download; pass --{code}-url.\n')
                continue
            path = cache / (f'{code}-english.jsonl' + ('.gz' if url.endswith('.gz') else ''))
            fetch(url, path)
        print(f'  reading {path}', file=sys.stderr)
        sections.append(report(label, *load_edition(path), freq))

    text = '\n'.join(sections)
    Path(args.out).write_text(text, encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
