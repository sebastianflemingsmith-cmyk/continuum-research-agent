# Reader

Both parts compare explicit rules with source evidence and write proposals. Neither edits the paper or approves a proposal.

## Part A: labelled SEC data

`reader_sec.py` uses `SPECS` to select financial labels and reporting periods. It checks paper figures and basket-company inputs, writes a report and appends proposals. It makes no model calls.

## Part B: text and tables

`reader_text.py` finds documents, combines them with supported watcher discoveries and uses DeepSeek to propose extractions. Code checks fragments against the source, then checks measure, value, units and period. A failed extraction never confirms the paper.

From a configured runtime ([setup](../docs/live-use.md)):

```sh
python3 reader/reader_text.py                 # plan; may download, no paid model call
python3 reader/reader_text.py --only NVDA     # plan one company
```

The operator runs `--go` when ready for paid calls. `--from-cache` uses saved documents; replays ignore live watcher discoveries unless `--handoff FILE` is explicit. `--without-handoff` uses only the independent search. The repository’s `tools/demo.py` provides an isolated offline replay.

## Code map

| File | Role |
|---|---|
| `claims.py` | Measures, baselines, components, formulas and document plan. |
| `companies.py` | Company identifiers and fiscal calendars. |
| `lib/documents.py`, `lib/handoff.py` | Selection, downloads and watcher accounting. |
| `lib/ai.py` | Model requests and saved responses. |
| `lib/checks.py`, `lib/tables.py`, `lib/extract.py` | Evidence, values and periods. |
| `lib/verdicts.py`, `lib/leads.py` | Paper comparisons and research leads. |
| `lib/openlist.py`, `lib/report.py` | Decisions, repeated findings and reports. |

## Records

`proposed.jsonl`, `runs.jsonl` and `decisions.jsonl` are append-only. Decisions reference proposal line numbers: never reorder or remove live proposal lines.

`open_proposals.py` rebuilds the open list. Repeated findings merge; a different newer finding can reopen an item while retaining its earlier decision. Documents and responses are saved for replay.

Handoff reports distinguish checked, failed, unsupported, deferred and previously checked items. Unsupported evidence is not treated as read. An empty handoff does not suppress the independent search.

## Verification

Run `python3 tools/check.py` from the repository root. The suite covers saved Part A data and Part B responses, quote failures, period and unit errors, deduplication, handoffs, publication rules and privacy allow-lists. Saved replays are not new model or live-network tests.
