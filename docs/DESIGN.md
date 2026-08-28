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

The interfaces between these modules — `Signal`, `AnalysisResult`, `render_report` — are fixed
before the modules behind them. That ordering is deliberate. Threshold tuning, report rendering,
sensitivity analysis and the adversarial dataset are four largely independent pieces of work that
share no reasoning with each other, and settling the contracts up front is what lets them proceed in
parallel without colliding in the same files or waiting on one another.

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

Inside a two-hour budget the binding constraint is not how fast code can be written. It is deciding
what *not* to build, and noticing when a plausible-looking result is subtly wrong — generating a
detector is quick, while noticing that it flags your best customer takes domain judgement. What
follows is that decision, split into two categories, because the reasons are different.

### Rejected on merit — would not add even with more time

| Thing | Why not |
|---|---|
| Hosted database (Supabase, Postgres) | The dataset is ~200 KB. A hosted DB is a second failure point on the path that decides whether the submission is gradeable at all, for zero analytical gain. DuckDB or plain CSV is a real database answer. |
| Streamlit / client-rendered SPA | The obvious answer to "interactive data tool" and the wrong one here. Content only exists after a WebSocket handshake and a client render, so an automated fetch of the URL returns an empty shell — and this page is fetched and read by tooling before a human ever opens it. Who consumes the artefact decides the architecture. |
| FX normalisation across NGN/KES/ZAR/GHS | Would require inventing exchange rates. Analysing per currency is the honest answer. |
| Graph database (Neo4j) | NetworkX handles ~100 nodes in milliseconds. Operational cost with no benefit at this scale. |
| An anomaly-detection model as the ranker | The reflex answer to "rank accounts by fraud likelihood" is Isolation Forest or DBSCAN over the graph features, and it is worse here. An investigator has to justify freezing a real person's account to a compliance officer and sometimes to the customer; "the model gave you 0.87" is not a justification. A weighted sum of named signals keeps the contribution of every piece of evidence visible and the weights arguable by the fraud team that owns them. At ~140 accounts of synthetic traffic a fitted score would also be learning the generator rather than fraud. It survives below only as a *benchmark against* the heuristic, never as a replacement. |
| Degree as a primary suspicion signal | High degree is the easiest signal to reach for and it flags the platform's busiest honest seller. The discriminator is the conduit test in `detect_fan_in_fan_out`: does the money *leave* again? An account that receives from many people and keeps the value is a popular merchant; one that forwards nearly all of it through a handful of larger transfers is a collector. The legitimate hub is planted in the test data to make the difference demonstrable rather than asserted. |
| Topology as evidence on its own | Cycle detection over a marketplace graph finds cycles everywhere — A buys from B, B buys from C, C happens to buy from A across three unrelated weeks — and coincidental loops swamp real ones by orders of magnitude. A cycle counts only if it closes quickly *and* preserves its value at each hop, which is why `graph.aggregate` carries `first_seen` and `last_seen` per edge: the detector has to ask temporal questions of a structural object. |

### Deferred — build if time remains, in this order

Two items on this list were built before the deadline and are struck through:

1. ~~**Threshold sensitivity analysis**~~ — **done.** `tna/sensitivity.py` sweeps the cut-off and the
   report renders the precision/recall/F1 curve. It justifies `FLAG_THRESHOLD = 40` two independent
   ways: 40 is the F1 maximum of the swept grid, and it sits at the top edge of the band within
   which recall stays at 1.0.
2. ~~**Adversarial hard negatives**~~ — **done**, and not originally on this list. The first
   validated run returned precision and recall of 1.000, and the temptation is to ship that. It
   proved almost nothing: every innocent account in the dataset was *trivially* innocent, so the
   detector had never been shown a hard case. Seventeen innocent-but-suspicious-looking accounts
   were therefore planted specifically to attack each detector — a bursty ticket seller, a family
   sharing a tablet, a shop settling between two accounts it owns, a flat-share splitting rent, a
   referral cohort onboarded the same week. They dropped precision from 1.000 to 0.821 and surfaced
   seven false positives, which are the most informative output in the project. Making your own
   headline numbers worse is the point: a detector that has not been tested against hard negatives
   has not been tested.

Still deferred, in priority order:

3. **Structural signals should not be sufficient alone** — the referral-cohort false positives all
   cleared the threshold on structure only (tight component + synchronised onboarding + a weak
   loop). Requiring at least one value- or volume-based signal alongside them would clear that
   cohort without weakening ring detection. Identified with too little time left to re-validate
   properly, and rushing a scoring change is how you trade seven visible false positives for an
   invisible false negative.
4. **Referral attribution as a feature** — the platform knows which accounts arrived through a
   marketing campaign. That single attribute would resolve the largest false-positive cluster,
   which is a data-availability fix rather than an algorithmic one.
5. **Interactive canvas polish** — click-to-focus a node's neighbourhood, account search box.
6. **Stretch B, anomaly model as a benchmark** — Isolation Forest or DBSCAN over the same graph
   features, reported *alongside* the heuristic rather than replacing it. Framed as a comparison it
   stops conflicting with the explainability goal and becomes evidence the heuristic was validated
   against an alternative.
7. **Temporal snapshots** — the network rendered at three points in time as small multiples, making
   ring formation visible rather than described.
8. **FastAPI `/api/analyze` upload endpoint** — served alongside the static report so a reviewer
   can POST their own CSV. Deferred because it puts a serverless cold start on the graded URL.

## Testing

Failing test first, per the repo's TDD rule. The ones that carry weight: a hand-built three-node
cycle is detected; a star topology is not reported as circular; the legitimate hub stays below the
flagging threshold; velocity windows are correct at boundaries; scores are monotonic in signal
strength and always carry reasons.

Rolling-window velocity arithmetic is the case that most justifies the discipline. Being right at
the window boundary is exactly the kind of detail that goes subtly wrong when written at speed, and
exactly the kind of detail a test pins down cheaply.

## Verification

Every claim made in the report, or in this document, is checked mechanically wherever a check is
possible.

- **The numbers are computed, not written.** Precision, recall and F1 come from `tna/evaluate.py`
  measured against planted ground truth, and are asserted in `tests/test_pipeline.py`. No figure in
  the report is typed by hand into prose.
- **The false-positive claim is a test.** "The legitimate hub is not flagged" is an assertion in the
  suite rather than a sentence in a document, as are the scores of each hard-negative population.
- **The deployment is checked from outside.** The public URL is fetched with plain `curl` from an
  unauthenticated session, because a report behind an auth wall is worth nothing to whoever needs to
  read it, regardless of its contents.
- **The weights are the one human judgement.** They encode a view about which structures imply
  coordination and which are merely circumstantial. The code records and applies that view; it does
  not derive it, and no test can validate it. That is precisely why the weights are documented in
  full and left arguable rather than fitted.
