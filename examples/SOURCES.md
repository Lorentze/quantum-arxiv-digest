# Real-paper preview provenance

`demo-state.json` contains **five real new-paper announcements**, selected from a captured official arXiv RSS feed. It is a reading-format preview, **not the output of a completed scheduled ingestion/AI API run**.

## Feed capture

- Retrieved endpoint: <https://rss.arxiv.org/rss/quant-ph>.
- RSS-declared channel / self URL: `http://rss.arxiv.org/rss/quant-ph`.
- Channel title: `quant-ph updates on arXiv.org`.
- Feed `lastBuildDate`: `Thu, 08 Oct 2026 04:00:12 +0000`.
- Feed `pubDate`: `Thu, 08 Oct 2026 00:00:00 -0400`, equivalent to `2026-10-08T04:00:00Z`.
- Local capture timestamp, taken from the downloaded XML's modification time: `2026-10-09T01:19:44Z`. This records when the local copy was saved, not a separately measured HTTP response timestamp.
- Captured feed: 145 entries — 64 new, 19 cross-list, 45 replacement, 17 replacement cross-list.
- Preview selection: 5 entries, all `announcement_type = new`, version 1.

The `published` and `updated` values copied into the preview are **RSS announcement dates**, indicated by `date_basis = announcement`. They are not independently verified original submission dates or revision dates. The snapshot announces October 8 papers even though it was captured on October 9 UTC. It is not a complete live search of October 9, all arXiv categories, or all recent relevant papers.

## Analysis provenance

The Chinese analyses were written in this ChatGPT session from the **full abstract text supplied by the RSS capture**. No full PDF or HTML manuscript was read for these five summaries. No AI API call generated these preview analyses. Each record therefore uses:

```json
{
  "status": "preview",
  "model": "ChatGPT preview (not API pipeline)",
  "evidence_level": "abstract"
}
```

All mathematical and experimental results are attributed to the authors' abstract claims. The preview does not independently verify proofs, noise assumptions omitted from abstracts, or performance figures. `follow_up` entries are proposed research questions, not additional results established by the papers. The preview state has `is_demo = true` and `last_successful_run = null`; it must not be treated as production run history.

The original Wigner-paper RSS abstract contains several question-mark substitutions in English words. The readable summary does not assert unseen details to repair those omissions. Original abstracts are not bundled in the preview; each source URL points to the abstract on arXiv.

## Selected papers and research fit

| arXiv ID | Paper | Why selected |
|---|---|---|
| [2610.10531](https://arxiv.org/abs/2610.10531) | Efficient Estimation of Logical Sensitivities Through Fault-Counting | Defines physical-to-logical noise sensitivity and provides a simulator-side estimation baseline. The summary explicitly distinguishes access to latent faults from access only to experimental syndromes. |
| [2610.08890](https://arxiv.org/abs/2610.08890) | Quantum Readout Complexity for Normalized Linear Functionals | Quantifies copies cost for a precise quantum-data output task; compares fixed single-copy measurements with classical information, adaptive allocation and coherent access. |
| [2610.10471](https://arxiv.org/abs/2610.10471) | Optimized discrete Wigner representations and non-stabilizerness in qubit systems | Links stabilizer structure to a measurement-based non-stabilizer witness. The finite-size non-monotonicity and missing sample-complexity guarantee are kept explicit. |
| [2610.09749](https://arxiv.org/abs/2610.09749) | Automated reduction of fault-tolerant circuits | Gives concrete syndrome-extraction resource reductions and noise-dependent logical performance, useful for depth–ancilla–control tradeoff formulation. |
| [2610.09341](https://arxiv.org/abs/2610.09341) | Building codes with transversal CCZ using projective geometry and SAT solvers | Connects CSS/stabilizer code design, non-Clifford implementation, and a clearly scoped physical-qubit lower bound. |

These picks prioritize concrete task/access models and quantitative claims. No direct new SPAM-robust benchmarking or unitary-design paper was forced into this selection merely to cover every profile label. The relevance scores are editorial assessments for this profile, not a quantitative quality ranking or a proof-correctness judgment.

Personal CV content, email addresses and application documents are not included in this preview.
