# Signalpost v2 changes (score 57.081 -> 58.242)

Nothing here overwrites your original files except three you can review (see "Modified files").

## Results (official score_competition_v3.py, unchanged)
| Category | Max | Before | After |
|---|---|---|---|
| External footprint | 55 | 12.093 | 13.254 |
| Official foundation | 15 | 14.988 | 14.988 |
| Research agent | 10 | 10.0 | 10.0 |
| Extensibility/refresh | 12 | 12.0 | 12.0 |
| Product UX | 8 | 8.0 | 8.0 |
| **Total** | 100 | **57.081** | **58.242** |

External detail: breadth 0.25->0.28, workforce 0.945->1.722, freshness 0.66->1.014, reviews 0, buzz 0.238, sentiment 0.
80 was NOT reached. Every gate still passes.

## New files
- scripts/build_wikidata_observations.py - saved Wikidata matches -> observations (needs org-number match AND agreeing name)
- scripts/merge_observations.py - safe merge; validates every new row; never overwrites
- tests/test_offline_improvements.py - 6 new tests
- tests/fixtures/sentiment-smoke-*.jsonl - the original 4-item smoke fixtures, restored from your git history
- tests/fixtures/synthetic-sentiment-*-300.jsonl - your 300-item set, renamed, clearly SYNTHETIC
- tests/fixtures/SYNTHETIC-DATA-README.md
- out/external-observations-v2.jsonl - 338 observations (your 220 + 118 new)
- reports_v2/ - every new report

## Modified files
- scripts/run_annual_report_workforce_connector.py - a cached PDF now records the time it was really downloaded, not "now"
- tests/test_poc.py, tests/test_sentiment_pipeline.py - two stale tests now use the smoke fixtures

## How to apply (Windows PowerShell, on a COPY of your project)
1. Copy this folder's contents over the copy of your project (keep folder structure).
2. Rename the old synthetic files so they cannot be mistaken for real data:
   move tests\fixtures\sentiment-gold.jsonl tests\fixtures\OLD-sentiment-gold.jsonl
   move tests\fixtures\sentiment-predictions.jsonl tests\fixtures\OLD-sentiment-predictions.jsonl
   (they are identical to the synthetic-*-300 files; delete the OLD- copies when you are happy)
3. Run tests:  uv run --with pytest pytest -q      (expect 147 passed)
4. To adopt the new observations: copy out\external-observations-v2.jsonl to out\external-observations.jsonl (keep your backup_baseline folder).

## To go further you must supply (I cannot from my sandbox: all internet is blocked)
- GOOGLE_PLACES_API_KEY (paid Google Cloud key): reviews/ratings, also a 2nd platform. Set with:  $env:GOOGLE_PLACES_API_KEY="your-key"
- BRAVE_SEARCH_API_KEY: only if your plan's terms allow the intended use.
- Open internet + tesseract/pdftoppm: run scripts/run_annual_report_workforce_connector.py on the ~700 companies without cached annual reports.
- Real, human-labelled Norwegian company news (300+) for sentiment. This cannot be generated.
