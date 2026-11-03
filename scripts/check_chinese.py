#!/usr/bin/env python3
"""Can other sources fill the gaps in Chinese Wiktionary's English entries?

For the most common English words, measures Chinese coverage from:

  1. zh.wiktionary English entries (Chinese definitions)       -- kaikki.org
  2. en.wiktionary translation tables (Mandarin translations)  -- kaikki.org
  3. CC-CEDICT, reversed so its English glosses become headwords
  4. a pivot through Japanese: the kanji words in ja.wiktionary's Japanese
     definitions, converted to Chinese characters and kept when CC-CEDICT
     has them as words (理解 -> 理解, 図書館 -> 图书馆)

and how often sources 3 and 4 agree with source 2, which is the closest thing
to a hand-checked answer. It also lists where the Japanese pivot lands on
known false friends (勉強 "study" is 勉强 "reluctantly" in Chinese).

Needs `pip install opencc`. Usage:

    python3 scripts/check_chinese.py --cache cache --out report-chinese.md
"""

from __future__ import annotations

import argparse
import gzip
import json
import random
import re
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

import opencc

UA = {'User-Agent': 'english-dictionary-coverage-check/1.0'}
FREQ_URL = 'https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/en/en_50k.txt'
CEDICT_URL = 'https://www.mdbg.net/chinese/export/cedict/cedict_1_0_ts_utf-8_mdbg.txt.gz'
EN_URL = 'https://kaikki.org/dictionary/English/kaikki.org-dictionary-English.jsonl'
JA_URLS = ['https://kaikki.org/jawiktionary/英語/kaikki.org-dictionary-英語.jsonl']
ZH_URLS = ['https://kaikki.org/zhwiktionary/英語/kaikki.org-dictionary-英語.jsonl',
           'https://kaikki.org/zhwiktionary/英语/kaikki.org-dictionary-英语.jsonl']
BANDS = [1000, 5000, 10000, 20000]
BROKEN = re.compile(r'Template:|\{\{|\}\}|^\W*$')
HAN = re.compile(r'^[一-鿿㐀-䶿々]+$')

# Same characters, different meaning in Japanese and Chinese. (Japanese sense / Chinese sense)
FALSE_FRIENDS = {
    '勉強': 'study / reluctantly', '手紙': 'letter / toilet paper', '大丈夫': 'all right / real man',
    '汽車': 'train / car', '愛人': 'lover / spouse', '丈夫': 'sturdy / husband', '新聞': 'newspaper / news',
    '工夫': 'ingenuity / time, kung fu', '迷惑': 'nuisance / puzzled', '結束': 'unity / to end',
    '留守': 'away from home / to stay behind', '娘': 'daughter / mother', '湯': 'hot water / soup',
    '走': 'run / walk', '先生': 'teacher / Mr.', '床': 'floor / bed', '暗算': 'mental arithmetic / plot against',
    '心中': 'double suicide / in one\'s heart', '人間': 'human / the human world', '野菜': 'vegetable / wild herbs',
    '真面目': 'serious / true features', '怪我': 'injury / blame me', '老婆': 'old woman / wife',
    '我慢': 'endurance / (not a word)', '必死': 'desperate / certain to die', '油断': 'carelessness / oil cut off',
    '邪魔': 'hindrance / demon', '検討': 'consider / self-criticism', '階段': 'stairs / stage, phase',
    '高校': 'high school / university', '大事': 'important / major event', '機嫌': 'mood / (not a word)',
}

jp2t = opencc.OpenCC('jp2t')
t2s = opencc.OpenCC('t2s')


def fetch(url: str, dest: Path) -> Path:
    if dest.exists():
        return dest
    parts = urllib.parse.urlsplit(url)
    url = urllib.parse.urlunsplit(parts._replace(path=urllib.parse.quote(parts.path)))
    print(f'  downloading {url}', file=sys.stderr, flush=True)
    tmp = dest.with_suffix(dest.suffix + '.part')
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300) as r, open(tmp, 'wb') as out:
        while chunk := r.read(1 << 20):
            out.write(chunk)
    tmp.rename(dest)
    return dest


def jsonl(path: Path):
    with (gzip.open(path, 'rt', encoding='utf-8') if path.suffix == '.gz' else open(path, encoding='utf-8')) as f:
        for line in f:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                pass


def good_glosses(e: dict) -> list[str]:
    out = []
    for s in e.get('senses') or []:
        g = (s.get('glosses') or [''])[-1].strip()
        if g and not BROKEN.search(g):
            out.append(g)
    return out


def add_forms(e: dict, key: str, forms: dict[str, set[str]]) -> None:
    for fm in e.get('forms') or []:
        f = (fm.get('form') or '').strip().lower()
        if f and ' ' not in f and f != key:
            forms[f].add(key)


# ---------- sources ----------

def load_wiktionary_glosses(paths: list[Path], forms) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for p in paths:
        for e in jsonl(p):
            if e.get('lang_code') not in (None, 'en') or not e.get('word'):
                continue
            key = e['word'].strip().lower()
            out[key] += good_glosses(e)
            add_forms(e, key, forms)
    return {k: v for k, v in out.items() if v}


def load_en_translations(path: Path, forms) -> dict[str, set[str]]:
    """English word -> Mandarin translations (normalised to Simplified)."""
    out: dict[str, set[str]] = defaultdict(set)
    for e in jsonl(path):
        word = (e.get('word') or '').strip()
        if not word:
            continue
        key = word.lower()
        add_forms(e, key, forms)
        trs = list(e.get('translations') or [])
        for s in e.get('senses') or []:
            trs += s.get('translations') or []
        for t in trs:
            code = t.get('lang_code') or t.get('code')
            lang = t.get('lang') or ''
            if code in ('cmn', 'zh') or lang in ('Mandarin', 'Chinese Mandarin') or (lang == 'Chinese' and 'Mandarin' in str(t.get('tags') or t.get('raw_tags') or '')):
                w = (t.get('word') or '').strip()
                if w and re.search(r'[一-鿿]', w):
                    out[key].add(t2s.convert(w))
    return dict(out)


def load_cedict(path: Path):
    """Returns (English gloss -> Simplified words, set of Traditional words -> Simplified)."""
    rev: dict[str, set[str]] = defaultdict(set)
    trad2simp: dict[str, str] = {}
    simp_words: set[str] = set()
    pat = re.compile(r'^(\S+) (\S+) \[[^\]]*\] /(.*)/\s*$')
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        for line in f:
            if line.startswith('#'):
                continue
            m = pat.match(line)
            if not m:
                continue
            trad, simp, glosses = m.groups()
            trad2simp.setdefault(trad, simp)
            simp_words.add(simp)
            for g in glosses.split('/'):
                if g.startswith(('CL:', 'variant of', 'old variant', 'see ', 'surname ', 'abbr.')):
                    continue
                g = re.sub(r'\([^)]*\)', '', g).strip().lower()
                g = re.sub(r'^to ', '', g).strip()
                if g and ' ' not in g:
                    rev[g].add(simp)
    return dict(rev), trad2simp, simp_words


def pivot_candidates(ja_glosses: list[str], trad2simp, simp_words) -> set[str]:
    """Kanji words in Japanese definitions that are also Chinese words."""
    out = set()
    for g in ja_glosses:
        g = re.sub(r'[（(][^）)]*[）)]', '', g)
        for term in re.split(r'[、。,，;；・「」『』\s/]+', g):
            term = re.sub(r'(する|な|の|に|さ|だ|である)$', '', term.strip())
            if not term or not HAN.match(term):
                continue
            # OpenCC's Japanese table sometimes picks a rarer variant (研 -> 硏), so try the
            # term as written too, and accept a Simplified match as well as a Traditional one.
            for t in dict.fromkeys((term, jp2t.convert(term))):
                if t in trad2simp:
                    out.add(trad2simp[t])
                    break
                if t2s.convert(t) in simp_words:
                    out.add(t2s.convert(t))
                    break
    return out


# ---------- report ----------

def resolve(word, table, forms):
    if word in table:
        return table[word]
    for lemma in forms.get(word, ()):
        if lemma in table:
            return table[lemma]
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--cache', default='cache')
    ap.add_argument('--out', default='report-chinese.md')
    ap.add_argument('--en-limit', type=int, help='read only this many English Wiktionary entries (testing)')
    args = ap.parse_args()
    cache = Path(args.cache)
    cache.mkdir(exist_ok=True)

    freq_path = fetch(FREQ_URL, cache / 'en_50k.txt')
    freq = [w for w in (l.split(' ')[0].lower() for l in freq_path.read_text(encoding='utf-8').splitlines())
            if re.fullmatch(r"[a-z][a-z'\-]*", w)]
    # Drop the stems the subtitle tokenizer leaves from contractions ("didn" from "didn't").
    freq = [w for w in freq if w not in {'don', 'didn', 'doesn', 'isn', 'wasn', 'wouldn', 'couldn', 'aren',
                                         'shouldn', 'weren', 'hasn', 'haven', 'hadn', 'ain', 'll', 've', 're', 't', 's', 'm', 'd'}]

    forms: dict[str, set[str]] = defaultdict(set)
    ja_paths = [fetch(u, cache / f'ja{i}.jsonl') for i, u in enumerate(JA_URLS)]
    zh_paths = [fetch(u, cache / f'zh{i}.jsonl') for i, u in enumerate(ZH_URLS)]
    en_path = fetch(EN_URL, cache / 'en.jsonl')
    cedict_path = fetch(CEDICT_URL, cache / 'cedict.txt.gz')

    print('  reading ja.wiktionary', file=sys.stderr, flush=True)
    ja = load_wiktionary_glosses(ja_paths, forms)
    print('  reading zh.wiktionary', file=sys.stderr, flush=True)
    zh = load_wiktionary_glosses(zh_paths, forms)
    print('  reading en.wiktionary (large)', file=sys.stderr, flush=True)
    en = load_en_translations(en_path, forms)
    print('  reading CC-CEDICT', file=sys.stderr, flush=True)
    cedict_rev, trad2simp, simp_words = load_cedict(cedict_path)
    pivot = {w: c for w, gl in ja.items() if (c := pivot_candidates(gl, trad2simp, simp_words))}

    sources = [
        ('zh.wiktionary definitions', zh),
        ('en.wiktionary Mandarin translations', en),
        ('CC-CEDICT reversed', cedict_rev),
        ('Japanese pivot', pivot),
    ]
    L = ['# Filling the Chinese gaps\n',
         f'Frequency list: FrequencyWords 2018 English subtitles, {len(freq):,} words after dropping contraction stems '
         '(*didn*, *isn*). A word counts as covered if a source has it directly or has its dictionary form '
         '(*went* → *go*).\n',
         '## Coverage of the most common English words\n',
         '| Source | ' + ' | '.join(f'top {n:,}' for n in BANDS) + ' |',
         '|---|' + '---|' * len(BANDS)]
    for name, table in sources:
        L.append(f'| {name} | ' + ' | '.join(f'{sum(1 for w in freq[:n] if resolve(w, table, forms)) / n:.1%}' for n in BANDS) + ' |')
    combos = [
        ('zh.wiktionary + en.wiktionary', [zh, en]),
        ('zh.wiktionary + en.wiktionary + CC-CEDICT', [zh, en, cedict_rev]),
        ('all four', [zh, en, cedict_rev, pivot]),
    ]
    for name, tables in combos:
        L.append(f'| **{name}** | ' + ' | '.join(
            f'{sum(1 for w in freq[:n] if any(resolve(w, t, forms) for t in tables)) / n:.1%}' for n in BANDS) + ' |')
    L.append('')

    # Agreement with en.wiktionary translations
    L.append('## Agreement with English Wiktionary\'s Mandarin translations\n')
    L.append('For words in the top 5,000 that have both a candidate from the source and an English Wiktionary '
             'translation: how often at least one candidate is among the translations. English Wiktionary '
             'lists only some valid translations, so this understates real accuracy; compare the two rows '
             'with each other rather than reading them as absolute.\n')
    L.append('| Source | words compared | agree | avg candidates per word |')
    L.append('|---|---|---|---|')
    top5 = freq[:5000]
    for name, table in (('CC-CEDICT reversed', cedict_rev), ('Japanese pivot', pivot)):
        n = agree = cands = 0
        for w in top5:
            a, b = resolve(w, table, forms), resolve(w, en, forms)
            if a and b:
                n += 1
                cands += len(a)
                agree += bool(a & b)
        L.append(f'| {name} | {n:,} | {agree / max(n, 1):.0%} | {cands / max(n, 1):.1f} |')
    L.append('')

    # What the pivot adds on its own
    gap = [w for w in top5 if not any(resolve(w, t, forms) for t in (zh, en, cedict_rev))]
    rescued = [w for w in gap if resolve(w, pivot, forms)]
    L.append(f'Top-5,000 words with nothing from the other three sources: {len(gap)}. '
             f'The Japanese pivot covers {len(rescued)} of them: ' +
             ', '.join(f'{w} ({"・".join(sorted(resolve(w, pivot, forms))[:3])})' for w in rescued[:60]) + '\n')
    L.append(f'Still uncovered in the top 5,000 ({len(gap) - len(rescued)}): ' +
             ', '.join(w for w in gap if w not in rescued)[:3000] + '\n')

    # False friends
    L.append('## False friends the pivot produces\n')
    L.append('English words in the top 20,000 whose pivot candidates include a known Japanese–Chinese false friend.\n')
    hits = defaultdict(list)
    ff_simp = {t2s.convert(jp2t.convert(k)): (k, v) for k, v in FALSE_FRIENDS.items()}
    for w in freq[:20000]:
        for c in resolve(w, pivot, forms) or ():
            if c in ff_simp:
                hits[c].append(w)
    for c, ws in sorted(hits.items(), key=lambda kv: -len(kv[1])):
        ja_form, meaning = ff_simp[c]
        L.append(f'- **{ja_form}** → {c} ({meaning}): ' + ', '.join(ws[:12]))
    L.append('')

    # Side-by-side sample
    random.seed(1)
    both = [w for w in freq[:5000] if resolve(w, pivot, forms) and resolve(w, en, forms)]
    L.append('## Sample: Japanese pivot vs English Wiktionary (30 random top-5,000 words)\n')
    L.append('| English | Japanese definitions | pivot → Chinese | en.wiktionary Mandarin |')
    L.append('|---|---|---|---|')
    for w in sorted(random.sample(both, min(30, len(both))), key=freq.index):
        jg = '; '.join((resolve(w, ja, forms) or [])[:3])[:80]
        L.append(f'| {w} | {jg} | {"・".join(sorted(resolve(w, pivot, forms))[:5])} | {"・".join(sorted(resolve(w, en, forms))[:5])} |')

    text = '\n'.join(L) + '\n'
    Path(args.out).write_text(text, encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
