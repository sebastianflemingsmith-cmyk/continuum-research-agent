# Source-access probe

`probe.py` checks whether a proposed host can reach the agent’s sources. It is not a scheduler, update button or deployment service.

It checks the watcher in dry-run mode, Part B’s plan, Part A’s SEC data and model-provider reachability without an API key. It makes no paid model calls or publication changes. Reports go outside the runtime; normal watcher state does not advance.

Configure an independent runtime using [live setup](../docs/live-use.md), then run:

```sh
python3 runner/probe.py
```

Inspect failures before choosing a host. Local access does not prove a cloud machine has the same access; sources can block particular hosts or require browser interaction.

Public CI is offline and needs no secrets. No live probe workflow is included. If one is added, keep contact details in the platform’s secret store, restrict execution to trusted code and inspect reports before publication. Artifacts in a public repository must not be treated as private storage.

Probe tests simulate network responses in the reader suite. Run them through `python3 tools/check.py` from the repository root.
