"""
Unit tests for whoop/auth/token_store.py
Tests encryption, decryption, file permissions, and error handling.
"""

import os
import stat
import time
import pytest
from pathlib import Path
from whoop.auth.token_store import TokenStore, TokenData
from whoop.errors import ErrorCode, WhoopError


KEY_HEX = "a" * 64  # 32 bytes of 0xaa — valid AES-256 key


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("WHOOP_TOKEN_ENCRYPTION_KEY", KEY_HEX)
    return TokenStore(str(tmp_path / "tokens.enc"))


@pytest.fixture
def sample_tokens():
    return TokenData(
        access_token="access_abc",
        refresh_token="refresh_xyz",
        expires_at=time.time() + 3600,
    )


class TestTokenStore:
    def test_save_and_load_roundtrip(self, store, sample_tokens):
        store.save(sample_tokens)
        loaded = store.load()
        assert loaded is not None
        assert loaded.access_token == sample_tokens.access_token
        assert loaded.refresh_token == sample_tokens.refresh_token
        assert loaded.expires_at == pytest.approx(sample_tokens.expires_at, abs=1.0)

    def test_load_nonexistent_returns_none(self, store):
        assert store.load() is None

    def test_file_permissions_600(self, store, sample_tokens):
        store.save(sample_tokens)
        mode = os.stat(store._path).st_mode
        assert mode & 0o777 == 0o600

    def test_clear_removes_file(self, store, sample_tokens):
        store.save(sample_tokens)
        assert store._path.exists()
        store.clear()
        assert not store._path.exists()

    def test_clear_nonexistent_does_not_raise(self, store):
        store.clear()  # должно быть тихим

    def test_corrupted_file_too_short_raises_and_preserves_file(self, store):
        store._path.parent.mkdir(parents=True, exist_ok=True)
        store._path.write_bytes(b"short")  # < 12 bytes (nonce size)
        with pytest.raises(WhoopError) as exc_info:
            store.load()
        assert exc_info.value.code == ErrorCode.TOKEN_STORE_ERROR
        assert store._path.read_bytes() == b"short"

    def test_wrong_key_raises_and_preserves_file(self, tmp_path, monkeypatch, sample_tokens):
        monkeypatch.setenv("WHOOP_TOKEN_ENCRYPTION_KEY", KEY_HEX)
        store1 = TokenStore(str(tmp_path / "tokens.enc"))
        store1.save(sample_tokens)
        original = (tmp_path / "tokens.enc").read_bytes()

        monkeypatch.setenv("WHOOP_TOKEN_ENCRYPTION_KEY", "b" * 64)
        store2 = TokenStore(str(tmp_path / "tokens.enc"))
        with pytest.raises(WhoopError) as exc_info:
            store2.load()
        assert exc_info.value.code == ErrorCode.TOKEN_STORE_ERROR
        assert (tmp_path / "tokens.enc").read_bytes() == original

        # The correct key still reads the preserved file
        monkeypatch.setenv("WHOOP_TOKEN_ENCRYPTION_KEY", KEY_HEX)
        assert TokenStore(str(tmp_path / "tokens.enc")).load().access_token == "access_abc"

    def test_random_bytes_raises(self, store):
        store._path.parent.mkdir(parents=True, exist_ok=True)
        store._path.write_bytes(os.urandom(100))  # random ciphertext
        with pytest.raises(WhoopError):
            store.load()
        assert store._path.exists()

    def test_error_message_has_no_secrets(self, tmp_path, monkeypatch, sample_tokens):
        monkeypatch.setenv("WHOOP_TOKEN_ENCRYPTION_KEY", KEY_HEX)
        TokenStore(str(tmp_path / "tokens.enc")).save(sample_tokens)
        monkeypatch.setenv("WHOOP_TOKEN_ENCRYPTION_KEY", "b" * 64)
        with pytest.raises(WhoopError) as exc_info:
            TokenStore(str(tmp_path / "tokens.enc")).load()
        text = str(exc_info.value)
        assert KEY_HEX not in text and "b" * 64 not in text
        assert exc_info.value.__cause__ is None

    def test_missing_env_var_raises(self, tmp_path, monkeypatch):
        monkeypatch.delenv("WHOOP_TOKEN_ENCRYPTION_KEY", raising=False)
        with pytest.raises(EnvironmentError, match="WHOOP_TOKEN_ENCRYPTION_KEY"):
            TokenStore(str(tmp_path / "tokens.enc"))

    def test_short_key_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WHOOP_TOKEN_ENCRYPTION_KEY", "aa" * 10)  # 10 bytes, not 32
        with pytest.raises(ValueError, match="32 bytes"):
            TokenStore(str(tmp_path / "tokens.enc"))

    def test_invalid_hex_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WHOOP_TOKEN_ENCRYPTION_KEY", "z" * 64)  # invalid hex
        with pytest.raises(ValueError):
            TokenStore(str(tmp_path / "tokens.enc"))

    def test_each_save_uses_different_nonce(self, store, sample_tokens):
        """Каждое сохранение должно давать разный nonce."""
        store.save(sample_tokens)
        data1 = store._path.read_bytes()

        store.save(sample_tokens)
        data2 = store._path.read_bytes()

        assert data1[:12] != data2[:12]

    def test_parent_dir_created_automatically(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WHOOP_TOKEN_ENCRYPTION_KEY", KEY_HEX)
        deep_path = tmp_path / "a" / "b" / "c" / "tokens.enc"
        store = TokenStore(str(deep_path))
        tokens = TokenData(access_token="x", refresh_token="y", expires_at=time.time() + 1000)
        store.save(tokens)
        assert deep_path.exists()

    def test_empty_refresh_token_roundtrip(self, store):
        tokens = TokenData(access_token="access", refresh_token="", expires_at=time.time() + 1000)
        store.save(tokens)
        loaded = store.load()
        assert loaded.refresh_token == ""


class TestTokenStoreRobustness:
    def test_interrupted_write_keeps_previous_file(self, store, sample_tokens, monkeypatch):
        store.save(sample_tokens)
        before = store._path.read_bytes()

        def _boom(_fd):
            raise OSError("disk full")

        monkeypatch.setattr("whoop.auth.token_store.os.fsync", _boom)
        with pytest.raises(WhoopError) as exc_info:
            store.save(TokenData(access_token="new", refresh_token="new", expires_at=time.time() + 10))

        assert exc_info.value.code == ErrorCode.TOKEN_STORE_ERROR
        assert store._path.read_bytes() == before
        leftovers = [p.name for p in store._path.parent.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []

    def test_temp_file_is_0600_before_replace(self, store, sample_tokens, monkeypatch):
        real_replace = os.replace
        modes = []

        def _spy(src, dst):
            modes.append(os.stat(src).st_mode & 0o777)
            real_replace(src, dst)

        monkeypatch.setattr("whoop.auth.token_store.os.replace", _spy)
        old_umask = os.umask(0)  # even a permissive umask must not widen permissions
        try:
            store.save(sample_tokens)
        finally:
            os.umask(old_umask)
        assert modes == [0o600]
        assert os.stat(store._path).st_mode & 0o777 == 0o600

    def test_scope_roundtrip(self, store):
        store.save(TokenData(access_token="a", refresh_token="r", expires_at=time.time() + 10, scope="offline read:sleep"))
        assert store.load().scope == "offline read:sleep"

    def test_legacy_file_without_scope_loads(self, store):
        import json as _json
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = os.urandom(12)
        plaintext = _json.dumps({"access_token": "a", "refresh_token": "r", "expires_at": 1.0}).encode()
        store._path.parent.mkdir(parents=True, exist_ok=True)
        store._path.write_bytes(nonce + AESGCM(bytes.fromhex(KEY_HEX)).encrypt(nonce, plaintext, None))
        loaded = store.load()
        assert loaded.refresh_token == "r"
        assert loaded.scope is None

    def test_lock_is_exclusive_and_times_out(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WHOOP_TOKEN_ENCRYPTION_KEY", KEY_HEX)
        holder = TokenStore(str(tmp_path / "tokens.enc"))
        waiter = TokenStore(str(tmp_path / "tokens.enc"), lock_timeout=0.2)
        with holder.lock():
            with pytest.raises(WhoopError) as exc_info:
                with waiter.lock():
                    pass
        assert exc_info.value.code == ErrorCode.TRANSIENT_ERROR
        with waiter.lock():  # released after holder exits
            pass

    def test_lock_dir_unusable_is_store_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WHOOP_TOKEN_ENCRYPTION_KEY", KEY_HEX)
        (tmp_path / "not_a_dir").write_text("x")
        store = TokenStore(str(tmp_path / "not_a_dir" / "tokens.enc"))
        with pytest.raises(WhoopError) as exc_info:
            with store.lock():
                pass
        assert exc_info.value.code == ErrorCode.TOKEN_STORE_ERROR

    def test_unreadable_token_path_is_store_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WHOOP_TOKEN_ENCRYPTION_KEY", KEY_HEX)
        (tmp_path / "tokens.enc").mkdir()  # reading a directory raises OSError
        with pytest.raises(WhoopError) as exc_info:
            TokenStore(str(tmp_path / "tokens.enc")).load()
        assert exc_info.value.code == ErrorCode.TOKEN_STORE_ERROR

    def test_unwritable_target_is_store_error(self, tmp_path, monkeypatch, sample_tokens):
        monkeypatch.setenv("WHOOP_TOKEN_ENCRYPTION_KEY", KEY_HEX)
        (tmp_path / "tokens.enc").mkdir()  # os.replace onto a directory fails
        with pytest.raises(WhoopError) as exc_info:
            TokenStore(str(tmp_path / "tokens.enc")).save(sample_tokens)
        assert exc_info.value.code == ErrorCode.TOKEN_STORE_ERROR
        assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]

