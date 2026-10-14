# Validation

Current arXiv and journal version:

- Official RSS endpoints for all eight requested journals were fetched and parsed successfully on 2026-10-14. Journal metadata supplements sometimes returned 429; those endpoint failures were explicitly recorded as partial source coverage and were not retried.
- Real recent metadata were screened; two articles were analyzed using official Nature publisher reference PDFs with explicit excerpt/version limitations, then passed the production importer.
- Regression tests cover RSS1/RSS2/Atom parsing, publication rather than deposit dates, journal independence on source errors, delayed abstract reconsideration, DOI/preprint deduplication with author checks, accepted-only deferral, source-bound analysis provenance, atomic imports, stable legacy GUIDs, source feeds, escaping and publisher-excerpt labels.
- Website build, all generated RSS files including source feeds, report paths and JavaScript syntax were checked. Browser screenshot verification was unavailable because no browser binary was installed.
- SciRate is disabled in profile, its evening workflow cron is removed, and the hosted evening task is paused. Daily collection remains 08:17 Asia/Shanghai; the daily hosted analysis task remains 09:00.

Deployment success must be checked from the actual GitHub Pages job, not inferred from a commit alone.
