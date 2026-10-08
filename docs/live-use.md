# Run against live sources

First run the [offline demonstration](../README.md#try-it-locally). Live operation uses the same pipeline with a separate working directory and your own credentials. New model calls may incur charges.

## Create an independent workspace

From the repository root:

```sh
python3 tools/workspace.py --out .local/live
```

This prepares the code and curated watch list without inheriting the example’s decisions or watcher state. `.local/` is ignored by Git. Do not force-add it or publish its contents.

Create configuration files from the empty examples:

```sh
cp .local/live/watcher/config.example.json .local/live/watcher/config.json
cp .local/live/reader/config.example.json .local/live/reader/config.json
chmod 600 .local/live/watcher/config.json .local/live/reader/config.json
```

Edit those two files locally. The watcher needs your own SEC contact name and email; the reader needs your own DeepSeek API key for new AI calls. Never put keys in source code, issues, committed examples or workflow YAML. The reader and gate share the reader’s model configuration.

For PDF source extraction, install the optional dependency in a virtual environment:

```sh
python3 -m venv .local/venv
. .local/venv/bin/activate
python3 -m pip install -r requirements-live.txt
```

## Discover and review the plan

```sh
cd .local/live
python3 watcher/watcher.py
python3 reader/reader_sec.py
python3 reader/reader_text.py
```

The watcher records discoveries and source failures. Part A checks labelled SEC data. Part B’s default plan may download documents but does not call the model or acknowledge discoveries as read. Inspect coverage and failures before proceeding.

When you choose to make a paid reading run, execute this yourself:

```sh
python3 reader/reader_text.py --go
```

Add `--only NVDA` for a smaller run. Unsupported or failed documents remain visible in handoff reports. A failed check is not evidence that nothing changed.

## Approve findings

```sh
python3 approvals/approvals.py make
```

Open the generated workbook in `approvals/sheets/`, inspect the evidence, fill in its Decision column and save as `.xlsx`. Then:

```sh
python3 approvals/approvals.py check
python3 approvals/approvals.py record
```

The optional AI gate is separate. Preview its eligibility decisions with `python3 approvals/gate.py`. Use `--help` for recording and saved-answer options. `--ask` enables new paid calls; run those yourself only after reviewing the plan. Ineligible findings remain for human review.

## Build output for a website

Still inside `.local/live`:

```sh
python3 review/build.py --out ../published
```

The result is `.local/published/review.json`, relative to the repository root. Inspect it before copying it into a website. This command does not deploy anything. The output folder must be outside the live workspace.

Never deploy the live workspace itself: it may contain credentials, contact details, raw responses and private decision notes. Publish only intended website assets and checked builder output. Keep an independent private backup of the append-only records.

## Operational limits

The first watcher run establishes web bookmarks; SEC comparisons use the workbook’s dated baseline. A fresh workspace can produce a substantial historical catch-up. Sources may refuse requests; saved fixtures do not establish current access from a cloud host.

This edition has no deployed update button or scheduler. Paper-baseline changes must be reflected explicitly in the ledger and reader rules. Marking an item done does not automatically teach the reader a new baseline.
