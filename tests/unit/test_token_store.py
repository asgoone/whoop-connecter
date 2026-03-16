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

    def test_corrupted_file_too_short_returns_none(self, store):
        store._path.parent.mkdir(parents=True, exist_ok=True)
        store._path.write_bytes(b"short")  # < 12 bytes (nonce size)
        assert store.load() is None
        assert not store._path.exists()  # должен быть удалён

    def test_wrong_key_decryption_fails_returns_none(self, tmp_path, monkeypatch, sample_tokens):
        monkeypatch.setenv("WHOOP_TOKEN_ENCRYPTION_KEY", KEY_HEX)
        store1 = TokenStore(str(tmp_path / "tokens.enc"))
        store1.save(sample_tokens)

        # Другой ключ
        monkeypatch.setenv("WHOOP_TOKEN_ENCRYPTION_KEY", "b" * 64)
        store2 = TokenStore(str(tmp_path / "tokens.enc"))
        result = store2.load()
        assert result is None
        assert not (tmp_path / "tokens.enc").exists()

    def test_random_bytes_decryption_fails_returns_none(self, store):
        store._path.parent.mkdir(parents=True, exist_ok=True)
        store._path.write_bytes(os.urandom(100))  # random ciphertext
        assert store.load() is None

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

        # Первые 12 байт — nonce; при разных nonce весь файл будет отличаться
        assert data1[:12] != data2[:12] or data1 != data2

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
