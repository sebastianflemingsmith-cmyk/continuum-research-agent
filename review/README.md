# Review builder

The builder produces the public table as `review.json`. It reads the watch list, proposals, decisions and run records, then publishes only approved evidence through an explicit field allow-list. It never edits the paper, models or reader records.

Run the offline demonstration from the repository root with `python3 tools/demo.py`. For live inputs, first follow [live setup](../docs/live-use.md), then from the independent runtime run:

```sh
python3 review/build.py --out ../published
```

The output directory must be outside that runtime. With no `--out`, the builder creates a temporary output directory. It uses the standard library and makes no network requests. Identical inputs produce identical JSON; the build date is derived from input records.

## Approval and evidence rules

Only paper figures with an approval in force can be published. Leads are excluded. The builder resolves the approved proposal lines, maps their code names to the Figures sheet, and checks the paper value, unit and period. Ambiguous mappings or inconsistent approvals are withheld and reported. If a later run proposes another value, the earlier approved value remains the published evidence until a new decision permits it.

Human approvals and the restricted AI gate are distinguished in `evidence.approved_by`. Gate approvals must satisfy the additional primary-source, newer-period and non-arithmetic restrictions. The gate cannot override human rulings.

An operator ruling in `paper_differs.json` can mark an approved result as the paper's own figure stated differently. Such rulings belong to the relevant research context and should not be inherited blindly by a fresh runtime.

| State | Meaning |
|---|---|
| `newer` | Approved evidence for a later period. |
| `differs` | Approved evidence differs for the same period, or an explicit human ruling applies. |
| `recalc` | A dependent input changed; the model needs recalculation. |
| `unchanged` | Watched, with no published change; check details remain visible. |
| `not_watched` | No reader rule checks this figure; the reason is shown. |

Part B evidence includes verified quoted fragments; Part A identifies the SEC labelled data; arithmetic includes its inputs and is labelled ESTIMATE. A failed or unsupported extraction never counts as confirmation of the paper. The builder flags dependencies but does not silently recalculate the paper's models.

## Publication boundary

Only the fields listed below reach the website. This boundary does not make input files safe to commit: a public repository exposes its committed files directly, including files the builder never reads. Keep live configuration, raw working records and private notes in the ignored runtime.

## review.json

Shaped so that a server could return the same thing later. Only the keys below are written, each by name; nothing is
copied whole. Optional keys are left out when they do not apply. Links are kept only when they start with `https://`
(the paper's PDF is the site's own path).

**Never written:** any Notes column, Status at baseline, How to extract, Automation, Impact, What would supersede it,
Next expected, the Baseline check sheet; a decision's note, suggested, suggested_why, sheet, decided_at, lines, checks,
answer or gate_run (its decided_by only as `approved_by`: `Seb` or `AI gate`); the AI gate's questions, DeepSeek's
answers and the gate's reports; a proposal's note, why, topic, related_rows, extraction, paper_check or run_id; the open
list's decision_made, to_do or earlier_decision; file paths. The tests check every key against this list and look for
every note's text in the output.

| Object | Keys |
|---|---|
| review (the file) | `schema` (1), `built` (the newest input: decision, run or watcher run), `paper`, `runs`, `counts`, `sections`, `bullets` |
| paper | `title`, `date`, `pdf` (the site's path to the PDF) |
| runs | `watcher` (handoff.json `run_id`), `part_b` (newest real run in runs.jsonl), `part_a` (the Part A report's time) |
| counts | `rows`, `figures`, `watched_rows`, `watched_figures`, `states`, `filters` |
| counts.states | figure lines in each state: `newer`, `differs`, `recalc`, `unchanged`, `not_watched` |
| counts.filters | rows: `all`, `changes` (at least one `newer` or `differs` line), `recalc` (at least one `recalc` line) |
| section | `name` (the ledger's "Paper section"), `rows` (ledger order) |
| row | `id`, `type`, `says` ("What the paper says"), `built_from` (row IDs), `models` (model entries of "Also used in"), `citation`, `figures` |
| citation | `fn` ("Paper fn"), `document` ("Primary document"), `links` ("Link", https only) |
| figure | `id`, `label` ("What the figure is"), `paper`, `watched`, `state`; when not watched: `dot`, `reason`; when published: `latest`, `evidence`, and for `newer` `affects`; for `recalc`: `recalc`; watched and not published: `last_checked` |
| figure.paper | `printed` ("As the paper prints it", when there is one), `shown`, `value`, `unit`, `period` ("Period / as of", word for word) |
| latest | `shown`, `value` (exact), `period_end` (ISO date), or for arithmetic `inputs_from` |
| evidence | `source` (Part B, Part A or arithmetic), `basis`, `how`, `document`, `link`, `link_text`, `figure`, `period_end`, `quote`, `formula`, `inputs`, `also`, `approved_by` (`Seb` or `AI gate`) |
| quote | `role`, `text` |
| input | `name`, `value`, `source` |
| affects | `models`, `rows`, `text` |
| affects row | `id`, `models` |
| recalc | `path` |
| last_checked | `text`, `date`, `by` (Part A or Part B) |
| bullet | `figure`, `row`, `segments` |
| segment | `text`; `href` (a link); `figure` (a link to that figure in the table) |

`SCHEMA` in `build.py` holds the same list; the builder refuses to write a file with any other key.

## Verification

`python3 tools/check.py` runs review tests as part of the reader suite. Coverage includes approval/reopen behaviour, later proposals, dual mappings, period and unit checks, dependency states, exact schema keys and excluded private fields. Historical fixtures and synthetic cases are checked offline.
