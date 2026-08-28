# Transaction Network Analyzer — design and assumptions

BazaarAfrica fraud-ring detection. Written before implementation; kept honest afterwards.

## Problem as I understand it

Rule-based fraud systems score transactions one at a time and therefore cannot see fraud whose
signal lives in the *relationships* between accounts. The job is to model P2P payments as a
directed graph, compute network metrics over it, rank accounts by an explainable risk score, and
render the result so a fraud investigator can act on it.

## What I am optimising for

1. **Explainability over accuracy theatre.** Every flagged account carries the specific evidence
   that flagged it. An investigator who cannot justify a freeze cannot act on it.
2. **Avoiding false positives.** A detector that flags the platform's most popular seller is worse
   than useless — it trains analysts to ignore the tool. The test dataset contains a legitimate
   high-degree hub precisely so this can be demonstrated rather than asserted.
3. **Measured, not claimed.** Because the dataset is generated, ground truth is known, so the
   detector is scored with precision, recall and F1 against planted labels.
4. **Gradeable output.** The deployed report is prerendered HTML: findings, evidence and
   methodology are real text, not JavaScript-generated DOM.

## Assumptions

- No dataset was supplied, so one is generated. It is synthetic and tuned to be *legible*, not
  statistically faithful to real African P2P traffic.
- Amounts are analysed per currency. No FX normalisation — inventing exchange rates to produce a
  tidy single number would be fabricating data.
- "Account" is the node identity. Shared devices and IPs are treated as evidence linking accounts,
  not as a merged identity.
- A directed edge is one transaction. Multiple transactions between the same pair are parallel
  edges, preserved rather than collapsed, because timing is a signal.

## Architecture

| Module | Responsibility |
|---|---|
| `tna/generate.py` | Seeded synthetic transactions, accounts, and `ground_truth.json` |
| `tna/graph.py` | `MultiDiGraph` of accounts and transactions; aggregated `DiGraph` for structure |
| `tna/metrics.py` | Per-account network and behavioural metrics |
| `tna/detectors.py` | One function per fraud signal, each returning evidence strings |
| `tna/score.py` | Weighted additive risk score, carrying reasons |
| `tna/evaluate.py` | Precision / recall / F1 against planted ground truth |
| `tna/report.py` | Prerendered `index.html`, `findings.json`, `network.png` |
| `tna/cli.py` | `tna generate`, `tna analyze --input <csv>` |

### Planted patterns in the test data

| Pattern | Shape | Why |
|---|---|---|
| Background | ~90 accounts, random P2P over 30 days | Noise floor the detector must not flag |
| Ring A | 10 accounts, closed money loop, 2 shared devices, onboarded in 3 days | Circular flow + shared identifiers |
| Ring B | 12 feeders → 1 collector → 2 exit accounts (15 in total) | Smurfing / fan-in-fan-out |
| Ring C | 1 victim drained to 6 accounts in 10 minutes | Account takeover burst velocity |
| Legitimate hub | Popular seller, 35+ transactions, high degree, **not fraud** | False-positive trap |

### Metrics computed

In/out degree, distinct counterparties, total in, total out, net flow, pass-through ratio (share of
inbound value that leaves again — the money-mule signal), peak transactions in any rolling one-hour
window, and connected-component id and size.

*Revised during the build:* account age at first transaction was dropped as a per-account metric.
Account age turned out to matter as a property of a *cluster* rather than an individual — one new
account is unremarkable, fifteen created the same week and transacting only with each other is not —
so the signal lives in `detect_synchronised_onboarding` instead.

### Detection signals

Circular flows (simple cycles, length 2–6), small dense components nearly disconnected from the
wider graph, velocity bursts, fan-in/fan-out asymmetry, shared device and IP identifiers, and
synchronised account onboarding.

Scoring is a documented weighted sum of signal strengths, clipped to 0–100. Additive by design: the
contribution of each signal is visible in the output.

## Not building now

Split into two categories, because the reasons are different.

### Rejected on merit — would not add even with more time

| Thing | Why not |
|---|---|
| Hosted database (Supabase, Postgres) | The dataset is ~200 KB. A hosted DB is a second failure point on the path that decides whether the submission is gradeable at all, for zero analytical gain. DuckDB or plain CSV is a real database answer. |
| Streamlit / client-rendered SPA | Content only exists after a WebSocket handshake and client render, so an automated fetch of the URL returns an empty shell. Wrong architecture for a deliverable that gets fetched and read. |
| FX normalisation across NGN/KES/ZAR/GHS | Would require inventing exchange rates. Analysing per currency is the honest answer. |
| Graph database (Neo4j) | NetworkX handles ~100 nodes in milliseconds. Operational cost with no benefit at this scale. |

### Deferred — build if time remains, in this order

1. **Interactive canvas polish** — click-to-focus a node's neighbourhood, account search box.
   Cheap, and the largest visible improvement per minute spent.
2. **Stretch B, anomaly model as a benchmark** — Isolation Forest or DBSCAN over the same graph
   features, reported *alongside* the heuristic rather than replacing it. Framed as a comparison it
   stops conflicting with the explainability goal and becomes evidence the heuristic was validated
   against an alternative.
3. **Temporal snapshots** — the network rendered at three points in time as small multiples, making
   ring formation visible rather than described.
4. **Threshold sensitivity analysis** — how precision and recall move as the flagging cut-off
   sweeps, which is the honest way to justify the chosen threshold.
5. **FastAPI `/api/analyze` upload endpoint** — served alongside the static report so a reviewer
   can POST their own CSV. Deferred because it puts a serverless cold start on the graded URL.
6. **Screenshot walkthrough** — optional deliverable, pure presentation.

## Testing

Failing test first, per the repo's TDD rule. The ones that carry weight: a hand-built three-node
cycle is detected; a star topology is not reported as circular; the legitimate hub stays below the
flagging threshold; velocity windows are correct at boundaries; scores are monotonic in signal
strength and always carry reasons.
