# English dictionary for Japanese and Chinese speakers

A Takoboto-style English dictionary for Japanese and Chinese (Simplified and Traditional) native speakers, built on the same approach as [Szókert](https://github.com/TaylorNunes/szokert).

## Data coverage check

`scripts/check_coverage.py` measures how well the English entries in Japanese and Chinese Wiktionary (via [kaikki.org](https://kaikki.org)) cover the most common English words. The **Coverage check** workflow runs it and commits the results to `report.md`. To run it locally (standard library only; `pip install opencc` adds the Simplified/Traditional split):

```sh
python3 scripts/check_coverage.py
```
