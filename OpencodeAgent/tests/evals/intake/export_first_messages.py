"""Read-only exporter for the first user message of each opencode session.

Why: the intake eval needs a request-label distribution, but raw messages are
private. Stores are copied to a temp directory, opened read-only, reduced to
counts, and raw text goes only outside the repo (default ``$TMPDIR``). The
aggregate is PII-scanned before it is written; an unknown schema yields
``source="unavailable"`` rather than a guess. The label rubric is a fixed
priority list, so specific actions resolve before the generic ``报告``.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

PRODUCT_LABELS = (
    "个股体检", "选股清单", "策略回测", "组合体检",
    "事件/宏观影响", "交易复盘", "定时监控", "报告",
)
ALL_LABELS = PRODUCT_LABELS + ("都不是",)
LENGTH_BUCKETS = ("<=10", "11-30", "31-80", ">80")
SCHEMA_VERSION, DEFAULT_SYNTHETIC_TOTAL, DEFAULT_SEED = 1, 40, 20261027

# Priority order matters: the first label with any keyword hit wins.
LABEL_KEYWORDS = (
    ("定时监控", "定时 监控 每天 每日 提醒 预警 盘前 盘后 cron schedule"),
    ("交易复盘", "复盘 交割单 交易记录 成交记录 盈亏 清仓 割肉"),
    ("策略回测", "回测 策略 因子 夏普 胜率 回撤 walk-forward 参数"),
    ("事件/宏观影响", "宏观 事件 战争 地缘 加息 降息 美联储 政策 俄乌"),
    ("组合体检", "组合 持仓 仓位 账户 portfolio 解套"),
    ("选股清单", "选股 筛选 清单 候选 股票池 排行 机会 推荐"),
    ("个股体检", "个股 体检 诊断 这只股 这只票 单只 估值 基本面"),
    ("报告", "报告 html 汇总 总结 导出"),
)

# Broad on purpose: a false positive aborts the aggregate write.
PII_PATTERNS = {
    "cn_mobile": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "cn_id": re.compile(r"(?<!\d)\d{17}[\dXx](?![\dXx])"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "money": re.compile(
        r"(?:\d[\d,]*\.?\d*\s*(?:元|万元|万|USD|CNY|¥|\$))|(?:[¥$]\s*\d[\d,]*\.?\d*)"
    ),
    "secret": re.compile(
        r"(?:sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|Bearer\s+\S+"
        r"|[A-Fa-f0-9]{32,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
    ),
}

SYNTHETIC_MARKETS = ("A股", "沪深300", "中证500", "创业板", "科创板", "港股", "美股", "ETF")
SYNTHETIC_STYLES = ("价值", "成长", "动量", "红利", "低波", "质量", "反转", "小盘")
SYNTHETIC_HORIZONS = ("短线", "中线", "长线", "近一周", "近一月", "近一年", "三年", "跨周期")
SYNTHETIC_ACTIONS = (
    ("个股体检", "帮我体检一下{market}里{style}风格的标的"),
    ("选股清单", "按{style}风格筛选一份{market}选股清单"),
    ("策略回测", "回测一个{market}{style}{horizon}策略"),
    ("组合体检", "看看我的{market}组合在{horizon}的表现"),
    ("事件/宏观影响", "{market}最近的宏观事件会怎么影响{style}板块"),
    ("交易复盘", "复盘一下我{horizon}在{market}的{style}交易"),
    ("定时监控", "每天盘后监控{market}的{style}信号并提醒我"),
    ("报告", "生成一份{market}{style}{horizon}分析报告"),
)


def classify(text: str) -> str:
    lowered = text.lower()
    for label, words in LABEL_KEYWORDS:
        if any(word in lowered for word in words.split()):
            return label
    return "都不是"


def length_bucket(text: str) -> str:
    size = len(text)
    if size <= 10:
        return "<=10"
    if size <= 30:
        return "11-30"
    if size <= 80:
        return "31-80"
    return ">80"


def scan_text(text: str) -> list[str]:
    return [name for name, pattern in PII_PATTERNS.items() if pattern.search(text)]


def generate_synthetic(count: int, seed: int = DEFAULT_SEED) -> list[dict]:
    rng = random.Random(seed)
    messages = []
    for index in range(count):
        label, template = SYNTHETIC_ACTIONS[index % len(SYNTHETIC_ACTIONS)]
        text = template.format(
            market=rng.choice(SYNTHETIC_MARKETS),
            style=rng.choice(SYNTHETIC_STYLES),
            horizon=rng.choice(SYNTHETIC_HORIZONS),
        )
        messages.append({"source": "synthetic", "text": text, "expected_label": label})
    return messages


def _loads(raw: str):
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _first_user_rows(conn: sqlite3.Connection):
    """Yield ``(session_id, message_id, time_created, data)`` for user messages."""
    yield from conn.execute(
        "SELECT session_id, id, time_created, data FROM message "
        "WHERE json_extract(data, '$.role') = 'user' "
        "ORDER BY session_id, time_created, id"
    )


def _extract_legacy(conn: sqlite3.Connection, sessions: dict[str, str]) -> None:
    first: dict[str, str] = {}
    for session_id, message_id, _, raw in _first_user_rows(conn):
        if session_id in sessions or session_id in first:
            continue
        payload = _loads(raw)
        if isinstance(payload, dict) and payload.get("role") == "user":
            first[session_id] = message_id
    if not first:
        return

    texts: dict[str, list[str]] = {}
    message_ids = list(first.values())
    for start in range(0, len(message_ids), 400):
        chunk = message_ids[start : start + 400]
        marks = ",".join("?" for _ in chunk)
        sql = (
            "SELECT message_id, data FROM part "
            f"WHERE message_id IN ({marks}) ORDER BY message_id, time_created, id"
        )
        for message_id, raw in conn.execute(sql, chunk):
            payload = _loads(raw)
            if isinstance(payload, dict) and payload.get("type") == "text":
                text = payload.get("text")
                if isinstance(text, str) and text.strip():
                    texts.setdefault(message_id, []).append(text.strip())

    for session_id, message_id in first.items():
        joined = "\n".join(texts.get(message_id, []))
        if joined:
            sessions[session_id] = joined


def _extract_new(conn: sqlite3.Connection, tables: set[str], sessions: dict[str, str]) -> None:
    if "session_message" not in tables:
        return
    columns = _columns(conn, "session_message")
    if not {"session_id", "type", "data"} <= columns:
        return
    order = "seq" if "seq" in columns else "rowid"
    sql = (
        "SELECT session_id, data FROM ("
        "SELECT session_id, data, ROW_NUMBER() OVER "
        f"(PARTITION BY session_id ORDER BY {order}) AS rn "
        "FROM session_message WHERE type = 'user') WHERE rn = 1"
    )
    try:
        for session_id, raw in conn.execute(sql):
            payload = _loads(raw)
            text = payload.get("text") if isinstance(payload, dict) else None
            if isinstance(text, str) and text.strip():
                sessions[session_id] = text
    except sqlite3.DatabaseError:
        pass


def extract_first_messages(conn: sqlite3.Connection) -> dict[str, str]:
    """Return ``{session_id: first user message}`` using whichever schema exists."""
    tables = _tables(conn)
    sessions: dict[str, str] = {}
    _extract_new(conn, tables, sessions)
    if "message" in tables and "part" in tables:
        message_columns = _columns(conn, "message")
        part_columns = _columns(conn, "part")
        if {"session_id", "id", "data"} <= message_columns and {"message_id", "data"} <= part_columns:
            _extract_legacy(conn, sessions)
    return sessions


def root_session_ids(conn: sqlite3.Connection) -> set[str]:
    tables = _tables(conn)
    roots: set[str] = set()
    for table in ("session", "session_v2"):
        columns = _columns(conn, table) if table in tables else set()
        if "id" not in columns:
            continue
        parent = "parent_id" if "parent_id" in columns else None
        sql = f"SELECT id, {parent} FROM {table}" if parent else f"SELECT id, NULL FROM {table}"
        for session_id, parent_id in conn.execute(sql):
            roots = roots | {session_id} if parent_id is None else roots - {session_id}
    return roots


def discover_dbs() -> list[Path]:
    roots = [Path.home() / ".local" / "share" / "opencode"]
    if os.environ.get("XDG_DATA_HOME"):
        roots.insert(0, Path(os.environ["XDG_DATA_HOME"]).expanduser() / "opencode")
    if os.environ.get("OPENCODE_DATA_HOME"):
        roots.insert(0, Path(os.environ["OPENCODE_DATA_HOME"]).expanduser())

    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        if (root / "opencode.db").is_file():
            found.append(root / "opencode.db")
        if (root / "storage").is_dir():
            found.extend(sorted((root / "storage").rglob("*.db")))
    return list(dict.fromkeys(path.resolve() for path in found))


def _copy_for_read(db_path: Path, workdir: str, index: int) -> Path:
    """Copy a store (plus WAL/SHM siblings) before any handle is opened."""
    target_dir = Path(workdir) / f"store_{index}"
    target_dir.mkdir()
    target = target_dir / db_path.name
    shutil.copy2(db_path, target)
    for suffix in ("-wal", "-shm"):
        sibling = db_path.with_name(db_path.name + suffix)
        if sibling.is_file():
            shutil.copy2(sibling, target.with_name(target.name + suffix))
    return target


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists():
            return parent
    return here.parents[4] if len(here.parents) > 4 else here.parent


def _refuse_repo_path(path: Path) -> None:
    resolved = path.expanduser().resolve()
    root = _repo_root()
    if resolved == root or root in resolved.parents:
        raise SystemExit(
            f"refusing --raw-out inside the repository: {resolved}; "
            "choose a path under $TMPDIR or $HOME"
        )


def _default_raw_out() -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return Path(tempfile.gettempdir()) / f"onboarding_first_messages_{stamp}.jsonl"


def _build_aggregate(real: list[dict], synthetic: list[dict], source: str, notes: str) -> dict:
    distribution = {label: 0 for label in ALL_LABELS}
    buckets = {bucket: 0 for bucket in LENGTH_BUCKETS}
    for message in real + synthetic:
        distribution[classify(message["text"])] += 1
        buckets[length_bucket(message["text"])] += 1
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": source,
        "source_mix": {"real": len(real), "synthetic": len(synthetic)},
        "n_total": len(real) + len(synthetic),
        "label_distribution": distribution,
        "length_buckets": buckets,
        "notes": notes,
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", help="explicit SQLite store; auto-detected when omitted")
    parser.add_argument("--raw-out", help="raw JSONL path outside the repo (default: $TMPDIR)")
    parser.add_argument("--out", help="aggregate path (default: results/request_labels.json)")
    parser.add_argument("--synthetic", type=int, default=None, help="N synthetic messages")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--root-only", action="store_true", help="exclude child sessions")
    return parser.parse_args(argv)


def _read_store(db_path: Path, workdir: str, index: int, root_only: bool, reasons: list[str]):
    if not db_path.is_file():
        reasons.append(f"{db_path.name}: missing")
        return {}
    try:
        copy = _copy_for_read(db_path, workdir, index)
        conn = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
    except (OSError, sqlite3.DatabaseError) as exc:
        reasons.append(f"{db_path.name}: unreadable ({exc.__class__.__name__})")
        return {}
    try:
        extracted = extract_first_messages(conn)
        if root_only:
            roots = root_session_ids(conn)
            extracted = {sid: text for sid, text in extracted.items() if sid in roots}
        reasons.append(f"{db_path.name}: {len(extracted)} first user messages")
        return extracted
    except sqlite3.DatabaseError as exc:
        reasons.append(f"{db_path.name}: read failed ({exc.__class__.__name__})")
        return {}
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    raw_out = Path(args.raw_out).expanduser() if args.raw_out else _default_raw_out()
    _refuse_repo_path(raw_out)
    default_out = Path(__file__).resolve().parent / "results" / "request_labels.json"
    out = Path(args.out).expanduser() if args.out else default_out

    candidates = [Path(args.db).expanduser()] if args.db else discover_dbs()
    real: dict[str, dict] = {}
    reasons: list[str] = []
    workdir = tempfile.mkdtemp(prefix="opencode_first_msg_")
    try:
        for index, db_path in enumerate(candidates):
            for session_id, text in _read_store(
                db_path, workdir, index, args.root_only, reasons
            ).items():
                real.setdefault(session_id, {"source": "real", "text": text})
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    if args.synthetic is None:
        synthetic_count = max(0, DEFAULT_SYNTHETIC_TOTAL - len(real))
    else:
        synthetic_count = max(0, args.synthetic)
    synthetic = generate_synthetic(synthetic_count, args.seed)

    if not candidates:
        notes = "no opencode session database found on this host; synthetic only"
    elif real:
        notes = "read a temporary read-only copy; " + "; ".join(reasons)
        notes += "; local store is a developer harness machine, not a novice-user corpus"
        if args.root_only:
            notes += "; root sessions only (child/subagent sessions excluded)"
        notes += "; raw text stayed outside the repository"
    else:
        notes = "opencode store found but no first user message could be extracted; "
        notes += "; ".join(reasons)
    if synthetic_count:
        notes += f"; {synthetic_count} synthetic messages generated from slot vocabularies"
        if real:
            notes += " (behaviour-test volume only)"

    payload = _build_aggregate(
        [{"source": item["source"], "text": item["text"]} for item in real.values()],
        synthetic,
        "local_store" if real else "unavailable",
        notes,
    )
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    hits = scan_text(rendered)
    if hits:
        raise SystemExit(
            "refusing to write aggregate: PII/secret scan fired " f"({', '.join(hits)})"
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, encoding="utf-8")

    rows = [
        {"session_id": sid, "source": item["source"], "text": item["text"]}
        for sid, item in real.items()
    ]
    rows += [
        {"session_id": None, "source": item["source"], "text": item["text"]}
        for item in synthetic
    ]
    raw_out.parent.mkdir(parents=True, exist_ok=True)
    raw_out.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    print(f"raw export (contains raw text; delete after use): {raw_out}")
    print(f"aggregate: {out} (real={len(real)}, synthetic={synthetic_count})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
