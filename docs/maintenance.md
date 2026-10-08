# Maintaining the public edition

This is a curated edition of a working research system. Runnable code and inspection evidence are public; operational records and credentials stay private. Fresh Git history keeps earlier private commits out of this repository.

## Updating implementation

1. Start from a recorded source revision and a clean public branch. Review implementation changes deliberately; do not copy the entire working directory.
2. Copy intended code changes only. Keep configuration examples empty and retain the separate runtime.
3. Run `python3 tools/check.py` and `python3 tools/demo.py`. Compare outputs on identical inputs when claiming unchanged behaviour.
4. Review the staged diff, including fixtures and binary workbooks. Check files and Git history for secrets, contact details, machine paths and private notes.
5. Update scope and documentation when behaviour changes, then publish through a reviewed change.

Security checks are a backstop, not proof that unreviewed data is safe. Never copy private `config.json` files into the public checkout, even temporarily.

## Updating example data

Dates belong in the evidence. Do not relabel historical outputs as a current check. Preserve each unit, source link, quote and reporting period.

Decisions refer to proposals by line number. If a new curated snapshot filters records, rebuild all affected references and verify its approved review against the source. Do not rewrite operational originals.

Inspect workbook internals as well as visible cells: properties, comments, links and hidden sheets can expose information. Retain fields needed by runtime rules and the public example.

Keep regression cases for important failures. Distinguish synthetic values from source evidence; never turn a synthetic fixture into a published finding.

## Updating the website

Build from the intended approved runtime, inspect `review.json`, then copy only publication output to the website. Keep its GitHub link and the README’s live-review link working. A website deployment does not update this repository’s historical snapshot automatically.

## Credentials and history

The demo and offline CI require no credentials. Live credentials belong in ignored configuration files or the chosen platform’s secret store. Restrict future secret-bearing workflows to trusted code; public CI should remain secret-free.

A committed secret is exposed even if a later commit removes it. Revoke or rotate it first, then address history and affected copies. Do not disclose private incident details in a public issue.
