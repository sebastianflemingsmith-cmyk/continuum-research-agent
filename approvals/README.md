# Approvals

The reader proposes; this step records a decision. Publication is a separate builder step. Neither changes the paper or models.

From an independent runtime ([setup](../docs/live-use.md)):

```sh
python3 approvals/approvals.py make
python3 approvals/approvals.py check
python3 approvals/approvals.py record
```

`make` creates an `.xlsx` sheet in `approvals/sheets/`. Fill its Decision column and save before `check`. `check` previews without writing decisions. `record` appends valid decisions and rebuilds lists. Invalid rows prevent an import; old sheets cannot silently replace newer human decisions.

| Item | Decisions |
|---|---|
| Figure or arithmetic | approve, keep paper, reader wrong |
| Lead | use, dismiss |
| Old item | dismiss |
| Approved item awaiting action | done |
| Undecided item | later or blank |

`python3 approvals/approvals.py reopen F061` reopens a row’s decisions. Add `--gate` to target gate approvals. Decisions reference proposal line numbers; both histories must remain append-only.

## Optional AI gate

`python3 approvals/gate.py` previews eligible cases without asking the model or recording approvals. See `--help` for `--from-line`, saved-answer recording and `--ask`. The operator explicitly enables new paid calls.

The gate can approve newer primary-source figures only after code checks and a separate call using the same reader model configuration. It excludes arithmetic, company disclosures, ambiguous mappings and cases reserved for human review. It checks periods, basis, magnitude and decision history; model agreement alone is insufficient.

Human decisions take precedence. Pending local spreadsheet items are held back. This protects only sheets visible in the runtime; a remote process cannot see an unshared local sheet.

Gate questions, answers and reports stay in the private runtime. The public review identifies only the allowed approver (`Seb` or `AI gate`). `suggest.py` holds separate spreadsheet suggestion rules; suggestions are not decisions.

## Outputs and tests

The step appends `reader/decisions.jsonl` and rebuilds the open list, approved changes, selected leads and reader mistakes. These records can contain private notes and are not website assets.

Marking a task done does not change the explicit baselines in the ledger or code. Run `python3 tools/check.py` from the repository root for offline import, gate, replay and human-override tests.
