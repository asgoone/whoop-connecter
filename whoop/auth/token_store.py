"""
Encrypted token storage. Tokens are AES-256-GCM encrypted on disk.
File permissions are set to 600 (owner read/write only).
"""

import json
import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)


@dataclass
class TokenData:
    access_token: str
    refresh_token: str
    expires_at: float  # Unix timestamp


class TokenStore:
    _KEY_ENV = "WHOOP_TOKEN_ENCRYPTION_KEY"

    def __init__(self, token_path: str) -> None:
        self._path = Path(token_path).expanduser()
        self._key = self._load_key()

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

    def save(self, tokens: TokenData) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)

        plaintext = json.dumps({
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token,
            "expires_at": tokens.expires_at,
        }).encode()

        nonce = os.urandom(12)
        aesgcm = AESGCM(self._key)
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)

        self._path.write_bytes(nonce + ciphertext)
        self._path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600
        logger.debug("Tokens saved to %s", self._path)

    def load(self) -> TokenData | None:
        if not self._path.exists():
            return None

        data = self._path.read_bytes()
        if len(data) < 12:
            logger.warning("Token file is corrupted (too short), removing")
            self._path.unlink(missing_ok=True)
            return None

        nonce, ciphertext = data[:12], data[12:]
        aesgcm = AESGCM(self._key)
        try:
            plaintext = aesgcm.decrypt(nonce, ciphertext, None)
        except Exception:
            logger.warning("Token file decryption failed, removing")
            self._path.unlink(missing_ok=True)
            return None

        payload = json.loads(plaintext)
        return TokenData(
            access_token=payload["access_token"],
            refresh_token=payload["refresh_token"],
            expires_at=payload["expires_at"],
        )

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)
        logger.info("Tokens cleared")
