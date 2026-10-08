# Public-edition verification

Checked on 8 October 2026 before initial publication.

- The 25 production Python files match the source revision recorded in [source-manifest.json](source-manifest.json) byte for byte.
- The original reader suite passed 284 tests and the approvals suite passed 77 tests, using an isolated copy of the curated snapshot.
- Eleven additional workspace and security tests passed. They cover private configuration exclusion, output isolation, network blocking and publication scanning.
- An independent Codex sub-agent reran the suites and demo and reviewed the publication boundary, workbook internals, documentation and CI configuration.
- The offline demo builds 345 figures in 160 table rows. Its `review.json` is byte-identical to the private source baseline on the same dated evidence, with SHA-256 `9af376022011ce20c5598c90a4ab53210964476f4b697e4b7bc74ca774e84757`.

The local tests used Python 3.9.6. CI runs the same offline checks on Python 3.11 and 3.13; consult the Actions results for their current status. No new paid model calls were made. Saved-answer tests do not establish present-day source availability or fresh model accuracy.

Private planning prose and decision notes were replaced with explicit redaction markers in curated fixtures. Workbook creator/editor properties and local paths were removed. Proposal line order, decision references and logic-relevant evidence were preserved. The original operational data was not edited.

Run the publication scanner with `python3 tools/security_check.py --history`. It checks selected files, compressed fixture contents, workbook XML and reachable Git history. Manual review complements pattern scanning; these checks are not a guarantee against every possible disclosure.
