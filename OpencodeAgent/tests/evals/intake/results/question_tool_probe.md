# Question-tool probe — IS-3 evidence

Date: 2026-10-28. Image: `opencode-serve:v3.11.1-onboarding-20261028-71572199-fix1`
(`sha256:7b6c5d1b…` base rebuilt from commit `71572199`; local-only, no repo digest).

## Setup

A scratch compose project `wave1probe` ran the new image with a per-container
environment override, not an `.env` edit:

```yaml
environment:
  OPENCODE_ENABLE_QUESTION_TOOL: "1"
```

`docker inspect wave1-probe` confirms `OPENCODE_ENABLE_QUESTION_TOOL=1` is in
the container environment. One `opencode run` session was driven for corpus
item `c05` (`我的持仓怎么样`) through the same harness as the gate run:

```bash
python tests/evals/intake/run.py --corpus tests/evals/intake/corpus40.jsonl \
  --only c05 --container wave1-probe --attach http://localhost:4096 \
  --recovery-url http://127.0.0.1:4101 --dir /workspace --timeout 300 \
  --out tests/evals/intake/results/wave1_fix1_probe.jsonl
```

## Result

- Session id: `ses_e7ded160effe9987L0vSbpsAh0`
- Wall: 42.579 s; tokens: 58,338; route: `clarify_private`; intake loaded: true.
- Tool calls in the session: `skill {"name":"intake"}`, `bash`, `vibe-trading_memory_recall`.
- **A `question` tool call did not appear.** `question_called=false`, `question_count=0`.
- No rendered question payload exists; the agent produced a text-based
  clarification with numbered/bulleted options and then the four-line closing
  block.

## Why it still does not fire

The session object returned by `GET /session/{id}` carries:

```json
"permission": [
  {"permission": "question", "pattern": "*", "action": "deny"},
  {"permission": "plan_enter", "pattern": "*", "action": "deny"},
  {"permission": "plan_exit", "pattern": "*", "action": "deny"}
]
```

The non-interactive `opencode run` handler injects `question: deny` into every
session it creates, regardless of `OPENCODE_ENABLE_QUESTION_TOOL=1`. The flag is
visible in the container environment but does not reach the session permission
surface, so the tool is never exposed to the model. This is a handler/config
path limitation, not model reluctance: the agent asked the same three private
slots in text form.

**Conclusion:** the question tool remains unavailable on the headless path.
IS-3's runtime question capability is **not** proven; the text-option fallback
is what the delivered image actually uses.