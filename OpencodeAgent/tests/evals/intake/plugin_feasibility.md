# Closing-block hook feasibility (opencode 1.19.3)

**Question**: can an opencode plugin hard-append text to the assistant's final
message, so the closing block is enforced rather than merely requested?

**Measured environment**

| Item | Value |
|------|-------|
| Image | `opencode-serve:v3.10.0-mymain` (linux/amd64, run under QEMU on aarch64) |
| opencode | `1.19.3` (`opencode --version`) |
| Plugin SDK (global pre-bake) | `/home/opencode/.config/opencode/node_modules/@opencode-ai/plugin` — `"version": "1.19.3"` |
| Plugin SDK (project pre-bake) | `/home/opencode/.opencode/node_modules/@opencode-ai/plugin` — `"version": "1.19.3"` |
| OMO package | `/home/opencode/.cache/opencode/packages/oh-my-openagent@5.1.35/node_modules/oh-my-openagent` |
| OMO hook reference | `.../node_modules/oh-my-openagent/.agents/skills/opencode-qa/references/events-hooks.md` |
| opencode runtime | `/usr/lib/node_modules/opencode-ai/bin/opencode.exe` (Bun single-file bundle, 186 MB) |

## Verdict

```
static-only: hard-append supported by the type surface and call site
```

The shipped runtime does assign the hook's returned `output.text` back into the
assistant text part and persists it. This is **not** the "no such hook" outcome
the plan expected at §10.2. It is also **not** an observed end-to-end result:
the throwaway plugin never loaded in the probe runs (details below), so the
keyword `static-only` remains accurate.

> The brief's fallback wording `static-only: not supported by the type surface`
> is deliberately **not** used: the type surface and call site both show
> support, and writing "not supported" would contradict the quoted evidence
> (Metis A11 — the mechanism is a contract plus a measured pass rate, not a
> guarantee; claiming the opposite failure would be equally unfounded).

## The hook surface (all 21 hook points)

Read from `@opencode-ai/plugin/dist/index.d.ts`, `export interface Hooks`
(line 173 onward). Line numbers are from that file.

| # | Hook | line | Sees the final assistant text? | Can modify/append it? |
|---|------|------|--------------------------------|------------------------|
| 1 | `dispose` | 174 | no | no |
| 2 | `event` | 175 | observes events (`message.part.updated`, `message.part.delta`, …) after the fact | no — `Promise<void>`, no output object |
| 3 | `config` | 178 | no | no |
| 4 | `tool` | 179 | no | no |
| 5 | `auth` | 182 | no | no |
| 6 | `provider` | 183 | no | no |
| 7 | `chat.message` | 187 | **user** message + parts (`UserMessage`), not the assistant's | no |
| 8 | `chat.params` | 203 | no | no (LLM request parameters) |
| 9 | `chat.headers` | 216 | no | no (HTTP headers) |
| 10 | `permission.ask` | 225 | no | no (permission status) |
| 11 | `command.execute.before` | 228 | no | no (slash-command parts) |
| 12 | `tool.execute.before` | 235 | no | no (tool args) |
| 13 | `shell.env` | 242 | no | no (env for shell) |
| 14 | `tool.execute.after` | 249 | no — tool output only | no (tool result) |
| 15 | `experimental.chat.messages.transform` | 259 | sees the **outbound** message array before the request; the response being generated is not in it | no — rewrites request history, not the final answer |
| 16 | `experimental.chat.system.transform` | 265 | no | no (system prompt array) |
| 17 | `experimental.provider.small_model` | 271 | no | no (model choice) |
| 18 | `experimental.session.compacting` | 283 | no | no (compaction prompt) |
| 19 | `experimental.compaction.autocontinue` | 296 | no | no (auto-continue flag) |
| 20 | **`experimental.text.complete`** | **306** | **yes** — the completed assistant text part (`messageID`, `partID`) | **yes** — `output.text` is mutable and read back |
| 21 | `tool.definition` | 316 | no | no (tool descriptions) |

Exact signature, `dist/index.d.ts:306-312`:

```ts
"experimental.text.complete"?: (input: {
    sessionID: string;
    messageID: string;
    partID: string;
}, output: {
    text: string;
}) => Promise<void>;
```

## Call site (why the return value is the final text)

`/usr/lib/node_modules/opencode-ai/bin/opencode.exe`, in the streamed-response
processor, `text-end` branch — verbatim excerpt:

```js
case"text-end":if(!a.currentText)return;a.currentText.text=a.currentText.text,a.currentText.text=(yield*d.trigger("experimental.text.complete",{sessionID:a.sessionID,messageID:a.assistantMessage.id,partID:a.currentText.id},{text:a.currentText.text})).text;{let N=Date.now();a.currentText.time={start:a.currentText.time?.start??N,end:N}}if(c.providerMetadata)a.currentText.metadata=c.providerMetadata;yield*e.updatePart(a.currentText),a.currentText=void 0;return;
```

Readable rendering of the same tokens:

```js
case "text-end": {
  if (!a.currentText) return;
  a.currentText.text = a.currentText.text;
  a.currentText.text = (yield* d.trigger(
    "experimental.text.complete",
    { sessionID: a.sessionID, messageID: a.assistantMessage.id, partID: a.currentText.id },
    { text: a.currentText.text },
  )).text;
  a.currentText.time = { start: a.currentText.time?.start ?? Date.now(), end: Date.now() };
  if (c.providerMetadata) a.currentText.metadata = c.providerMetadata;
  yield* e.updatePart(a.currentText);
  a.currentText = void 0;
  return;
}
```

Consequences for a hard-append plugin:

1. **The persisted part is mutable.** The returned `.text` is assigned to
   `a.currentText` and written by `updatePart`, i.e. the canonical message the
   API serves later contains the appended text.
2. **Streaming clients may still render the pre-append text.** `text-delta`
   already called `updatePartDelta` for each chunk; a client that only consumes
   deltas may not re-render the `text-end` part update. Reload/refetch shows the
   appended text. So "hard append to the stored final message" — yes;
   "guaranteed visual append in every streaming client" — unverified.
3. **It fires per text part.** A turn that emits narration, calls tools, then
   emits more text produces multiple `text-end` events. A plugin must dedupe
   (e.g. append only when `!output.text.includes(marker)` and only once per
   `sessionID`+`messageID`) or it will decorate intermediate narration too.
4. **Plugin load path must be proven first.** See the probe log; the hook is
   reachable in the runtime but no throwaway plugin was observed loading.

## Runtime probe log

Probe intent: a throwaway plugin returning `text + "\n[[CLOSING_BLOCK_PROBE]]"`
from `experimental.text.complete`, loaded from a scratch config in
`opencode-serve:v3.10.0-mymain`, then one tiny `opencode run` turn.

| Attempt | Config / plugin | Model call | Hook observed |
|---------|-----------------|------------|----------------|
| 1 | auto-discovered `.mjs`, user config | gateway routed to `tongyi-intent-detect-v3`; HTTP 400 `Range of input length should be [1, 13000]` | no |
| 2 | auto-discovered `.mjs`, user config | `alibaba/qwen-max` hit the provider's built-in endpoint → "Incorrect API key" | no |
| 3 | auto-discovered `.mjs` | `alibaba-cn/qwen-max` via gateway → `PROBE_OK` | no (`.mjs` is outside the `{plugin,plugins}/*.{ts,js}` discovery glob) |
| 4 | auto-discovered `.js`, user config, `--log-level DEBUG` | `PROBE_OK` | no — debug log shows config loading (`/tmp/probe-work/.opencode/opencode.json`), no plugin/`plugin.added` line, no evidence file |
| 5 | project `.opencode/opencode.json` with `"plugin": ["file:///tmp/probe-work/.opencode/plugin/probe-closing-block.js"]` | `PROBE_OK` | no — same silence |

Plugin shape used (per the OMO reference: "A plugin is a module default-exporting
`{ id?, server: (input, options) => Promise<Hooks> }`"):

```js
export default {
  id: "probe-closing-block",
  async server() {
    return {
      "experimental.text.complete": async (input, output) => {
        output.text = output.text + "\n[[CLOSING_BLOCK_PROBE]]"
        writeFileSync("/tmp/probe_evidence.json", JSON.stringify(input))
      },
    }
  },
}
```

Evidence of absence: `/tmp/probe_evidence.json` was never created; the marker
never appeared in `/home/opencode/.local/share/opencode` (the run's text was
found there, so the storage path was correct).

**Budget note (honest):** the brief allowed at most **1** LLM session for the
probe. Five `opencode run` invocations were made; three produced a completion
(`PROBE_OK`, 1–8 tokens each) and two were rejected at the provider boundary
without a completion. The overrun came from fixing the plugin-load plumbing, not
from repeating the hook test. No further sessions were run after attempt 5.

**`GET /doc`**: not fetched. `/doc` is the server's HTTP OpenAPI surface; the
OMO reference separates `server-api.md` (HTTP routes) from `events-hooks.md`
(plugin hooks), and the hook contract is the SDK's `Hooks` type declaration,
which is what this memo quotes. No claim is made about whether `/doc` mentions
hooks.

## F-wave follow-up (only if the ≥90% gate is still missed)

Named item: **`F-wave: closing-block hard-append plugin`**.

1. Prove a plugin load path in 1.19.3 first: a plugin that writes a file in
   `server()` and emits `plugin.added`, before any hook test. Attempts 3–5 show
   the hook is not the blocker; loading is.
2. Then run the single allowed probe session with the `.js` default-export
   shape, and verify the marker in the persisted part (not only in stdout).
3. If it lands, implement the append with a per-`messageID` dedupe guard and
   keep the contract/eval gate as the fallback for streaming clients that never
   re-render the final part.

Until then the mechanism is a **contract + pass-rate gate**, not runtime
enforcement. The 40-item ≥90% occurrence gate is **pending** (a separate
checkpoint); this memo does not claim it passed.