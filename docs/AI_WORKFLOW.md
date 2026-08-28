# How this was built with AI

The brief asks for a two-hour build. This is an honest account of where AI did the work, where I
did, and where I had to overrule it.

## The setup

Claude Code (Opus) as the driver, with two background subagents running in parallel for the two
jobs that are self-contained and produce a lot of noisy output: tuning the detector thresholds
against the test suite, and writing the HTML report renderer. The main session kept the design, the
contracts between modules, and the decisions.

That split was deliberate. Threshold tuning is an iterate-until-green loop that generates hundreds
of lines of pytest output per attempt, and report rendering is a long single file with no
interesting decisions in it. Neither needs the design context, and both would have crowded out the
reasoning if run inline. The interfaces between modules — `Signal`, `AnalysisResult`,
`render_report` — were specified by hand *before* dispatching, so two agents could work on
different files simultaneously without coordinating.

## What AI was clearly good at

**Volume with a specification.** The synthetic data generator is the largest file in the repo and
took one pass. Given a precise description of the four structures to plant, generating them was
mechanical.

**Breadth of recall on the domain.** Smurfing, layering, pass-through ratios, the fan-in/fan-out
signature — having the vocabulary immediately available meant no clock time went to reading about
money laundering typologies.

**The tedious correctness work.** Rolling-window velocity arithmetic that is right at the window
boundary is exactly the kind of thing I would get subtly wrong by hand at speed, and exactly the
kind of thing a test pins down cheaply.

## Where it needed overruling

**It reached for the impressive tool over the right one.** The first instinct on "rank accounts by
fraud likelihood" is an anomaly-detection model — Isolation Forest, DBSCAN on graph features. That
would have been actively worse here: the rubric rewards logic that is "sound and explainable," and
a fraud analyst has to justify freezing someone's account to a human. A weighted sum of named
signals is less impressive and more correct. The ML model got demoted to a deferred idea, and only
survives as a *benchmark against* the heuristic rather than a replacement for it.

**It would have built the wrong architecture for the reader.** Streamlit is the obvious answer to
"interactive data tool," and it is the wrong answer when the page is fetched and read by an
automated reviewer before a human opens it, because a Streamlit page has no content until a
WebSocket handshake completes. Working that out required reasoning about who actually consumes the
artefact, which is not something the default suggestion accounted for.

**It optimised for true positives and ignored false positives.** Left alone, a detector built to
"find the fraud ring" flags the platform's busiest honest seller, because high degree is the
easiest signal to reach for. The legitimate hub in the test data exists specifically to make that
failure visible, and the conduit test in `detect_fan_in_fan_out` — does the money *leave* again? —
is what separates a popular merchant from a mule.

**It initially treated topology as evidence on its own.** Cycle detection over a marketplace graph
finds cycles everywhere: A buys from B, B buys from C, C happens to buy from A across three
unrelated weeks. Those are coincidences, not laundering. Constraining the detector to loops that
close quickly *and* preserve their value at each hop was the difference between a signal and noise.

## What I verified myself rather than trusting

- **Every number in the report.** The precision, recall and F1 figures are computed against planted
  ground truth in `tna/evaluate.py` and asserted in `tests/test_pipeline.py`, not written by hand.
- **The false-positive claim.** "The legitimate hub is not flagged" is a test, not a sentence.
- **The deployment.** The public URL was verified with plain `curl` from outside any authenticated
  session, because a submission behind an auth wall scores nothing regardless of its contents.
- **The fraud logic itself.** The weights encode a view about which structures imply coordination.
  Those are my judgements; AI wrote them down.

## What I would tell a team about this

AI moved the constraint. The bottleneck in two hours was never typing speed — it was deciding what
*not* to build, and noticing when a plausible-looking output was subtly wrong. Generating a
detector takes a minute; noticing that it flags your best customer takes domain judgement, and
that is the part worth a person's attention.
