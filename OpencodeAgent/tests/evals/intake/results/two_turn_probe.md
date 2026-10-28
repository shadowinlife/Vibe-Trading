# Two-turn clarification probe (IS-1 / IS-3 evidence)

Date: 2026-10-28. Image: `opencode-serve:v3.11.1-onboarding-20261028-71572199-fix1`.
Lane: compose project `wave1probe-a`, container `wave1probe-a`, host port 4097.

## Setup

Corpus item `c05` (`我的持仓怎么样`, `clarify_private` / 组合体检) was driven by the
resident harness (`results/wave1_probe_a.jsonl`). After that run finished, the
same session was continued — no new session — with the user's answers:

```bash
docker exec -w /workspace wave1probe-a opencode run --attach http://localhost:4096 \
  --dir /workspace --format json --session ses_e7d0938b5ffezQWuSkvB9tOqkA "1A 2B 3C"
```

Turn 2 exited 0 after 179 s. The authoritative transcript was read from
`GET /session/{id}/message` (25 messages, 2 user messages).

## Turn 1 — the clarification

- Questions: **3** (`体检哪一组持仓？`, `成本价怎么算？`, `仓位占比？`), i.e. within the
  ≤3-question budget.
- Options: **3 per question** (A/B/C), numbered `1`/`2`/`3` with bold letters.
- Escape hatch: **present** — each question has a "以上都不是 / 未知" option, and
  the turn ends with `如果你不想逐项回答，可以直接回复 默认，我就按 1A + 2B + 3C 直接运行`.
- `question` tool calls: **0**. The headless `opencode run` handler injects
  `question: deny` into the session (same finding as `results/question_tool_probe.md`),
  so the questions are text options, not a rendered question tool.

## Turn 2 — the continuation

- Session id: `ses_e7d0938b5ffezQWuSkvB9tOqkA`
- User turns in the session: **2** (initial prompt + `"1A 2B 3C"`); the answer was
  accepted and the agent did not ask again.
- Artifact: **yes** — `/workspace/analysis/portfolio_2026-10-28/report.md`
  (12,432 bytes, 171 lines) was written in the container; `analysis/_index.json`
  was updated. The reply also carries the full 组合体检 artifact inline.
- Closing block: `checks.find_closing_block()` on the final assistant message is
  `{"assumptions": true, "alternative": true, "next_steps": true,
  "disclaimer": true, "complete": true, "missing": []}`.
- Final assistant message tokens: 99,220 (cumulative session total; turn-2 output
  1,617 + reasoning 92).
- Raw tool names, turn 2: `skill`, `vibe-trading_ch_query`, `bash`,
  `vibe-trading_quantlib_call`, `write`. Full session order:
  `skill, bash` (turn 1) then the second list.

## Verdict

Continuation is **supported on this harness**: turn 1 asked three private-slot
questions with an escape hatch, turn 2 produced the named artifact with all four
closing lines, using exactly two user turns.

<!-- two-turn:json
{"session_id": "ses_e7d0938b5ffezQWuSkvB9tOqkA", "turn1_questions": 3, "turn1_options_per_question": 3, "turn1_escape_hatch": true, "turn2_wall_s": 179, "user_turns": 2, "artifact_path": "/workspace/analysis/portfolio_2026-10-28/report.md", "artifact_bytes": 12432, "artifact_lines": 171, "closing_complete": true, "missing": [], "turn2_tools": ["skill", "vibe-trading_ch_query", "bash", "vibe-trading_quantlib_call", "write"], "final_tokens": 99220, "continuation_supported": true}
-->