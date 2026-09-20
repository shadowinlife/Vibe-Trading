"""Password hashing for the opt-in user-auth layer — stdlib ``hashlib.scrypt``.

Design notes (plan D2):

* ``scrypt(n=2**14, r=8, p=1, dklen=32)`` with a per-user 16-byte random salt.
  scrypt is memory-hard (~16 MiB per verify), which is the point: it resists
  GPU/ASIC bulk guessing far better than PBKDF2 at equal stdlib availability.
* Encoded format ``scrypt$16384$8$1$<salt_b64>$<hash_b64>`` carries the
  algorithm and parameters, so a future migration (e.g. argon2) can rehash on
  successful login without a flag day.
* Verification uses :func:`hmac.compare_digest` — constant time.
* Zero new dependencies: ``hashlib`` / ``hmac`` / ``secrets`` / ``base64`` are
  stdlib, and the project requires Python 3.11+.

⚠️ Cost note: each verify costs ~16 MiB and perceptible CPU, which makes the
(unauthenticated) login endpoint a CPU/memory amplifier. This round
deliberately ships no application-layer rate limiting (plan D11, user
decision); the deployment covers it with nginx ``limit_req_zone``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_ALGORITHM = "scrypt"
_N = 16384  # 2**14
_R = 8
_P = 1
_DKLEN = 32
_SALT_BYTES = 16
_ENCODED_FIELD_COUNT = 6


def _scrypt_smoke_check() -> None:
    """Fail loud at import time when ``hashlib.scrypt`` is unavailable.

    ``hashlib.scrypt`` depends on the OpenSSL backend and is missing from some
    platform builds (upstream CI includes Windows). Silently degrading to a
    weaker hash is never acceptable for password storage, so probe once with
    cheap parameters and raise with actionable guidance instead.
    """
    try:
        hashlib.scrypt(
            b"smoke", salt=b"0123456789abcdef", n=2, r=_R, p=_P, dklen=_DKLEN
        )
    except (AttributeError, ValueError) as exc:
        raise RuntimeError(
            "hashlib.scrypt is unavailable on this platform; the user-auth "
            "layer refuses to start rather than degrade to a weaker password "
            "hash. Rebuild Python against a modern OpenSSL, or port this "
            "module to an explicit pbkdf2_hmac fallback (plan D2)."
        ) from exc


_scrypt_smoke_check()


def hash_password(password: str) -> str:
    """Return the encoded scrypt hash of *password* with a fresh random salt.

    Args:
        password: The plaintext password. Callers bound the length (the HTTP
            layer enforces 8..128 characters) so scrypt input size stays sane.

    Returns:
        ``scrypt$16384$8$1$<salt_b64>$<hash_b64>`` — the only form persisted.
    """
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN
    )
    salt_b64 = base64.b64encode(salt).decode("ascii")
    digest_b64 = base64.b64encode(digest).decode("ascii")
    return f"{_ALGORITHM}${_N}${_R}${_P}${salt_b64}${digest_b64}"


def verify_password(password: str, encoded: str) -> bool:
    """Return whether *password* matches an encoded hash, in constant time.

    A malformed or foreign-format ``encoded`` value returns False rather than
    raising: on the login path a corrupt stored hash must fail the login, not
    500 the request. The stored parameters are honored (not the module
    defaults) so hashes produced under a future cost bump keep verifying.
    """
    try:
        fields = encoded.split("$")
        if len(fields) != _ENCODED_FIELD_COUNT or fields[0] != _ALGORITHM:
            return False
        n, r, p = int(fields[1]), int(fields[2]), int(fields[3])
        salt = base64.b64decode(fields[4], validate=True)
        expected = base64.b64decode(fields[5], validate=True)
    except (ValueError, TypeError):
        return False
    if n < 2 or r < 1 or p < 1 or not expected:
        return False
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=len(expected)
    )
    return hmac.compare_digest(digest, expected)
