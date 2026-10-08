"""Watcher discoveries supplement the reader's normal search; they never replace it.

Read the append-only discovery log (so missed runs are not lost) and the latest source-check report.
Attach each supported document to the normal reading jobs, once per URL. Unsupported and failed items
remain visible. Only a completed reading run writes receipts; planning never consumes discoveries.
The evidence checks, claims, proposals and human decisions are unchanged.
"""
import glob
import hashlib
import json
import os
import re
from urllib.parse import urlsplit, urlunsplit

import companies as C
import claims as R
import watcher as W
from . import documents as D


def url_key(url):
    """Ignore fragments and normalise host/scheme; preserve meaningful query strings."""
    p = urlsplit(str(url or ""))
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        raise ValueError("not an HTTP(S) document URL")
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path, p.query, ""))


def filing_key(url):
    p = urlsplit(url)
    if p.hostname not in ("www.sec.gov", "sec.gov"):
        return None
    m = re.match(r"/Archives/edgar/data/(\d+)/(\d{18})/", p.path)
    return (int(m[1]), m[2]) if m else None


def json_lines(path, problems):
    if not os.path.exists(path):
        return []
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError("expected an object")
                    rows.append(row)
                except ValueError:
                    problems.append("{} line {} could not be read; needs review".format(os.path.basename(path), n))
    except OSError as e:
        problems.append("Could not read {} ({})".format(os.path.basename(path), e.__class__.__name__))
    return rows


def load(run, path=None):
    """An explicit file is a saved watcher JSON report or discovery JSONL; default is the live handoff."""
    problems, snapshot, discoveries = [], {}, []
    if path and not os.path.isfile(path):
        raise SystemExit("Watcher handoff file does not exist: " + path)
    if not path or path.endswith(".jsonl"):
        discoveries = json_lines(path or W.LOG_PATH, problems)
    snapshot_path = path if path and not path.endswith(".jsonl") else (W.HANDOFF_PATH if not path else None)
    if snapshot_path and os.path.exists(snapshot_path):
        try:
            snapshot = W.load_json(snapshot_path, {})
            if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1:
                raise ValueError("unrecognised handoff format")
            for key in ("new", "errors", "skipped", "baselined", "checked"):
                if not isinstance(snapshot.get(key, []), list):
                    raise ValueError("invalid " + key)
                if key != "checked" and any(not isinstance(item, dict) for item in snapshot.get(key, [])):
                    raise ValueError("invalid item in " + key)
            discoveries += snapshot.get("new", [])
        except (OSError, ValueError):
            problems.append("Watcher report could not be read; its source coverage is unknown")
            snapshot = {}
    elif not path:
        problems.append("No structured watcher report yet; using any saved discoveries. Run the watcher for current source coverage.")
    receipts_path = os.path.join(run.out_dir, "handoff_receipts.jsonl")
    receipts = {}
    for row in json_lines(receipts_path, problems):
        receipts[row.get("id")] = row
    entries, seen = [], {}
    for item in discoveries:
        if not isinstance(item, dict):
            problems.append("An invalid watcher item needs review")
            continue
        try:
            key = url_key(item.get("url"))
        except ValueError:
            problems.append("Watcher item from {} has an invalid URL; needs review".format(item.get("source", "unknown")))
            continue
        if key in seen:
            # An earnings page may first appear as an event, then acquire its transcript at the same URL.
            # Prefer the later usable description instead of permanently keeping the initial event stub.
            if route(item) or not route(seen[key]["item"]):
                seen[key]["item"] = item
            continue
        uid = hashlib.sha256(key.encode("utf-8")).hexdigest()
        entry = dict(id=uid, item=item, url=key, jobs=[], status="pending", detail="", previous=receipts.get(uid))
        seen[key] = entry
        entries.append(entry)
    return dict(entries=entries, snapshot=snapshot, problems=problems, receipts_path=receipts_path)


def route(item):
    """Only existing company/document rules are used. Everything else requires an explicit future rule."""
    source = str(item.get("source") or "")
    ticker = source[3:] if source.startswith("TR-") else source
    if ticker not in C.CIK:
        return None
    form = str(item.get("form") or "")
    if form in ("10-Q", "10-K"):
        kind = "periodic"
    elif form == "8-K" and "2.02" in {s.strip() for s in str(item.get("items") or "").split(",")}:
        kind = "results"
    elif form == "6-K" and ticker == "NBIS":
        kind = "6k"
    elif source.startswith("TR-") and re.search(r"transcript", form + " " + str(item.get("description") or ""), re.I):
        kind = "transcript"
    else:
        return None
    return (ticker, kind) if (ticker, kind) in R.PLAN else None


def cached_documents(ticker, kind):
    """All saved documents of this kind, including older watcher discoveries; never downloads in replay mode."""
    out = []
    paths = glob.glob(os.path.join(D.CACHE_DIR, ticker + "_" + kind + "_*.txt"))
    paths += glob.glob(os.path.join(D.CACHE_DIR, "watcher_" + ticker + "_" + kind + "_*.txt"))
    for path in sorted(paths, reverse=True):
        with open(path, encoding="utf-8") as f:
            title, url, _, body = f.read().split("\n", 3)
        stem = os.path.basename(path)[:-4]
        meta = W.load_json(os.path.join(D.CACHE_DIR, stem + ".meta.json"), {})
        out.append((stem, dict(meta, title=title, url=url), body))
    return out


def same_document(entry, job, kind):
    if entry["url"] == url_key(job["meta"]["url"]):
        return True
    # A watcher SEC entry points to the filing cover; the reader reads the financial exhibits.
    return kind in ("results", "6k") and job["kind"] == kind and filing_key(entry["url"]) is not None and \
        filing_key(entry["url"]) == filing_key(job["meta"]["url"])


def resolve(entry, ticker, kind, run):
    """Resolve this exact discovery, never substitute the newest filing for an older watched one."""
    item = entry["item"]
    if run.from_cache:
        return [(stem, meta, body) for stem, meta, body in cached_documents(ticker, kind)
                if same_document(entry, dict(kind=kind, meta=meta), kind)]
    if kind in ("results", "6k"):
        filing = filing_key(entry["url"])
        if not filing or filing[0] != C.CIK[ticker]:
            raise ValueError("the filing URL does not match the company")
        a = filing[1]
        row = dict(acc="{}-{}-{}".format(a[:10], a[10:12], a[12:]), filed=item.get("filed", ""))
        metas = D.exhibit_documents(ticker, kind, row, run.ua)
    else:
        if kind == "periodic":
            filing = filing_key(entry["url"])
            if not filing or filing[0] != C.CIK[ticker]:
                raise ValueError("the filing URL does not match the company")
            if not re.search(r"\.html?$", urlsplit(entry["url"]).path, re.I):
                raise ValueError("the watcher has a filing directory, not a primary document")
        metas = [dict(title=item.get("description") or "{} filed {}".format(item.get("form"), item.get("filed", "")),
                      url=entry["url"], filed=item.get("filed", "") if kind != "transcript" else "",
                      form="transcript" if kind == "transcript" else item["form"], report_date="")]
    docs = []
    for meta in metas:
        # SEC contact details only go to the SEC, never to company transcript sites.
        ua = run.ua if urlsplit(meta["url"]).hostname in ("www.sec.gov", "sec.gov") else W.BROWSER_UA
        docs.append((None, meta, D.get_text(meta["url"], ua)))
    return docs


def merge(handoff, jobs, run):
    """Retain the full normal scan, then add eligible watcher documents, once per company/kind/URL."""
    for entry in handoff["entries"]:
        pair = route(entry["item"])
        ticker, kind = pair or (None, None)
        source = str(entry["item"].get("source") or "")
        if run.only and (ticker or source.removeprefix("TR-")) not in run.only:
            entry.update(status="deferred", detail="Outside this --only selection; remains pending")
            continue
        matches = [j for j in jobs if same_document(entry, j, kind)]
        if matches:
            entry.update(jobs=matches, status="ready", detail="Already in the normal search; read once")
            if any(same_document(entry, j, kind) for j in getattr(run, "document_failures", [])):
                entry.update(status="could not read", detail="At least one exhibit failed to download; the available exhibits are still read")
            continue
        if (entry.get("previous") or {}).get("status") == "checked":
            entry.update(status="already checked", detail="Handled in an earlier reader run")
            continue
        if not pair:
            entry.update(status="needs review", detail="Outside current reader coverage; no reading rule for this source/document")
            continue
        try:
            docs = resolve(entry, ticker, kind, run)
            if not docs:
                raise ValueError("no supported financial exhibit or saved document found")
            for stem, meta, body in docs:
                key = url_key(meta["url"])
                job = next((j for j in jobs if j["t"] == ticker and j["kind"] == kind and url_key(j["meta"]["url"]) == key), None)
                if job is None:
                    # Stable unique cache stem avoids overwriting a normal-search document or another discovery.
                    suffix = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
                    job = dict(t=ticker, kind=kind, stem=stem, meta=meta, text=body[:run.max_chars],
                               handoff_stem="watcher_{}_{}_{}_{}".format(ticker, kind, run.started.strftime("%Y-%m-%d"), suffix))
                    jobs.append(job)
                entry["jobs"].append(job)
            entry.update(status="ready", detail="Added from watcher discoveries")
        except (W.FetchError, OSError, ValueError, KeyError) as e:
            entry.update(status="could not read", detail=str(e))


def statuses(handoff, completed=False):
    rows = []
    for entry in handoff["entries"]:
        status, detail = entry["status"], entry["detail"]
        jobs = entry["jobs"]
        if status == "ready":
            if any(not j.get("ask") for j in jobs):
                status, detail = "needs review", "No applicable claim checks for at least one document; remains pending"
            elif completed:
                issues = [j.get("handoff_problem") for j in jobs if j.get("handoff_problem")]
                if issues or not all(j.get("handoff_read") for j in jobs):
                    status, detail = "needs review", "; ".join(issues) or "Reading did not finish; remains pending"
                else:
                    status, detail = "checked", "Read through the existing evidence checks; see the Part B report for findings"
        rows.append(dict(id=entry["id"], url=entry["url"], source=entry["item"].get("source", ""),
                         status=status, detail=detail, documents=[j["meta"]["url"] for j in jobs]))
    return rows


def report(handoff, started, completed=False):
    rows = statuses(handoff, completed)
    lines = ["# Watcher handoff — " + started.isoformat(sep=" "), "",
             "The reader keeps its full independent search. Each watcher discovery is accounted for below.",
             "Checked means the document passed through the reader; it does not mean an update was approved.", ""]
    snapshot = handoff["snapshot"]
    if snapshot:
        lines += ["Watcher run: {} · scope: {}".format(snapshot.get("run_id", "unknown"), snapshot.get("scope", "unknown")), ""]
    for problem in handoff["problems"]:
        lines.append("- Needs attention: " + problem)
    for key, label in (("errors", "Could not check"), ("skipped", "Not watched"), ("baselined", "First visit, baseline only")):
        for item in snapshot.get(key, []):
            lines.append("- {}: {} — {}".format(label, item.get("name") or item.get("what") or item.get("source", ""), item.get("why", "")))
    if not rows:
        lines += ["", "No saved watcher discoveries. The reader still runs its normal search."]
    for row in rows:
        lines += ["", "- **{}** · {} · [document]({})".format(row["status"], row["source"], row["url"]),
                  "  " + row["detail"]]
    if not completed:
        lines += ["", "Plan only: no watcher discovery has been marked checked."]
    return "\n".join(lines) + "\n"


def save(handoff, run):
    """Called only after the ordinary report/proposals have been saved successfully."""
    rows = statuses(handoff, completed=True)
    for row in rows:
        row["run_id"] = "B " + run.started.strftime("%Y-%m-%d %H.%M.%S")
        row["read_at"] = run.started.isoformat()
    base = os.path.join(run.reports_dir, "Handoff " + run.started.strftime("%Y-%m-%d %H.%M.%S"))
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(report(handoff, run.started, completed=True))
    W.save_json(base + ".json", dict(entries=rows, problems=handoff["problems"], watcher=handoff["snapshot"]))
    # Replays/custom input files are isolated from live acknowledgement state.
    if not run.replay and not run.from_cache and not run.handoff_path:
        W.append_jsonl(handoff["receipts_path"], (row for row in rows if row["status"] not in ("already checked", "deferred")))
    return base + ".md"
