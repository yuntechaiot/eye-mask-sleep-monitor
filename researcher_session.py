"""Short-lived, server-side sessions for the researcher portal.

The browser receives only a random session ID. DocterCloud tokens and passwords
are never written into a browser cookie or local storage.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable


SESSION_TTL_SECONDS = 30 * 60
COOKIE_NAME = "eye_mask_researcher_session"
_SESSION_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{32,128}\Z")


@dataclass(frozen=True)
class ResearcherSession:
    token: str
    account: str
    expires_at: float


class ResearcherSessionStore:
    """A thread-safe, process-local store; app restarts invalidate sessions."""

    def __init__(
        self,
        ttl_seconds: int = SESSION_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: dict[str, ResearcherSession] = {}

    def create(self, token: str, account: str) -> tuple[str, ResearcherSession]:
        session_id = secrets.token_urlsafe(32)
        now = self._clock()
        session = ResearcherSession(token, account, now + self.ttl_seconds)
        with self._lock:
            self._sessions = {
                key: value
                for key, value in self._sessions.items()
                if value.expires_at > now
            }
            self._sessions[session_id] = session
        return session_id, session

    def get(self, session_id: str | None) -> ResearcherSession | None:
        if not isinstance(session_id, str) or not _SESSION_ID_PATTERN.fullmatch(session_id):
            return None
        with self._lock:
            session = self._sessions.get(session_id)
            if session and session.expires_at <= self._clock():
                self._sessions.pop(session_id, None)
                return None
            return session

    def revoke(self, session_id: str | None) -> None:
        if session_id:
            with self._lock:
                self._sessions.pop(session_id, None)


def cookie_script(session_id: str | None, *, remember: bool = False) -> str:
    """Set or clear the first-party cookie without placing a token in the DOM."""
    if session_id is not None and not _SESSION_ID_PATTERN.fullmatch(session_id):
        raise ValueError("Invalid researcher session ID")
    attributes = "Path=/; SameSite=Strict"
    if session_id is None:
        attributes += "; Max-Age=0"
    elif remember:
        attributes += f"; Max-Age={SESSION_TTL_SECONDS}"
    cookie = f"{COOKIE_NAME}={session_id or ''}; {attributes}"
    return (
        "<script>document.cookie = "
        + json.dumps(cookie)
        + " + (location.protocol === 'https:' ? '; Secure' : '');</script>"
    )
