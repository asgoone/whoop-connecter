"""
WHOOP OAuth 2.0 with PKCE.

Background paths (get_access_token / force_refresh / token_status) never start
an interactive login: no browser, no stdin, no callback server. Interactive
login happens only through the explicit login_browser() / login_headless().

WHOOP rotates both tokens on every refresh and invalidates the previous access
token, so every refresh runs under the TokenStore lock and re-reads the store
first: a token already refreshed by another process is reused, not refreshed
again.
"""

import base64
import hashlib
import http.server
import logging
import secrets
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx

from whoop.errors import ErrorCode, WhoopError, parse_retry_after

from .token_store import TokenData, TokenStore

logger = logging.getLogger(__name__)

WHOOP_AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
WHOOP_TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
SCOPES = "read:profile read:body_measurement read:workout read:recovery read:sleep read:cycles offline"

_EXPIRY_BUFFER_SECONDS = 60
_TOKEN_REQUEST_TIMEOUT = 30


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
    # Non-interactive API (safe for MCP, cron, background CLI)
    # ------------------------------------------------------------------

    def get_access_token(self) -> str:
        """Return a valid access token, refreshing under lock if expired.

        Raises WhoopError: AUTH_REQUIRED, REAUTH_REQUIRED, TRANSIENT_ERROR,
        RATE_LIMITED, TOKEN_STORE_ERROR, CONFIG_ERROR.
        """
        tokens = self._store.load()
        if tokens and not self._is_expired(tokens.expires_at):
            return tokens.access_token

        with self._store.lock():
            tokens = self._require_tokens()
            if not self._is_expired(tokens.expires_at):
                # Another process refreshed while we waited for the lock
                return tokens.access_token
            logger.info("Access token expired, refreshing")
            return self._refresh_locked(tokens)

    def force_refresh(self, rejected_access_token: str) -> str:
        """Recover after WHOOP rejected an access token with 401.

        If the stored token differs from the rejected one, another process has
        already rotated it: return the stored token without a second refresh.
        """
        with self._store.lock():
            tokens = self._require_tokens()
            if (
                tokens.access_token != rejected_access_token
                and not self._is_expired(tokens.expires_at)
            ):
                logger.info("Access token was already rotated by another process")
                return tokens.access_token
            logger.info("Access token rejected by WHOOP, refreshing")
            return self._refresh_locked(tokens)

    def token_status(self) -> dict:
        """Passive status: reads the token file only, no network, no refresh.

        state: valid | refresh_needed | auth_required | reauth_required | store_error.
        "authenticated" means stored credentials exist that may still work; only a
        real API call (WhoopService.auth_check) proves the refresh token is accepted.
        """
        try:
            tokens = self._store.load()
        except WhoopError as exc:
            return {
                "state": "store_error",
                "authenticated": False,
                "expires_at": None,
                "expired": None,
                "error": exc.to_dict(),
            }

        if tokens is None:
            return {"state": "auth_required", "authenticated": False, "expires_at": None, "expired": None}

        expired = self._is_expired(tokens.expires_at)
        if not expired:
            state = "valid"
        elif tokens.refresh_token:
            state = "refresh_needed"
        else:
            state = "reauth_required"

        status = {
            "state": state,
            "authenticated": state != "reauth_required",
            "expires_at": datetime.fromtimestamp(tokens.expires_at, tz=timezone.utc).isoformat(),
            "expired": expired,
            "has_refresh_token": bool(tokens.refresh_token),
        }
        if tokens.scope is not None:
            granted = set(tokens.scope.split())
            status["scopes"] = sorted(granted)
            status["missing_scopes"] = sorted(set(SCOPES.split()) - granted)
        return status

    # ------------------------------------------------------------------
    # Interactive login (explicit operator action only)
    # ------------------------------------------------------------------

    def login_browser(self) -> None:
        """Open a local browser and catch the redirect on a temporary local server."""
        auth_url, state, code_verifier = self.get_auth_url()

        parsed = urllib.parse.urlparse(self._config.redirect_uri)
        port = parsed.port or 8080

        result = _CallbackResult()
        handler_class = _make_callback_handler(result, expected_state=state)
        server = http.server.HTTPServer(("localhost", port), handler_class)
        server.timeout = 120

        logger.info("Opening browser for WHOOP authorization")
        print(f"\nOpening browser. If it doesn't open, visit:\n{auth_url}\n")
        webbrowser.open(auth_url)

        deadline = time.time() + 120
        try:
            while result.code is None and result.error is None:
                if time.time() > deadline:
                    raise TimeoutError("OAuth callback timed out after 120 seconds")
                server.handle_request()
        finally:
            server.server_close()

        if result.error:
            raise RuntimeError(f"OAuth error: {result.error}")
        if result.code is None:
            raise RuntimeError("OAuth callback completed but no authorization code was received")

        self._exchange_code(result.code, code_verifier)

    def login_headless(self) -> None:
        """Headless OAuth: prints auth URL, accepts callback URL from stdin.

        Designed for VPS usage where no browser is available.
        """
        auth_url, state, code_verifier = self.get_auth_url()

        print("\n=== WHOOP OAuth (headless) ===")
        print("Open this URL in your browser:\n")
        print(auth_url)
        print("\nAfter authorizing, your browser will redirect to a URL like:")
        print(f"  {self._config.redirect_uri}?code=...&state=...")
        print("The page may fail to load - that is expected.")
        print("Copy the FULL redirect URL from the address bar and paste it below.\n")

        callback_url = input("Callback URL: ").strip()
        if not callback_url:
            raise RuntimeError("No callback URL provided")

        self.exchange_callback(callback_url, state, code_verifier)

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

    def exchange_callback(self, callback_url: str, expected_state: str, code_verifier: str) -> None:
        """Complete OAuth by parsing a callback URL. For bot/external integrations."""
        parsed = urllib.parse.urlparse(callback_url)
        params = dict(urllib.parse.parse_qsl(parsed.query))

        if params.get("state") != expected_state:
            raise RuntimeError("State mismatch: the callback URL does not belong to this login attempt")

        code = params.get("code")
        if not code:
            error = params.get("error_description", params.get("error", "unknown"))
            raise RuntimeError(f"OAuth error: {error}")

        self._exchange_code(code, code_verifier)

    def revoke(self) -> None:
        with self._store.lock():
            self._store.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _is_expired(expires_at: float, buffer_seconds: int = _EXPIRY_BUFFER_SECONDS) -> bool:
        return time.time() >= expires_at - buffer_seconds

    def _require_tokens(self) -> TokenData:
        tokens = self._store.load()
        if tokens is None:
            raise WhoopError(ErrorCode.AUTH_REQUIRED, "No WHOOP tokens stored")
        return tokens

    def _refresh_locked(self, tokens: TokenData) -> str:
        """Refresh and save. Caller must hold the store lock.

        On any failure the stored tokens are left untouched.
        """
        if not tokens.refresh_token:
            raise WhoopError(ErrorCode.REAUTH_REQUIRED, "No refresh token stored")

        payload = self._post_token({
            "grant_type": "refresh_token",
            "refresh_token": tokens.refresh_token,
            "client_id": self._config.client_id,
            "client_secret": self._config.client_secret,
            "scope": "offline",
        })
        new_tokens = self._tokens_from_payload(payload, previous=tokens)
        try:
            self._store.save(new_tokens)
        except WhoopError:
            # WHOOP already rotated the pair: losing it means a forced re-login
            logger.warning("Saving refreshed tokens failed, retrying once")
            try:
                self._store.save(new_tokens)
            except WhoopError as exc:
                logger.critical("Refreshed tokens could not be saved; re-login will be required")
                raise WhoopError(
                    ErrorCode.TOKEN_STORE_ERROR,
                    f"Tokens were refreshed by WHOOP but could not be saved ({exc.message})",
                ) from None
        logger.info("Tokens refreshed (expires in %ds)", int(new_tokens.expires_at - time.time()))
        return new_tokens.access_token

    def _exchange_code(self, code: str, code_verifier: str) -> None:
        payload = self._post_token({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._config.redirect_uri,
            "client_id": self._config.client_id,
            "client_secret": self._config.client_secret,
            "code_verifier": code_verifier,
        })
        new_tokens = self._tokens_from_payload(payload, previous=None)
        with self._store.lock():
            self._store.save(new_tokens)
        logger.info("Login complete, tokens saved")

    @staticmethod
    def _post_token(data: dict) -> dict:
        """POST to the token endpoint and classify failures. Never logs secrets."""
        try:
            resp = httpx.post(
                WHOOP_TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=_TOKEN_REQUEST_TIMEOUT,
            )
        except httpx.TimeoutException:
            raise WhoopError(ErrorCode.TRANSIENT_ERROR, "WHOOP token endpoint timed out") from None
        except httpx.TransportError as exc:
            raise WhoopError(
                ErrorCode.TRANSIENT_ERROR, f"Network error calling WHOOP token endpoint ({type(exc).__name__})"
            ) from None

        status = resp.status_code
        if status == 429:
            raise WhoopError(
                ErrorCode.RATE_LIMITED,
                "WHOOP token endpoint rate limited",
                retry_after=parse_retry_after(resp.headers.get("Retry-After")),
            )
        if status >= 500:
            raise WhoopError(ErrorCode.TRANSIENT_ERROR, f"WHOOP token endpoint returned {status}")
        if status >= 400:
            oauth_error = _oauth_error_code(resp)
            message = f"WHOOP token endpoint returned {status} ({oauth_error or 'no error code'})"
            if oauth_error == "invalid_client":
                raise WhoopError(ErrorCode.CONFIG_ERROR, message)
            if oauth_error == "invalid_scope":
                raise WhoopError(ErrorCode.INSUFFICIENT_SCOPE, message)
            if oauth_error in ("invalid_grant", "unauthorized_client"):
                raise WhoopError(ErrorCode.REAUTH_REQUIRED, message)
            if status in (400, 401) and oauth_error is None:
                # RFC 6749 rejections without a parseable code: treat the grant as unusable
                raise WhoopError(ErrorCode.REAUTH_REQUIRED, message)
            if oauth_error is None:
                # 403/408/425/... without an OAuth body: likely proxy/WAF, not the grant
                raise WhoopError(ErrorCode.TRANSIENT_ERROR, message)
            raise WhoopError(ErrorCode.API_ERROR, message)

        try:
            payload = resp.json()
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            raise WhoopError(ErrorCode.API_ERROR, "WHOOP token endpoint returned a non-JSON body")
        return payload

    @staticmethod
    def _tokens_from_payload(payload: dict, previous: TokenData | None) -> TokenData:
        """Validate a token response. Raises instead of saving a malformed response."""
        access_token = payload.get("access_token")
        expires_in = payload.get("expires_in")
        if (
            not isinstance(access_token, str)
            or not access_token
            or isinstance(expires_in, bool)
            or not isinstance(expires_in, (int, float))
            or expires_in <= 0
        ):
            raise WhoopError(ErrorCode.API_ERROR, "Malformed WHOOP token response")

        refresh_token = payload.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            if previous is not None and previous.refresh_token:
                # WHOOP normally rotates the refresh token; keep the previous one
                # rather than storing an empty value that would force a re-login.
                logger.warning("Refresh response has no refresh_token; keeping the previous one")
                refresh_token = previous.refresh_token
            else:
                raise WhoopError(
                    ErrorCode.INSUFFICIENT_SCOPE,
                    "WHOOP did not return a refresh token (offline scope not granted)",
                )

        scope = payload.get("scope")
        if not isinstance(scope, str):
            scope = previous.scope if previous is not None else None

        return TokenData(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=time.time() + expires_in,
            scope=scope,
        )


def _oauth_error_code(resp: httpx.Response) -> str | None:
    """Extract the RFC 6749 "error" code only; descriptions may echo input."""
    try:
        body = resp.json()
    except ValueError:
        return None
    if isinstance(body, dict) and isinstance(body.get("error"), str):
        return body["error"][:64]
    return None
