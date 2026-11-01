# Research-discovery experimental evaluation

This is a separate, hand-authored synthetic multi-turn corpus. It contains no
customer records or real account data. Legacy intake v1 corpora, snapshots,
results, checks and A/B verdicts remain unchanged.

Compare the current artifact-first control with explicit research-discovery
activation using the **same model, image, tool permissions and data fixtures**.
The progressive arm explores decisions that affect the route. The deep arm is
used only when the first user message requests deep exploration. Replay each
case's actual user replies; do not collapse them into a single prompt.

## Pre-registered outcomes

- Primary: blind reviewers find the final research goal and usable artifact
  consistent with the user's final request. A menu/brief or tool call is not an artifact.
- Mechanical: `checks.check_record` requires recorded user turns, revised target,
  route, preserved unknowns, a real artifact file with SHA-256, cited observed
  result payloads with matching digests, and actual data-as-of.
- Human review: appropriateness of questions, whether the answer changes the
  plan, distinction between routes, factual usefulness, unwanted assumptions,
  repeated questions, abandonment and appropriateness of defaults.
- Resource: measured tool/model calls, provider-reported tokens and wall time,
  including failed runs. Missing usage is unknown, never zero.
- Safety/contract gates: zero unauthorized expensive starts, fake confirmations,
  stale-revision approvals, cross-session goal mixing, or illegal tool arguments
  in fault-injection tests. No live accounts, trading or external credentials.

The twelve cases are coverage fixtures, **not a powered efficacy trial**.
No superiority claim below 30 eligible pairs; pre-register a larger sample from
pilot discordance before drawing a directional conclusion. Use exact paired
McNemar on the binary human-reviewed outcome and report effect intervals,
failures and per-market strata. A nonsignificant difference does not establish
noninferiority. Do not enable default behavior without a separate noninferiority
margin and adequate sample size agreed before the trial.

## Offline checks

```bash
conda run -n legonanobot python -m pytest OpencodeAgent/tests/test_research_discovery.py -q
```

These exercise schema capture and negative fixtures without invoking finance
tools, an LLM or a broker. A passing offline suite is not evidence that a model
asks useful questions. Real model replay requires explicit cost authorization;
write sanitized records/artifacts outside tracked files and review them blindly.
