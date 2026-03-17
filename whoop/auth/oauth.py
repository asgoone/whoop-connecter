"""
WHOOP OAuth 2.0 with PKCE.
Runs a temporary local HTTP server to catch the redirect callback.
"""

import base64
import hashlib
import http.server
import logging
import os
import secrets
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx

from .token_store import TokenData, TokenStore

logger = logging.getLogger(__name__)

WHOOP_AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
WHOOP_TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
SCOPES = "read:profile read:body_measurement read:workout read:recovery read:sleep read:cycles offline"


@dataclass
class OAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str


class _CallbackResult:
    """Mutable container for OAuth callback result. Avoids class-level state."""

    def __init__(self) -> None:
        self.code: str | None = None
        self.error: str | None = None


def _make_callback_handler(result: _CallbackResult, expected_state: str):
    """Factory: creates a per-request handler that verifies state and captures code."""

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            params = dict(urllib.parse.parse_qsl(parsed.query))

            received_state = params.get("state")
            if received_state != expected_state:
                logger.warning("OAuth callback: state mismatch (possible CSRF)")
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<html><body><h2>Invalid state parameter.</h2></body></html>")
                return

            if "code" in params:
                result.code = params["code"]
            elif "error" in params:
                result.error = params.get("error_description", params["error"])

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>Auth complete. You can close this tab.</h2></body></html>"
            )

        def log_message(self, *_) -> None:
            pass

    return _Handler


class WhoopOAuth:
    def __init__(self, config: OAuthConfig, store: TokenStore) -> None:
        self._config = config
        self._store = store

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ensure_valid_token(self) -> str:
        """Return a valid access token, refreshing or re-authorizing as needed."""
        tokens = self._store.load()

        if tokens and not self._is_expired(tokens.expires_at):
            return tokens.access_token

        if tokens and tokens.refresh_token:
            logger.info("Access token expired, refreshing")
            try:
                return self._refresh(tokens.refresh_token)
            except Exception as exc:
                logger.warning("Token refresh failed (%s), starting new auth flow", exc)

        logger.info("Starting interactive OAuth flow")
        return self._authorize()

    def token_status(self) -> dict:
        """Return authentication status, attempting refresh if token is expired.

        This ensures the status reflects the *actual* usable state — not just
        the stored expires_at which may be stale after auto-refresh by the
        API client.
        """
        tokens = self._store.load()
        if tokens is None:
            return {"authenticated": False, "expires_at": None, "expired": None}

        expired = self._is_expired(tokens.expires_at)

        # If expired but we have a refresh token, try to refresh silently
        if expired and tokens.refresh_token:
            try:
                self._refresh(tokens.refresh_token)
                # Re-read updated tokens after successful refresh
                tokens = self._store.load()
                expired = False
                logger.info("Token refreshed during status check")
            except Exception as exc:
                logger.debug("Silent refresh during status check failed: %s", exc)

        return {
            "authenticated": True,
            "expires_at": datetime.fromtimestamp(tokens.expires_at, tz=timezone.utc).isoformat(),
            "expired": expired,
        }

    def authorize_headless(self) -> str:
        """Headless OAuth: prints auth URL, accepts callback URL from stdin.

        Designed for VPS / bot usage where no browser is available.
        Returns the access token.
        """
        auth_url, state, code_verifier = self.get_auth_url()

        print("\n=== WHOOP OAuth (headless) ===")
        print("Open this URL in your browser:\n")
        print(auth_url)
        print(f"\nAfter authorizing, your browser will redirect to a URL like:")
        print(f"  {self._config.redirect_uri}?code=...&state=...")
        print("Copy the FULL redirect URL and paste it below.\n")

        callback_url = input("Callback URL: ").strip()
        if not callback_url:
            raise RuntimeError("No callback URL provided")

        return self.exchange_callback(callback_url, state, code_verifier)

    def get_auth_url(self) -> tuple[str, str, str]:
        """Generate OAuth URL and PKCE params for external use (e.g. bot).

        Returns (auth_url, state, code_verifier) — caller must store
        state and code_verifier to complete the flow via exchange_callback().
        """
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = (
            base64.urlsafe_b64encode(
                hashlib.sha256(code_verifier.encode()).digest()
            )
            .rstrip(b"=")
            .decode()
        )

        state = secrets.token_urlsafe(16)
        auth_url = WHOOP_AUTH_URL + "?" + urlencode({
            "response_type": "code",
            "client_id": self._config.client_id,
            "redirect_uri": self._config.redirect_uri,
            "scope": SCOPES,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        })
        return auth_url, state, code_verifier

    def exchange_callback(self, callback_url: str, expected_state: str, code_verifier: str) -> str:
        """Complete OAuth by parsing a callback URL. For bot/external integrations.

        Returns the access token.
        """
        parsed = urllib.parse.urlparse(callback_url)
        params = dict(urllib.parse.parse_qsl(parsed.query))

        received_state = params.get("state")
        if received_state != expected_state:
            raise RuntimeError(
                f"State mismatch. Expected={expected_state}, got={received_state}"
            )

        code = params.get("code")
        if not code:
            error = params.get("error_description", params.get("error", "unknown"))
            raise RuntimeError(f"OAuth error: {error}")

        return self._exchange_code(code, code_verifier)

    def revoke(self) -> None:
        self._store.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _is_expired(expires_at: float, buffer_seconds: int = 60) -> bool:
        return time.time() >= expires_at - buffer_seconds

    def _authorize(self) -> str:
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = (
            base64.urlsafe_b64encode(
                hashlib.sha256(code_verifier.encode()).digest()
            )
            .rstrip(b"=")
            .decode()
        )

        parsed = urllib.parse.urlparse(self._config.redirect_uri)
        port = parsed.port or 8080

        state = secrets.token_urlsafe(16)
        auth_url = WHOOP_AUTH_URL + "?" + urlencode({
            "response_type": "code",
            "client_id": self._config.client_id,
            "redirect_uri": self._config.redirect_uri,
            "scope": SCOPES,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        })

        result = _CallbackResult()
        handler_class = _make_callback_handler(result, expected_state=state)
        server = http.server.HTTPServer(("localhost", port), handler_class)
        server.timeout = 120

        logger.info("Opening browser for WHOOP authorization")
        print(f"\nOpening browser. If it doesn't open, visit:\n{auth_url}\n")
        webbrowser.open(auth_url)

        deadline = time.time() + 120
        while result.code is None and result.error is None:
            if time.time() > deadline:
                server.server_close()
                raise TimeoutError("OAuth callback timed out after 120 seconds")
            server.handle_request()

        server.server_close()

        if result.error:
            raise RuntimeError(f"OAuth error: {result.error}")

        if result.code is None:
            raise RuntimeError("OAuth callback completed but no authorization code was received")

        return self._exchange_code(result.code, code_verifier)

    def _exchange_code(self, code: str, code_verifier: str) -> str:
        resp = httpx.post(
            WHOOP_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._config.redirect_uri,
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret,
                "code_verifier": code_verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        resp.raise_for_status()
        return self._save_and_return(resp.json())

    def _refresh(self, refresh_token: str) -> str:
        resp = httpx.post(
            WHOOP_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        resp.raise_for_status()
        return self._save_and_return(resp.json())

    def _save_and_return(self, payload: dict) -> str:
        expires_at = time.time() + payload["expires_in"]
        tokens = TokenData(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token", ""),
            expires_at=expires_at,
        )
        self._store.save(tokens)
        logger.info("Tokens saved (expires in %ds)", payload["expires_in"])
        return tokens.access_token
