# harness_bench eval environment notes

Isolated eval venv: `.venv-eval/` at the worktree root (git-ignored), created
with the legonanobot interpreter (`python -m venv .venv-eval`, Python 3.12.13).
Product dependency surface (`pyproject.toml`, `requirements*.txt`, `uv.lock`)
is untouched — everything below lives only in `.venv-eval/`.

Recreate:

```bash
python -m venv .venv-eval
.venv-eval/bin/pip install \
  "pydantic-ai==2.33.0" \
  "harbor==0.6.1" \
  "tau2 @ https://codeload.github.com/sierra-research/tau2-bench/tar.gz/fc0055dc4e0a316c3f83133267fbd6faaa770992"
```

`artifacts/eval_venv_freeze.txt` is the recorded `pip freeze` of the exact
environment this todo was verified with.

## Pins and why

### pydantic-ai==2.33.0

- Source: PyPI `pydantic-ai`, latest stable at pin time (uploaded 2026-08-21,
  verified via `https://pypi.org/pypi/pydantic-ai/json`).
- Plan reference: PoC base is PydanticAI v2 (draft decision D4); 2.33.0 is the
  current v2 line.

### tau2 @ codeload tarball of sierra-research/tau2-bench @ fc0055dc4e0a…770992

- The project's distribution name is `tau2` (its `pyproject.toml`, hatchling
  build backend), but it is **not published on PyPI**: the PyPI name `tau2`
  belongs to an unrelated project ("magnetic relaxation rates"). Git/tarball
  install is the only path, exactly as its README states (`uv sync` from a
  clone).
- Pinned commit `fc0055dc4e0a316c3f83133267fbd6faaa770992` is tag **v1.0.1**,
  the current stable release (verified via the GitHub tags API). v1.0.1 is
  chosen over older tags because the project declares results produced with
  < 1.0.1 not comparable with >= 1.0.1 (banking_knowledge grading fixes), and
  it still ships the `retail`/`airline` domains the suite uses.
- Install URL is the immutable codeload tarball of that commit instead of
  `git+https` because this network blocks the git protocol (HTTP2 framing
  errors / connection refused on github.com:443 while plain HTTPS works); the
  tarball of a full SHA is equally immutable and `pip freeze` records the URL.
  Tarball sha256 at download time:
  `7a227036d07fdeb088dbd0c99fdb40ee9b9e0ebc06aadbc4dec1217b3383e57e`.
- Requires Python >=3.12,<3.14 — matches the 3.12.13 venv.

### harbor==0.6.1

- Source: PyPI `harbor` (harborframework.com, the official Terminal-Bench-2.0
  harness from the terminal-bench creators; verified author/description via
  `https://pypi.org/pypi/harbor/json`).
- **Why not 0.22.0 (latest):** single-venv coexistence. tau2 1.0.1 requires
  `litellm>=1.80.15,<1.82.7`; harbor >= 0.6.2 requires `litellm>=1.83.14`
  (0.21+ requires >=1.92.0) — a hard resolver conflict. harbor **0.6.1**
  (2026-04-30) is the newest release whose `litellm>=1.80.8` floor overlaps
  tau2's window; with `litellm==1.82.6` all three packages coexist in one venv.
- harbor 0.6.1 already targets Terminal-Bench-2.0 (harbor has been its
  official runner since 0.1.x, 2025-11). If a later todo needs newer harbor
  features, the clean exit is a second tool venv — recorded as a deviation
  then, not silently taken now.
- pip provides the `harbor` CLI/framework; actual task execution additionally
  requires Docker, which the preflight verifies separately.

## Environment variables used (names only — values never committed)

- `DASHSCOPE_API_KEY` — DashScope key for the parity model; the preflight
  falls back to the local opencode auth store
  (`~/.local/share/opencode/auth.json`, provider `alibaba-cn`) when unset.
- `DASHSCOPE_BASE_URL` — defaults to
  `https://dashscope.aliyuncs.com/compatible-mode/v1` (see parity_spec.json).
- `HF_ENDPOINT` — HuggingFace mirror override; preflight records the decision
  when only a mirror is reachable.
- `HARNESS_BENCH_PYTHON` — interpreter for `scripts/preflight.sh`.
- `HARNESS_BENCH_OPENCODE_URL` / `HARNESS_BENCH_OPENCODE_PASSWORD` /
  `HARNESS_BENCH_OPENCODE_IMAGE` — probe a pre-existing opencode-serve instead
  of starting an ephemeral container.
- `HARNESS_BENCH_INJECT_FAILURE` / `HARNESS_BENCH_POISON_HOSTS` /
  `HARNESS_BENCH_SKIP_OPENCODE` — test/failure-path injection seams.

## Known network constraint

github.com git protocol is blocked on this host (curl HTTPS works, git clone
fails with HTTP2 framing errors). Anything git-pinned in this suite uses
codeload tarballs of exact commit SHAs instead.
