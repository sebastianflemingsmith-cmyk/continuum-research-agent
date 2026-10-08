# Security and private data

The checkout contains source code and a dated, curated demonstration dataset. It
does not contain live configuration or credentials. Tests and the demonstration
block outbound socket connections and require no API keys.

Create a runtime below the ignored `.local/` directory with `tools/workspace.py`.
Fill the configuration examples only in that runtime. The SEC contact details go
in `watcher/config.json`; the DeepSeek key goes in `reader/config.json`. Never
commit either file or paste credentials into an issue, pull request, or log.

Live reading sends document text and the relevant research claims to DeepSeek.
The AI gate also sends proposed evidence for a second check. Only run those paid
commands with material you are authorized to send. The gate is a second call
using the reader's model configuration, not independent model verification.

The public CI has read-only repository permissions, no application credentials,
no paid runs, no deployment steps, and no automatic approval or publication of
research results. GitHub Actions are pinned to full commit IDs.

`tools/security_check.py` scans tracked files, compressed text and workbook XML,
and reachable Git history for credential patterns and personal paths. It reports
locations rather than secret values. Pattern checks are a guard, not a guarantee;
review new data manually before publication. Do not force-add ignored files.

The website output is built with an explicit field allowlist. Publication of
source data is a separate decision: do not assume that the review builder's
filtering makes input files safe to share.

For a suspected credential exposure, revoke the credential first. Do not post the
value publicly. Report a vulnerability privately through GitHub's security
advisory feature if enabled; otherwise contact the maintainer through their
public profile without including secrets in the initial message.
