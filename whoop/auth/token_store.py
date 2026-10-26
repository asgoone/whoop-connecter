"""
Encrypted token storage. Tokens are AES-256-GCM encrypted on disk.

Guarantees:
- writes are atomic (temp file in the same dir, 0600 from creation, fsync, replace);
- a file that cannot be read or decrypted is never deleted;
- lock() gives an exclusive inter-process lock for read-refresh-save sequences.

Only one host may own the token file: a local flock does not protect against
another machine refreshing the same refresh token.
"""

import contextlib
import fcntl
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from whoop.errors import ErrorCode, WhoopError

logger = logging.getLogger(__name__)

_NONCE_SIZE = 12


@dataclass
class TokenData:
    access_token: str
    refresh_token: str
    expires_at: float  # Unix timestamp
    scope: str | None = None  # space-separated scopes granted by WHOOP


class TokenStore:
    _KEY_ENV = "WHOOP_TOKEN_ENCRYPTION_KEY"

    def __init__(self, token_path: str, lock_timeout: float = 60.0) -> None:
        self._path = Path(token_path).expanduser()
        self._lock_path = self._path.with_name(self._path.name + ".lock")
        self._lock_timeout = lock_timeout
        self._key = self._load_key()

    @property
    def path(self) -> Path:
        return self._path

    def _load_key(self) -> bytes:
        raw = os.environ.get(self._KEY_ENV)
        if not raw:
            raise EnvironmentError(
                f"Environment variable {self._KEY_ENV} is not set. "
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        key = bytes.fromhex(raw)
        if len(key) != 32:
            raise ValueError(f"{self._KEY_ENV} must be a 64-character hex string (32 bytes)")
        return key

    # ------------------------------------------------------------------
    # Locking
    # ------------------------------------------------------------------

    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        """Exclusive inter-process lock (flock on a sibling .lock file)."""
        try:
            self._ensure_dir()
            fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as exc:
            raise WhoopError(
                ErrorCode.TOKEN_STORE_ERROR, f"Cannot open token lock file: {exc.strerror}"
            ) from None
        try:
            deadline = time.monotonic() + self._lock_timeout
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise WhoopError(
                            ErrorCode.TRANSIENT_ERROR,
                            f"Timed out after {self._lock_timeout:.0f}s waiting for token lock",
                        )
                    time.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    # ------------------------------------------------------------------
    # Read / write
    # ------------------------------------------------------------------

    def save(self, tokens: TokenData) -> None:
        """Atomically replace the token file. Raises WhoopError(TOKEN_STORE_ERROR)."""
        try:
            self._write(tokens)
        except OSError as exc:
            raise WhoopError(
                ErrorCode.TOKEN_STORE_ERROR, f"Cannot write token file: {exc.strerror}"
            ) from None
        logger.debug("Tokens saved to %s", self._path)

    def _write(self, tokens: TokenData) -> None:
        self._ensure_dir()

        payload = {
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token,
            "expires_at": tokens.expires_at,
        }
        if tokens.scope is not None:
            payload["scope"] = tokens.scope

        nonce = os.urandom(_NONCE_SIZE)
        ciphertext = AESGCM(self._key).encrypt(nonce, json.dumps(payload).encode(), None)

        tmp_path = self._path.with_name(f".{self._path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(nonce + ciphertext)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self._path)
        except BaseException:
            with contextlib.suppress(OSError):
                tmp_path.unlink()
            raise
        self._fsync_dir()

    def load(self) -> TokenData | None:
        """Return stored tokens, None if no file. Never deletes the file.

        Raises WhoopError(TOKEN_STORE_ERROR) if the file exists but is unusable.
        """
        try:
            data = self._path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise WhoopError(
                ErrorCode.TOKEN_STORE_ERROR, f"Cannot read token file: {exc.strerror}"
            ) from None

        if len(data) <= _NONCE_SIZE:
            raise WhoopError(ErrorCode.TOKEN_STORE_ERROR, "Token file is truncated")

        try:
            plaintext = AESGCM(self._key).decrypt(data[:_NONCE_SIZE], data[_NONCE_SIZE:], None)
            payload = json.loads(plaintext)
            return TokenData(
                access_token=payload["access_token"],
                refresh_token=payload["refresh_token"],
                expires_at=float(payload["expires_at"]),
                scope=payload.get("scope"),
            )
        except Exception:
            # Do not chain: the original exception adds nothing useful and
            # must not leak decrypted content into logs.
            raise WhoopError(
                ErrorCode.TOKEN_STORE_ERROR,
                "Token file cannot be decrypted or parsed (wrong key or damaged file)",
            ) from None

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)
        logger.info("Tokens cleared")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _ensure_dir(self) -> None:
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    def _fsync_dir(self) -> None:
        with contextlib.suppress(OSError):
            dir_fd = os.open(self._path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
