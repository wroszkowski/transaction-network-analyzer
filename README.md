![Python](tests/report/transaction-network-analyzer-requires-python.svg)
![Version](tests/report/transaction-network-analyzer-version.svg)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/ruff)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![ty](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ty/main/assets/badge/v0.json)](https://github.com/astral-sh/ty)
[![Pre-commit](https://img.shields.io/badge/Pre--commit-Enabled-brightgreen?logo=pre-commit)](https://github.com/pre-commit/pre-commit)
![Coverage](tests/report/coverage.svg)
![Tests](tests/report/tests.svg)

# Transaction Network Analyzer

Fraud-ring detection for BazaarAfrica, a C2C marketplace operating across Nigeria, Kenya, South
Africa and Ghana. The tool ingests a transaction ledger, builds a directed payment graph over it,
computes per-account network and behavioural metrics, runs six independent detectors that each
return the sentence an investigator will read, combines their evidence into a weighted risk score,
sweeps the flagging threshold to show what every other cut-off would have produced, and renders a
prerendered HTML report with a ranked findings table, cluster breakdowns and an interactive network
view. Rule-based systems score transactions one at a time and so cannot see
fraud whose signal lives in the relationships between accounts; this models the relationships
directly.

- Live report: **https://transaction-network-analyzer.vercel.app**
- Repository: **https://github.com/wroszkowski/transaction-network-analyzer**

## Quickstart

```bash
just init                      # uv venv, sync dev deps, install pre-commit hooks, run the gate
just demo                      # generate the dataset, analyse it, write the report into public/
just analyze input=<csv>       # analyse any ledger with the same columns
just all-hooks                 # ruff check, ruff format, ty, pytest
```

`just demo` is the one command that shows everything: it writes `data/transactions.csv`,
`data/accounts.csv` and `data/ground_truth.json`, runs the full pipeline over them, and writes
`public/index.html`, `public/findings.json` and `public/network.png`. Open `public/index.html`.

The recipes are thin wrappers over the CLI, which can be driven directly:

```bash
uv run python -m tna.cli demo --seed 42
uv run python -m tna.cli generate --seed 42 --out data
uv run python -m tna.cli analyze --input data/transactions.csv \
    --accounts data/accounts.csv --truth data/ground_truth.json --out public
```

`--accounts` and `--truth` are optional. Without an accounts file the onboarding detector has
nothing to work with and is silent; without ground truth the pipeline runs and reports, it just
cannot score itself. The required transaction columns are `transaction_id`, `timestamp`,
`sender_account`, `receiver_account`, `amount`, `currency`, `device_id`, `ip_address`,
`payment_method`.

## Approach

Every transaction becomes one directed edge from sender to receiver in a NetworkX `MultiDiGraph`
(`tna/graph.py`). Parallel edges are preserved rather than collapsed, because the timing between two
accounts is itself a signal — nine payments from one account to six others inside ten minutes is a
different event from nine payments spread across a month, and collapsing repeats destroys exactly
the burst pattern the velocity detector looks for.

Structural algorithms want the other view, so `aggregate` collapses parallel edges into a `DiGraph`
whose edges carry `total_amount`, `count`, `first_seen` and `last_seen`. Cycle enumeration and
connected-component analysis run over that aggregate. The timestamps survive aggregation
deliberately: a loop of payments is only suspicious when it closes quickly, and that question cannot
be answered from topology.

The pipeline in `tna/analyze.py` is a straight line — build graph, aggregate, compute metrics, run
detectors, score, evaluate against ground truth if present — and returns one `AnalysisResult` object
holding every intermediate, so any stage can be inspected without re-running the others.

Amounts are analysed per currency. There is no FX normalisation across NGN, KES, ZAR and GHS,
because inventing exchange rates to produce a single tidy number would be fabricating data.

## Metrics

One row per account, computed in `tna/metrics.py`.

| Metric | Why it discriminates |
|---|---|
| `in_degree`, `out_degree` | Raw volume; the asymmetry between them is what marks a collector. |
| `distinct_counterparties` | Separates thirty deals with thirty people from thirty deals with one. |
| `total_in`, `total_out` | Value moved, as opposed to transaction count. |
| `net_flow` | Value retained. A merchant accumulates; a mule ends near zero. |
| `pass_through_ratio` | Share of inbound value that left again. 1.0 is a pure conduit — the mule signature. |
| `peak_velocity` | Most transactions the account was party to in any rolling 60-minute window. A drained account spikes; ordinary trade does not. |
| `component_id`, `component_size` | Which weakly connected cluster the account sits in. Real users are embedded in the large component; a provisioned ring is its own island. |

## Detection signals

Six detectors in `tna/detectors.py`. Each returns `Signal` objects carrying an account, a strength
in 0–1, and a plain-English evidence string. Detectors never decide guilt; `tna/score.py` combines
them.

| Signal | Weight | Fires when |
|---|---|---|
| `circular_flow` | 35.0 | The account sits on a simple cycle of length 2–6 that closes within `CYCLE_MAX_SPAN_DAYS = 5.0` and preserves at least `CYCLE_MIN_VALUE_COHERENCE = 0.25` of its value at every hop. |
| `tight_component` | 25.0 | The account's weakly connected component has 3–25 members, at least 1.2 edges per node, and no ties outside itself. |
| `fan_in_fan_out` | 25.0 | `in_degree >= 5`, `pass_through_ratio >= 0.7`, and `out_degree < in_degree / 2`. |
| `velocity_burst` | 20.0 | `peak_velocity >= 8` transactions inside one hour. |
| `synchronised_onboarding` | 15.0 | Four or more accounts in one component were all created within 3 days of each other. |
| `shared_identifiers` | 15.0 | Two or more distinct accounts send from the same `device_id` or `ip_address`. |

The score is the weighted sum of `weight × strength` over the distinct signals an account fired,
clipped to 100. A signal that fires repeatedly for one account — three separate loops, say — counts
once, at its strongest, so a single structure cannot inflate a score by being detected several
times. Accounts at or above **`FLAG_THRESHOLD = 40.0`** are surfaced for investigation.

The weights encode a view, not a truth: structural evidence outranks circumstantial. A closed money
loop that conserves value is hard to explain innocently. A shared IP address could be a family, a
shared office, or a cybercafé, so on its own it is worth 15 points against a 40-point bar — never
enough to flag anyone by itself.

## Why a weighted sum and not a model

The obvious instinct for "rank accounts by fraud likelihood" is an anomaly-detection model —
Isolation Forest or DBSCAN over the graph features. That would be worse here. An investigator has to
justify freezing a real person's account, to a compliance officer and sometimes to the customer, and
"the model gave you 0.87" is not a justification. A weighted sum of named signals keeps the
contribution of every piece of evidence visible and the weights arguable by the fraud team that owns
them. Every flag in the output traces back to the specific structure that produced it, in a sentence
a human wrote the template for.

It is also the honest choice at this data volume. A model trained on ~100 accounts of synthetic
traffic would be learning the generator, not fraud.

## The false-positive problem

This is the part the tool is actually built around. A detector that flags the platform's busiest
honest seller is worse than useless, because it trains analysts to ignore the tool. Three things
address it.

**The legitimate hub.** `ACC_HUB_SELLER` is planted in the test data precisely as a trap: 36
transactions, 36 distinct counterparties, more than any member of any fraud ring. It scores **0.0**.
Not "below the threshold" — zero, because no detector fires on it. Its pass-through ratio is 0.0
(it keeps what it earns), its peak velocity is 1 (its trade is spread evenly across the month rather
than concentrated in bursts), and it sits inside the 97-account main component rather than an island.
High degree is the easiest signal to reach for and it is the wrong one.

**The conduit test.** `detect_fan_in_fan_out` does not fire on fan-in alone. It requires
`pass_through_ratio >= 0.7` — the money has to leave again. An account that receives from many
people and *keeps* the value is a popular seller; an account that receives from many people and
forwards nearly all of it through a handful of larger transfers is a collector. That one condition
is the entire difference between flagging a mule and flagging a merchant.

**Coincidental cycles.** The first version of `detect_circular_flows` flagged 40-odd innocent
background accounts. The reason is structural, not a bug: in a 97-node component carrying 340 random
edges, hundreds of accounts sit on *some* cycle of length ≤ 6 by pure chance. A buys from B, B buys
from C, C happens to buy from A, across three unrelated weeks. **Topology alone is not evidence.**

Two constraints separate laundering from coincidence, and both required `graph.aggregate` to carry
`first_seen` and `last_seen` per edge so the detector could ask temporal questions of a structural
object:

- `CYCLE_MAX_SPAN_DAYS = 5.0` — a laundering loop closes in hours or days. Value is moved round the
  ring deliberately, not across a month of unrelated trade.
- `CYCLE_MIN_VALUE_COHERENCE = 0.25` — the ratio of the smallest hop to the largest. Laundering
  conserves value minus a cut, so every hop is roughly the same size. Coincidental loops join
  payments of wildly different amounts, because nothing connects them.

With those two conditions the same detector stops firing on the background traffic entirely. It does
still fire on innocent accounts whose payments genuinely form a fast, value-coherent loop — a shop
settling with its own payouts account, a parent reimbursed by a teenager the next morning — and those
cases are the subject of the results section below. The constraints remove coincidence, not
ambiguity.

## The test data

No dataset was supplied with the brief, so `tna/generate.py` builds one, seeded and deterministic.
That is an advantage rather than a compromise: because the planted fraud is labelled in
`ground_truth.json`, the detector can be measured rather than merely demonstrated.

| Population | Size | Shape |
|---|---|---|
| Background | 90 accounts, 340 transactions | Unrelated people paying each other at irregular intervals over 30 days. One pair shares a household IP, `197.210.44.10`, so that a shared IP on its own cannot flag anyone. |
| Legitimate hub | 1 account, 36 transactions | A popular seller. High degree, many counterparties, keeps what it earns, spread across the month. **Not fraud.** |
| Ring A — circular | 10 accounts | Freshly minted, moving the same value round overlapping 3-account loops that close within an hour, on two shared devices. |
| Ring B — smurfing | 15 accounts | 12 feeders consolidating into `SMR_COLLECTOR`, value layered back out to half the feeders and returned, then exiting through two accounts. Three shared devices. |
| Ring C — takeover | 7 accounts | A long-dormant account drained to six mules in minutes from one device and IP, the mules then cycling value among themselves. |
| Hard negatives | 17 accounts | Five innocent populations built to attack a specific detector. **Not fraud, and not softened to keep the score clean.** |

140 accounts and 525 transactions in total, of which 32 accounts are labelled fraudulent. The
generator plants layered short cycles rather than one long loop for ring A, and returns value to the
feeders in ring B, because that is what real laundering looks like — value broken up and recombined
so that no single hop looks unusual.

### The hard negatives

In the first version of this dataset every innocent account was trivially innocent — background
traffic with no structure to it, plus one legitimate hub — so the detector was never asked the only
question that matters, which is whether it can tell a fraud ring from an innocent group of accounts
that looks like one. The innocents were therefore made adversarial on purpose. Each population
targets one detector, and `Dataset.hard_negatives` in `tna/generate.py` carries, per account, the
sentence explaining why that account looks suspicious.

| Population | Size | Detector it attacks |
|---|---|---|
| Bursty ticket merchant (`MERCH_TICKETS_88`) | 1 account | `velocity_burst`. Sells 15 tickets in half an hour, twice in the month — a drop-day queue with the velocity profile of an account being emptied. |
| Shared-device household (`HH_PARENT_01`, `HH_TEEN_02`, `HH_TEEN_03`) | 3 accounts | `shared_identifiers` and `circular_flow`. One family tablet, one home IP, three identities, and money moving between them in both directions. |
| Business settlement pair (`SHOP_TRADE_71`, `SHOP_PAYOUT_72`) | 2 accounts | `circular_flow`. A shop's month-end reconciliation moves nearly identical sums back and forth between its own two accounts inside three days, from one back-office terminal — fast and value-coherent by design. |
| Isolated friend group (`FRND_R1`–`FRND_R5`) | 5 accounts | `tight_component`. A flat-share paying rent and bills only to each other: dense internal edges, no outside ties. |
| Referral cohort (`REF_C0`–`REF_C5`) | 6 accounts | `synchronised_onboarding` and `tight_component`. Six accounts a marketing campaign signed up inside two days, who then trade with each other. |

The populations are appended after the existing generators, so the random stream feeding the
background, the hub and the three rings is untouched. Not one pre-existing account's score changed
when the hard negatives were added; those parts of the ledger are byte-identical. The precision drop
below is caused entirely by the new accounts, not by the data shifting under the detector.

## Results and validation

**A detector that has not been tested against hard negatives has not been tested.** The first pass
of this project scored precision 1.000, recall 1.000, F1 1.000 against a dataset whose innocent
accounts were all trivially innocent. Then seventeen innocent-but-suspicious-looking accounts were
planted on purpose, each aimed at one detector, and precision fell to 0.821. The seven false
positives that produced are the actual result of this project; the headline metrics are context for
them.

Measured against planted ground truth in `tna/evaluate.py`, seed 42:

| | |
|---|---|
| Precision | 0.821 |
| Recall | 1.000 |
| F1 | 0.901 |
| Flagged | 39 |
| Actually fraudulent | 32 |
| False positives | 7 |
| False negatives | 0 |

Recall held at 1.000: every planted ring member is still caught, and adding the hard negatives cost
nothing on that side. Precision is what the adversarial cases bought, and it is the honest number.
Note also what this did to separability — the highest-scoring innocent account now sits at **59.4**
and the lowest-scoring fraudulent account at **40.9**. The two classes overlap. No threshold on this
score can now separate them, which was not true before and is the more realistic situation.

### The seven false positives

| Account(s) | Score | Signals |
|---|---|---|
| `REF_C0`, `REF_C1`, `REF_C2` | 59.4 | `circular_flow`, `tight_component`, `synchronised_onboarding` |
| `REF_C3`, `REF_C4` | 53.6 | `circular_flow`, `tight_component`, `synchronised_onboarding` |
| `HH_PARENT_01`, `HH_TEEN_03` | 40.7 | `circular_flow`, `shared_identifiers` |

**The referral cohort — five of six flagged, and a genuine detector weakness.** This is the most
useful finding in the project. `synchronised_onboarding` and `tight_component` cannot distinguish
"the marketing team onboarded six people on Tuesday" from "someone provisioned six accounts on
Tuesday", because the observable is identical: accounts created together, transacting together, with
no outside ties. Three of them then traded in a loop that closed in 2.8 days at 54% value coherence,
which is a laundering signature and also just six people from a campaign WhatsApp group buying and
selling second-hand phones. The detector is not wrong about the structure; it is missing an
attribute. The platform *knows* which accounts arrived through a referral campaign — that fact
exists in the marketing system and is simply not in the feature set. This is the most concrete and
most fixable of the failures.

**The household pair — genuinely ambiguous, and thinly evidenced.** Three identities on one device
with money moving between them is exactly the mule-farm pattern, and a ledger cannot see a family. I
do not think the detector is wrong to notice this. What is wrong is the weight of evidence behind
the flag: `HH_PARENT_01` and `HH_TEEN_03` clear a 40-point bar on 25.7 points of cycle plus 15
points of shared device, with no volume evidence, no value evidence and no pass-through at all.
Those are thin grounds for freezing a parent's account.

The pair also exposes something fragile about cycle detection. The *elder* teenager,
`HH_TEEN_02`, receives pocket money twice across the month and pays a share of the phone bill back
— and is **not** flagged, scoring 15.0, because those payments aggregate into one edge spanning more
than `CYCLE_MAX_SPAN_DAYS`, so the loop is rejected. The younger teenager is flagged only because a
single school-trip transfer and its next-day partial refund are the only edges between that pair, so
the aggregated span is 0.8 days. The same family behaviour, and whether a loop fires depends on how
many unrelated payments happen to share the same account pair. Edge aggregation over a fixed window
is doing more work here than it should.

### The three that stayed clean

- **The shop settlement pair** (`SHOP_TRADE_71` / `SHOP_PAYOUT_72`) scores **37.7**, 2.3 under the
  line, and it is the closest thing to a weakness among the accounts that were not flagged. It
  satisfies *both* cycle constraints legitimately — a 1.60-day span at 55% value coherence — because
  month-end reconciliation genuinely resembles layering: the same float moved back and forth between
  two accounts in three days. It stays under only because two accounts share the back-office
  terminal rather than three, so `shared_identifiers` contributes at reduced strength. One more
  account on that terminal and this flags.
- **The friend group** (`FRND_R1`–`FRND_R5`) scores **25.0**. `tight_component` fires at full
  strength — 13 internal payment relationships over 5 nodes with no outside ties, structurally a
  small ring — but all 16 candidate cycles are rejected, because rent dominates the aggregated edges
  and wrecks value coherence. The flat-share lands on the watch list rather than the freeze list,
  which is where it belongs.
- **The bursty ticket merchant** (`MERCH_TICKETS_88`) scores **18.8**. Peak velocity 15, twice in
  the month, and still under half the bar: its pass-through ratio is 0.19 (it banks its takings) and
  it sits inside the 97-account main component rather than on an island. Velocity alone cannot flag
  anyone, which is the intended design.

### The margins

- **The legitimate hub still scores 0.0.** The highest-degree account on the platform, with more
  counterparties than any criminal in the data, produces no evidence at all. No account id appears
  anywhere in `src/` — nothing is special-cased.
- **The tightest true positive is `SMR_F11` at 40.9**, 0.9 above the line, along with the rest of the
  SMR_EXIT / F06–F11 cohort. These are the smurfing ring's periphery: they fire `tight_component`,
  `synchronised_onboarding` and `shared_identifiers`, but never sit on a closed loop and never
  collect. Three circumstantial signals just clear the bar.
- **`ACC_003` scores 37.7** and remains unflagged. It shares the household IP `197.210.44.10` with
  `ACC_004` *and* sits on a 2-account loop with `ACC_023` that closes in 0.77 days at 26% value
  coherence — barely inside both cycle constraints. Slightly different random draws and this becomes
  an eighth false positive.
- **`ATO_VICTIM` scores 44.8** and is flagged, on `velocity_burst`. That is correct rather than a
  mislabel: the victim of an account takeover is part of the incident, their account is compromised
  and being drained, and an investigator needs to see it. "Flagged" means "investigate", not
  "guilty".

Test suite: 63 tests, 96.3% coverage, `just all-hooks` green. The claims above are tests, not
sentences: the legitimate hub staying at zero, each hard-negative population's score, and the
precision and recall figures are all asserted in the suite.

### What I would change, and deliberately did not

The referral case points at a specific fix. **Structure alone should probably not be sufficient to
cross the threshold.** Every one of the seven false positives is carried entirely by structural and
circumstantial signals — cycles, component shape, shared devices, onboarding timing — with no
value-based or volume-based evidence behind any of them. Requiring at least one of `fan_in_fan_out`
or `velocity_burst` alongside the structural signals before an account can be flagged would clear
the whole referral cohort and the household pair, and would not weaken ring detection: every planted
ring has a collector, a drain or a burst somewhere in it.

That change is proposed, not implemented. Making it under time pressure would mean re-tuning the
weights and the threshold together against the same single generated ledger, on the last afternoon,
with no way to check whether the result generalises — which is the exact failure mode the
sensitivity analysis exists to detect. It belongs in a change with its own test data, not in this
one.

## Threshold sensitivity

`tna/sensitivity.py` re-evaluates the run at every cut-off from 0 to 100 and publishes the curve, so
the choice of 40.0 can be checked rather than taken on trust. `sweep()` returns one row per
threshold — flagged count, precision, recall, F1, false positives, false negatives — and `plateau()`
finds the widest band over which the metrics do not move. The block is mirrored into
`findings.json` under `sensitivity`.

Two independent arguments land on the same value.

**The full-recall band.** Every threshold from **0 to 40.9** catches all 32 planted accounts, because
the lowest-scoring fraudulent account is `SMR_F11` at 40.9. Anywhere inside that band, moving the
cut-off changes only how many innocent accounts come along; recall is unaffected. So the top of the
band is the only sensible place to sit, and 40 is a round number just inside it.

**The F1 maximum.** Over the swept grid, F1 peaks at threshold 40.0 with 0.901. Two different lines
of reasoning — "do not give up any recall" and "maximise the balance" — arrive at the same cut-off,
which is a better justification for a threshold than either on its own.

Two caveats, both worth stating plainly.

- On a finer sweep at 0.1 resolution, F1 peaks marginally higher at **40.8**: 0.928, 37 flagged, 5
  false positives. The threshold was deliberately **not** moved there. Tuning to the third decimal
  place on a single generated ledger is precisely the overfitting the sensitivity analysis exists to
  rule out, and a threshold that only works at 40.8 and not at 40.0 is a threshold fitted to this
  dataset's noise.
- Past 40.9 the drop is a **cliff, not a slope**. Nine accounts leave the flagged set at once,
  because the weaker members of the smurfing ring's periphery fire the same three circumstantial
  signals and therefore score alike. That is a property of the score distribution rather than of the
  threshold: there is no gentle trade-off available just above 40, only a step.

## How to read the output

The report at `public/index.html` (and at the live URL) is prerendered HTML — the findings, evidence
and methodology are real text in the document, not JavaScript-generated DOM, so it reads correctly
from a plain `curl` as well as in a browser.

**Ranked findings table.** One row per flagged account, highest risk first, with the score, the
band, the signals that fired, and the evidence sentence for each. Scores band as `critical` (≥ 70),
`high` (≥ 40, the flagging threshold), `elevated` (≥ 20) and `low` (below 20). Only `high` and
`critical` are flagged; `elevated` is the watch list, and reading it is how you judge whether the
threshold sits in the right place — `ACC_003`, the shop settlement pair at 37.7 and the friend group
at 25.0 all live there.

**Threshold sensitivity.** The curve of precision, recall and F1 against the cut-off, with the table
of flagged counts beside it and the full-recall band shaded. Read it left to right: recall is flat at
1.000 across the whole shaded band and falls off a cliff at its right-hand edge, while precision
climbs steadily as innocent accounts drop out. The chosen threshold is marked. The point of the
figure is that the marked line sits at the right-hand end of the shaded band rather than at a
convenient bump in the middle of the curve.

**Cluster view.** Findings grouped by connected component, with the internal value moved inside each
cluster. This is the level an investigator actually works at: a ring is a case, not thirty separate
alerts.

**Interactive graph.** The network with accounts as nodes coloured by risk band and payments as
directed edges. Useful for seeing the shape — ring A's overlapping loops, ring B's star into the
collector, ring C's fan out of the victim — next to the hub's wide, flat, unflagged neighbourhood.

**`network.png`.** A seeded spring-layout render of the same graph, reproducible and requiring no
JavaScript at all. This is the figure to paste into a document.

**`findings.json`.** The same findings in machine-readable form, for diffing between runs or
re-scoring under different weights.

## Stretch goals

**A — multi-feature enrichment: done.** Shared device and IP identifiers (`detect_shared_identifiers`)
and synchronised account creation (`detect_synchronised_onboarding`) both contribute evidence, at
deliberately low weights so that circumstantial links support a case without making one.

**Threshold sensitivity: done.** `tna/sensitivity.py` sweeps the cut-off across its whole range, the
report renders the curve and the table, and `findings.json` carries the numbers. This was listed as
the first thing to add in the previous version of this README; it is now the section above.

**Adversarial evaluation: done.** Seventeen innocent-but-suspicious-looking accounts, five
populations, each aimed at one detector. This is what turned a meaningless 1.000 into a precision
figure worth arguing about.

**C — temporal analysis: partially done.** Rolling 60-minute velocity windows are computed per
account and drive `velocity_burst`, and cycle detection is time-gated by `CYCLE_MAX_SPAN_DAYS`,
which is where the temporal dimension earns the most. Not done: temporal snapshots of the network at
several points in time, which would make ring formation visible rather than described.

**B — ML anomaly model: deliberately deferred.** An Isolation Forest or DBSCAN over the same graph
features would conflict with the explainability requirement that motivates the whole design, and at
140 accounts of synthetic traffic it would be learning the generator rather than fraud. The version
worth building is a *benchmark* — the model reported alongside the heuristic as a check that the
weights are not missing an obvious axis, not as a replacement for them. The reasoning is in
[`docs/DESIGN.md`](docs/DESIGN.md), which also records what was rejected on merit (hosted database,
Streamlit, FX normalisation, Neo4j) and why.

## Limitations and what I would do next

- The threshold is calibrated against one synthetic dataset, and the sensitivity sweep shows what
  that calibration is worth: 40.0 is defensible on this ledger by two independent arguments, and
  neither of them transfers to real traffic without being re-run on it.
- **The classes overlap.** The highest innocent account scores 59.4 and the lowest guilty one 40.9,
  so no cut-off on this score separates them and 0.821 precision is the ceiling for a pure threshold
  rule here. Fixing that means better features, not a better threshold — see the proposed
  structure-plus-value requirement above, and the referral attribute the platform already holds.
- Requiring a value- or volume-based signal alongside the structural ones is designed, argued and
  deliberately not implemented. It is the first thing I would build next, with its own test data
  rather than tuned against this one.
- Cycle detection depends on how payments happen to aggregate onto an edge. `HH_TEEN_02` escapes a
  flag and `HH_TEEN_03` does not, for the same family behaviour, because the elder teenager has more
  unrelated payments on the same account pair and the aggregated edge therefore spans more than five
  days. A per-transaction or sliding-window cycle search would not have this artefact.
- `shared_identifiers` groups on the sender side only, and treats every co-occurrence identically. A
  mobile IP or a shared cybercafé should be weighted differently from a device fingerprint; that
  needs identifier cardinality as an input.
- No FX normalisation, so cross-currency rings would be under-detected on value coherence. Correct
  at this scale, wrong at platform scale.
- Cycle enumeration is `nx.simple_cycles` with a length bound, which is fine for 140 nodes and will
  not be at a million. The aggregated view and the length bound are the right shape for scaling, but
  the search itself would need to become incremental.
- Ground truth exists only because the data is generated. On real traffic the feedback loop is
  confirmed-fraud labels arriving weeks later, which changes how the weights should be tuned.

## Further reading

- [`docs/DESIGN.md`](docs/DESIGN.md) — the design written before implementation: the problem as
  understood, what was optimised for, assumptions, module responsibilities, and everything not built
  with the reason for each.
- [`docs/AI_WORKFLOW.md`](docs/AI_WORKFLOW.md) — an account of where AI did the work, where it had
  to be overruled, and what was verified rather than trusted.
