# Learnings - novice-onboarding-gprime

Conventions, patterns, and successful approaches discovered during work on this plan.

_Auto-scaffolded by /ulw-execute. Append new entries below - never overwrite._

---

## todo 4 — first-message exporter (2026-10-28)

- **Where sessions actually live on this host**: `~/.local/share/opencode/opencode.db` (14 GiB, WAL mode, plus `opencode.db-wal` / `-shm`); legacy JSON storage also exists at `~/.local/share/opencode/storage/{session,message,part}/`. The `opencode-serve:v3.10.0-mymain` image has **no** `.db` under `/home/opencode/.local/share/opencode/` (empty dir), so image-side extraction is unavailable.
- **Discovered schema (opencode 1.19.x)**: `session_message` (`session_id`, `type`, `seq`, `data` JSON with `text`) + `session_v2` (`parent_id`); legacy `message` (`session_id`, `id`, `time_created`, `data` JSON with `role`) and `part` (`message_id`, `data` JSON with `type`/`text`). First user message = min `seq` for `session_message.type='user'`, else earliest `message.role='user'` plus its `part.type='text'` rows. `session` / `session_v2` `parent_id` separates root from child/subagent sessions.
- **Real counts**: 3,480 sessions (725 root), 123,136 messages, 564,684 parts. Root-only extraction found 726 first user messages; 586 classified `都不是` — this is a developer harness machine, not a novice-user corpus, and the aggregate notes say so.
- **Read-only proof**: `shutil.copy2` of the DB + WAL/SHM siblings into `tempfile.mkdtemp()`, `sqlite3.connect("file:"+copy+"?mode=ro", uri=True)`, `shutil.rmtree` in `finally`. The 14 GiB copy took ~20 s on APFS.
- **Scanner patterns**: CN mobile `1[3-9]\d{9}` with digit boundaries; 18-char ID shape `\d{17}[\dXx]`; email; money with suffix units (`元|万元|万|USD|CNY|¥|$`) and prefix `¥`/`$`; secrets `sk-…`, `ghp_…`, `Bearer …`, 32+ hex, PEM private-key header. The aggregate is scanned as serialized JSON and aborted on any hit.
