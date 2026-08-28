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
and renders a prerendered HTML report with a ranked findings table, cluster breakdowns and an
interactive network view. Rule-based systems score transactions one at a time and so cannot see
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
than concentrated in bursts), and it sits inside the 91-account main component rather than an island.
High degree is the easiest signal to reach for and it is the wrong one.

**The conduit test.** `detect_fan_in_fan_out` does not fire on fan-in alone. It requires
`pass_through_ratio >= 0.7` — the money has to leave again. An account that receives from many
people and *keeps* the value is a popular seller; an account that receives from many people and
forwards nearly all of it through a handful of larger transfers is a collector. That one condition
is the entire difference between flagging a mule and flagging a merchant.

**Coincidental cycles.** The first version of `detect_circular_flows` flagged 40-odd innocent
background accounts. The reason is structural, not a bug: in a 91-node component carrying 340 random
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

With those two conditions the same detector fires on the planted rings and on nothing else.

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

32 accounts are labelled fraudulent. The generator plants layered short cycles rather than one long
loop for ring A, and returns value to the feeders in ring B, because that is what real laundering
looks like — value broken up and recombined so that no single hop looks unusual.

## Results and validation

Measured against planted ground truth in `tna/evaluate.py`, seed 42:

| | |
|---|---|
| Precision | 1.000 |
| Recall | 1.000 |
| F1 | 1.000 |
| Flagged | 32 |
| Actually fraudulent | 32 |
| False positives | 0 |
| False negatives | 0 |

**Perfect precision and recall on a dataset the author designed is not evidence that the detector
generalises.** The dataset was built to contain legible versions of known laundering typologies, so
a detector written against those typologies finding them is close to tautological. The threshold is
tuned to one synthetic dataset and would need recalibrating on real traffic, which is far messier
than this. The numbers worth reading are the ones about the margins.

- **The legitimate hub scores 0.0.** This is the result that means something. The highest-degree
  account on the platform, with more counterparties than any criminal in the data, produces no
  evidence at all. No account id appears anywhere in `src/` — nothing is special-cased.
- **The separation between the highest innocent account and the lowest guilty one is 3.2 points.**
  That is thin. It is the honest measure of how much headroom the threshold has, and it is not much.
- **The tightest true positive is `SMR_F11` at 40.9**, 0.9 above the line, along with the rest of the
  SMR_EXIT / F06–F11 cohort. These are the smurfing ring's periphery: they fire `tight_component`,
  `synchronised_onboarding` and `shared_identifiers`, but never sit on a closed loop and never
  collect. Three circumstantial signals just clear the bar.
- **The highest-scoring innocent account is `ACC_003` at 37.7**, 2.3 below the line. It shares the
  household IP `197.210.44.10` with `ACC_004` *and* sits on a 2-account loop with `ACC_023` that
  closes in 0.77 days at 26% value coherence — barely inside both cycle constraints. It stays
  unflagged only because neither structural detector fires on it. Slightly different random draws
  and this becomes a false positive.
- **`ATO_VICTIM` scores 44.8** and is flagged, on `velocity_burst`. That is correct rather than a
  mislabel: the victim of an account takeover is part of the incident, their account is compromised
  and being drained, and an investigator needs to see it. "Flagged" means "investigate", not
  "guilty".

Test suite: 41 tests, 84.41% coverage, `just all-hooks` green. The false-positive claim is a test,
not a sentence: the legitimate hub staying below the threshold is asserted in the suite, as are the
precision and recall figures above.

## How to read the output

The report at `public/index.html` (and at the live URL) is prerendered HTML — the findings, evidence
and methodology are real text in the document, not JavaScript-generated DOM, so it reads correctly
from a plain `curl` as well as in a browser.

**Ranked findings table.** One row per flagged account, highest risk first, with the score, the
band, the signals that fired, and the evidence sentence for each. Scores band as `critical` (≥ 70),
`high` (≥ 40, the flagging threshold), `elevated` (≥ 20) and `low` (below 20). Only `high` and
`critical` are flagged; `elevated` is the watch list, and reading it is how you judge whether the
threshold sits in the right place — `ACC_003` at 37.7 lives there.

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

**C — temporal analysis: partially done.** Rolling 60-minute velocity windows are computed per
account and drive `velocity_burst`, and cycle detection is time-gated by `CYCLE_MAX_SPAN_DAYS`,
which is where the temporal dimension earns the most. Not done: temporal snapshots of the network at
several points in time, which would make ring formation visible rather than described.

**B — ML anomaly model: deliberately deferred.** An Isolation Forest or DBSCAN over the same graph
features would conflict with the explainability requirement that motivates the whole design, and at
~123 accounts of synthetic traffic it would be learning the generator rather than fraud. The version
worth building is a *benchmark* — the model reported alongside the heuristic as a check that the
weights are not missing an obvious axis, not as a replacement for them. The reasoning is in
[`docs/DESIGN.md`](docs/DESIGN.md), which also records what was rejected on merit (hosted database,
Streamlit, FX normalisation, Neo4j) and why.

## Limitations and what I would do next

- The threshold is calibrated against one synthetic dataset. A threshold sensitivity sweep —
  precision and recall as the cut-off moves from 20 to 60 — is the honest way to justify 40.0, and
  is the first thing I would add.
- The 3.2-point separation between the highest innocent and lowest guilty account is too thin to
  survive messier data. On real traffic I would expect the circumstantial signals to fire far more
  often, and the fix is to require at least one structural signal before flagging rather than to
  raise the threshold.
- `shared_identifiers` groups on the sender side only, and treats every co-occurrence identically. A
  mobile IP or a shared cybercafé should be weighted differently from a device fingerprint; that
  needs identifier cardinality as an input.
- No FX normalisation, so cross-currency rings would be under-detected on value coherence. Correct
  at this scale, wrong at platform scale.
- Cycle enumeration is `nx.simple_cycles` with a length bound, which is fine for ~123 nodes and will
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
