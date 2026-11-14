# Strategy version source contract

> Contract version: `strategy-version-source-v1`
> Status: local release candidate. Production delivery and named human verification are separate gates.

## Purpose

`POST /strategy-version-source` returns the exact `code/signal_engine.py` bytes for one owner-scoped `strategy_id + strategy_version`. It does not resolve latest/current versions, workspace paths, Git HEAD, or branch names. It does not run a backtest or change the execution-control contract.

The route returns the contract version, strategy and version labels, opaque persistent `version_id`, owner and caller scope, fixed source path and encoding, exact byte length, standard base64 source bytes, and lowercase SHA-256. Successful responses contain exactly those eleven fields. Errors never include source bytes, byte length, a replacement digest, or a version list.

## Authentication and deployment owner binding

The dedicated strategy-run entry authenticates the request with the same service token used by the strategy-run routes. The expected token is read lazily from `RM_VT_STRATEGY_RUN_TOKEN_FILE`; only trailing CR/LF characters are removed. Browser session credentials are not accepted.

After service authentication and before any strategy lookup, the route resolves the deployment owner from `RM_VT_STRATEGY_OWNER_BINDING_FILE`. The request body and headers cannot select or assert an owner.

The binding file must be:

- a regular, non-symlink file owned by the `opencode` process user;
- mode exactly `0600`;
- a UTF-8 JSON object with no extra fields:

```json
{
  "schema_version": "strategy-owner-binding-v1",
  "owner_user_id": "synthetic-owner-example",
  "token_sha256": "<lowercase SHA-256 of the normalized service token>"
}
```

The token digest binds the owner document to the same service credential. A valid token with a missing, malformed, mismatched, or unsafe binding receives `403 {"detail":"forbidden"}` before body parsing or strategy lookup. The token, token digest, binding path, and real user source must not enter logs.

A shared sandbox template may configure only the binding file path. Each per-user sandbox must write its own binding from the actual provisioned user. A static shared owner document is not a valid multi-tenant deployment.

## Version registry and lineage

Reads require an active persistent registration for `(owner_user_id, strategy_id, strategy_version)`. The registry stores the opaque random `version_id`, fixed source path, recorded source SHA-256 and byte length, and `pending` / `active` / `tombstoned` state.

Publishing persists a pending identity, writes exact source bytes atomically, verifies them, and then activates the registration. Incomplete publications are unreadable. Recovery may continue only with the original identity and recorded bytes. Deletion creates a terminal tombstone; the same label and identity cannot be reused.

Legacy directories and manifests without trusted registration are not readable. There is no production backfill or lazy migration. State export/import preserves owners, registrations, digests, and tombstones, rejects conflicts before installation, and keeps imported foreign-owner data invisible to the bound owner.

## Response and error behavior

- Another owner's strategy and a nonexistent strategy return the same `404 strategy_not_found` body, including pending and tombstoned foreign versions.
- An owned strategy's unknown, pending, unregistered, or tombstoned version returns `404 version_not_found`.
- An expected digest mismatch or registry/manifest/actual-byte drift returns `409 input_digest_mismatch` without source payload.
- A registered active identity without provable version files or lineage returns `404 version_not_found`.
- `config.json` is generated and frozen by RM. VT consumes it during prepare but does not generate or rewrite it. The backtest executor is unchanged.

## Verification boundary

The reviewed local implementation is covered by exact-byte HTTP tests, restart and tombstone tests, crash injection, state round trips, and the selected strategy/backtest regression suite. Local evidence is not an image digest, a deployment record, or named human acceptance. Record those coordinates separately after the immutable image and deployed checks complete.
