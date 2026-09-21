"""The hub's one secret, and the browser sessions it opens."""

from __future__ import annotations

import hmac
from hashlib import sha256
from secrets import token_urlsafe
from typing import NewType

from kinby.contracts import AccessToken, ControlToken
from kinby.hub.registry import HubRegistry

#: The cookie a browser carries after it exchanges the access token at POST /auth/login.
SESSION_COOKIE = "kinby_session"
#: The value in that cookie. It names a stored session and nothing else.
SessionId = NewType("SessionId", str)

_TOKEN_BYTES = 32


def _hashed(secret: str) -> str:
    return sha256(secret.encode()).hexdigest()


def new_control_token() -> ControlToken:
    """Mint the secret the hub presents to one instance. The access token never goes there."""
    return ControlToken(token_urlsafe(_TOKEN_BYTES))


class HubAccess:
    """Authenticate the hub's one user, by access token or by the session it opened."""

    def __init__(self, registry: HubRegistry) -> None:
        self._registry = registry

    def issue(self) -> AccessToken | None:
        """Generate the access token on first start. A hub that has one keeps it."""
        token = AccessToken(token_urlsafe(_TOKEN_BYTES))
        if self._registry.try_set_access_token_hash(_hashed(token)):
            return token
        return None

    def rotate(self) -> AccessToken:
        """Replace the access token and end every session the old one opened."""
        token = AccessToken(token_urlsafe(_TOKEN_BYTES))
        self._registry.replace_access_token_hash(_hashed(token))
        return token

    def accepts(self, token: AccessToken) -> bool:
        """Compare in constant time, so a failed login tells an attacker nothing."""
        stored = self._registry.access_token_hash()
        if stored is None:
            return False
        return hmac.compare_digest(stored, _hashed(token))

    def login(self, token: AccessToken) -> SessionId | None:
        """Open a session if the token is still current after rotation."""
        if not self.accepts(token):
            return None
        session = SessionId(token_urlsafe(_TOKEN_BYTES))
        if self._registry.open_session_if_current(_hashed(token), _hashed(session)):
            return session
        return None

    def open_session(self) -> SessionId:
        session = SessionId(token_urlsafe(_TOKEN_BYTES))
        self._registry.open_session(_hashed(session))
        return session

    def session_open(self, session: SessionId) -> bool:
        return self._registry.session_open(_hashed(session))
