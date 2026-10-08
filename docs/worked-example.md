# Worked example: a newer lease commitment

This traces the saved demonstration, not a fresh financial check. The example is ledger row `F061`, Amazon’s leases not yet commenced. Its evidence is retained in the saved review fixtures and demo snapshot.

## 1. Define the comparison

The rule in `reader/claims.py` identifies the measure, company, unit and reporting date. The saved proposal compares `$106.347bn` at 31 March 2026 with `$137.214bn` at 30 June 2026. The date and measure prevent substitution of a different commitment category or historical column.

The recorded source is [Amazon’s 10-Q filed 31 July 2026](https://www.sec.gov/Archives/edgar/data/1018724/000101872426000026/amzn-20260630.htm). The saved contractual commitments table identifies the total and states that its units are millions. The agent converts the source amount to the rule’s billions.

## 2. Check the extraction

Part B receives a model-proposed value with quoted fragments. Code checks that those fragments occur in the saved document, that the row names the requested measure, that the units agree and that the reporting date is supported. Table headings and values are checked together.

Inspect `reader/lib/checks.py`, `reader/lib/tables.py` and the `F061` entries in the snapshot’s `proposed.jsonl`. A proposal carries its source link, period, fragments and extraction verdict. It is still pending at this stage.

## 3. Record a decision

The human approval sheet presents the paper’s value, the proposed finding and its evidence. `approvals.py record` appends a decision referencing proposal lines. Finding the same value again does not create a new human approval.

The demo retains a historical approval; it does not approve the result of the replay you just ran. The optional AI gate is a separate path with extra eligibility checks. This example needs no live gate call.

## 4. Build the public record

The builder maps the approved finding onto Figures line `F061-1`, checks its unit and period, and writes `latest` and `evidence`. It identifies the approver and marks dependent rows for recalculation. It does not rewrite the paper or calculate replacement model outputs.

Run `python3 tools/demo.py` from the repository root, then inspect the output:

```sh
python3 - <<'PY'
import json
from pathlib import Path
review = json.loads(Path('.local/demo/review.json').read_text())
for section in review['sections']:
    for row in section['rows']:
        for figure in row['figures']:
            if figure['id'] == 'F061-1':
                print(json.dumps(figure, indent=2, ensure_ascii=False))
PY
```

Compare `paper`, `latest`, `evidence` and `state` with the saved proposal. The [review schema](../review/README.md) defines the public fields. Reader tests exercise rejected values, incorrect periods and units; review tests check that unapproved or mismatched findings stay out of the public result.
