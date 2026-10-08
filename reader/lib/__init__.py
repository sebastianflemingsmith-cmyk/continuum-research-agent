"""
The reader's code. The files you may want to edit are one level up: claims.py (what each ledger row means, and which
documents it is checked in) and companies.py (names, SEC numbers, fiscal year ends). The commands are reader_text.py
(Part B), reader_sec.py (Part A) and open_proposals.py (the open list).

  documents.py   finding, downloading, converting and saving documents; the period each one reports on
  ai.py          the prompt, the DeepSeek call, the rough cost, reading the answer (or a saved one)
  checks.py      text checks with no AI: finding a quote, numbers, dates, comparing figures, naming the measure
  tables.py      reading a table row: its cells, each column's period (dates or fiscal labels), the units
  extract.py     did the agent read the document correctly? (the extraction check, item by item)
  verdicts.py    does the paper agree? (one verdict per component, and the arithmetic)
  leads.py       new facts: checked word for word, then sorted (set aside, merged, already listed)
  report.py      the run's report and the proposals it adds to proposed.jsonl
  openlist.py    the open list: every proposal still waiting for a decision, with repeats merged
"""
