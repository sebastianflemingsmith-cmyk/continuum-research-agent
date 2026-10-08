# Architecture

The pipeline keeps discovery, extraction, decisions and publication separate. Its rules describe one paper. A new report requires a new ledger and explicit reader rules; uploading a PDF is not enough.

## Data flow

1. The workbook supplies source lists, ledger rows, figure mappings and dependencies.
2. `watcher/watcher.py` discovers documents and writes bookmarks, a cumulative log and structured handoff. It does not interpret financial evidence.
3. `reader/reader_sec.py` reads labelled SEC data using `SPECS`. `reader/reader_text.py` combines an independent document search with supported watcher findings. Its default mode shows a plan; `--go` enables paid extraction.
4. `reader/lib/` checks quoted fragments, units, measures, table columns and reporting periods. The reader appends proposals and run records. `openlist.py` applies decisions and merges repeated findings.
5. `approvals/approvals.py` imports human spreadsheet decisions. `approvals/gate.py` may approve narrowly eligible primary-source updates after code checks and a separate call to the configured model.
6. `review/build.py` writes `review.json` from approved evidence using a named allow-list of public fields. The website renders that file.

## Where to read the code

| Question | Code |
|---|---|
| What does a paper claim mean? | `reader/claims.py`, `reader/companies.py`, `reader/reader_sec.py` |
| Which documents are considered? | `watcher/watcher.py`, `reader/lib/documents.py`, `reader/lib/handoff.py` |
| Is the extraction supported? | `reader/lib/checks.py`, `reader/lib/tables.py`, `reader/lib/extract.py` |
| How does it compare with the paper? | `reader/lib/verdicts.py` |
| What happens to repeated findings? | `reader/lib/openlist.py` |
| What may the AI gate decide? | `approvals/gate.py` |
| What may reach the website? | `review/build.py` and its `SCHEMA` |

## Trust boundaries

Documents and model responses are inputs to check. Model output alone never publishes a figure. A verified extraction becomes a proposal; it then needs an approval in force and must pass the builder’s mapping, value, unit and period checks.

The optional gate excludes arithmetic, company-disclosure-only evidence, ambiguous mappings and cases reserved for human review. Its separate call uses the same model configuration and can repeat the reader’s error. Deterministic checks and human override therefore remain important.

`proposed.jsonl`, `runs.jsonl` and `decisions.jsonl` are append-only runtime records. Decisions identify proposals by line number. Reordering or filtering proposals without rebuilding and verifying those references changes their meaning.

The JSON allow-list protects website output. It does not sanitise a repository: committing an input file makes that file public even when the builder excludes its fields.

## Reproducibility

The demo builds a review from historically approved records and separately replays saved source text and responses. Replaying an answer tests the current checking code, not a fresh model call or current network availability. The builder derives its timestamp from inputs rather than the wall clock, so identical inputs produce identical JSON.

Historical and synthetic fixtures preserve regression cases, including previous reader mistakes. Credentials and generated runtime files belong in an ignored `.local/` workspace. A fresh live workspace does not inherit historical decisions or watcher bookmarks.
