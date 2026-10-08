# Watcher

`watcher.py` checks the watch list’s SEC feeds, releases and transcript pages for new documents. It records discoveries and source failures; the reader decides what can be checked.

Create a separate runtime with your own SEC contact details using [live setup](../docs/live-use.md). From that runtime:

```sh
python3 watcher/watcher.py
python3 watcher/watcher.py --dry-run
```

`--only sec` and `--only web` select source types. `--since YYYY-MM-DD` checks filings from a chosen date without saving normal state. First visits establish web bookmarks.

## Outputs and handoff

- `state.json`: source bookmarks.
- `log.jsonl`: cumulative discoveries.
- `reports/`: readable and structured reports.
- `handoff.json`: the latest normal run’s discoveries and coverage.

Dry runs do not advance bookmarks or replace the live handoff. Discoveries are saved before bookmarks move; an interrupted save may repeat an item, which the reader merges.

Part B combines supported discoveries with its independent search. Outcomes go separately into `reader/handoff_receipts.jsonl`. Never clear the discovery log to acknowledge reading.

Source rules are in `watcher.py`; topic terms and dependencies come from the workbook. A headline match ranks an item for attention, not as verified evidence. A failed source is not an unchanged figure.

Run `python3 tools/check.py` from the repository root for offline watcher and handoff regression coverage.
