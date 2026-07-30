"""Vinted Go account API client.

Vinted Go is account-based, with **passwordless e-mail** auth (no password, no
OAuth/captcha):

* :meth:`async_register` — POST an e-mail; Vinted Go e-mails a verification link.
* :meth:`async_confirm` — POST the token from that link; returns a session token
  (a 7-day JWT) and a refresh token.
* :meth:`async_get_shipments` — the account's parcels (sent + received), Bearer.
* :meth:`async_get_tracking_events` — a parcel's timeline (the public keyless
  endpoint, which also answers for the account's own tracking codes).

Only the **refresh token** is persisted. The session token is refreshed silently
(:meth:`_async_refresh`); a rotated refresh token is handed back through
``on_tokens_updated`` so the caller can persist it. A failed refresh raises
:class:`VintedGoAuthError`, which setup maps to reauth.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import aiohttp

from .const import (
    CONFIRM_URL,
    LOGOUT_URL,
    ME_URL,
    REFRESH_URL,
    REGISTER_URL,
    SHIPMENTS_URL,
    TRACKING_EVENTS_URL,
)

_LOGGER = logging.getLogger(__name__)


class VintedGoApiError(Exception):
    """Raised when a Vinted Go request fails in a non-auth way (retry later)."""

    def __init__(self, detail: str) -> None:
        """Store the detail that triggered the error."""
        super().__init__(f"Vinted Go request failed: {detail}")
        self.detail = detail


class VintedGoAuthError(Exception):
    """Raised when the session cannot be (re)established — triggers reauth."""


class VintedGoInvalidToken(Exception):
    """Raised when a verification token is rejected (config-flow only)."""


class VintedGoApiClient:
    """Client for the Vinted Go account API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        refresh_token: str | None = None,
        *,
        on_tokens_updated: Callable[[str], None] | None = None,
    ) -> None:
        """Initialise with an aiohttp session and (optionally) a refresh token."""
        self._session = session
        self._refresh_token = refresh_token
        self._session_token: str | None = None
        self._on_tokens_updated = on_tokens_updated

    @property
    def refresh_token(self) -> str | None:
        """The current (possibly rotated) refresh token."""
        return self._refresh_token

    # --- passwordless login (config flow) -----------------------------------

    async def async_register(self, email: str) -> None:
        """Ask Vinted Go to e-mail a verification link to ``email``."""
        async with self._session.post(REGISTER_URL, json={"email": email}) as resp:
            if resp.status != 200:
                raise VintedGoApiError(f"registration HTTP {resp.status}")

    async def async_confirm(self, token: str) -> None:
        """Exchange a verification token for a session + refresh token.

        Raises :class:`VintedGoInvalidToken` when the token is wrong or expired.
        """
        async with self._session.post(CONFIRM_URL, json={"token": token}) as resp:
            if resp.status in (400, 401, 404, 422):
                raise VintedGoInvalidToken
            if resp.status != 200:
                raise VintedGoApiError(f"confirm HTTP {resp.status}")
            self._store_tokens(await _json(resp))

    async def async_get_user_id(self) -> int:
        """Return the numeric account id (used as the config entry unique_id)."""
        data = await self._authed_get(ME_URL)
        user_id = data.get("user_id")
        if user_id is None:
            raise VintedGoApiError("users/me without user_id")
        return int(user_id)

    # --- session management --------------------------------------------------

    def _store_tokens(self, payload: dict[str, Any]) -> None:
        """Store the token pair from a confirm/refresh response."""
        session_token = payload.get("session_token")
        refresh_token = payload.get("refresh_token")
        if not session_token or not refresh_token:
            raise VintedGoApiError("token response missing a token")
        self._session_token = session_token
        rotated = refresh_token != self._refresh_token
        self._refresh_token = refresh_token
        if rotated and self._on_tokens_updated is not None:
            self._on_tokens_updated(refresh_token)

    async def _async_refresh(self) -> None:
        """Mint a fresh session token from the refresh token."""
        if not self._refresh_token:
            raise VintedGoAuthError
        try:
            async with self._session.post(
                REFRESH_URL, json={"refresh_token": self._refresh_token}
            ) as resp:
                if resp.status == 200:
                    self._store_tokens(await _json(resp))
                    return
                if resp.status in (400, 401, 403, 404, 422):
                    raise VintedGoAuthError
                raise VintedGoApiError(f"refresh HTTP {resp.status}")
        except aiohttp.ClientError as err:
            # A transient network error is not an auth failure — let it retry.
            raise VintedGoApiError(f"refresh network error ({err})") from err

    async def _authed_get(self, url: str) -> Any:
        """GET ``url`` with the session token, refreshing once on a 401."""
        if self._session_token is None:
            await self._async_refresh()
        for attempt in range(2):
            headers = {"Authorization": f"Bearer {self._session_token}"}
            async with self._session.get(url, headers=headers) as resp:
                if resp.status == 401 and attempt == 0:
                    await self._async_refresh()
                    continue
                if resp.status == 401:
                    raise VintedGoAuthError
                if resp.status != 200:
                    raise VintedGoApiError(f"HTTP {resp.status}")
                return await _json(resp)
        raise VintedGoAuthError

    async def async_logout(self) -> None:
        """Best-effort logout — never raises."""
        if not self._refresh_token:
            return
        try:
            async with self._session.delete(
                LOGOUT_URL, json={"refresh_token": self._refresh_token}
            ):
                pass
        except aiohttp.ClientError:
            pass

    # --- data ----------------------------------------------------------------

    async def async_get_shipments(self) -> list[dict[str, Any]]:
        """Return the account's shipments (sent + received)."""
        data = await self._authed_get(SHIPMENTS_URL)
        if not isinstance(data, list):
            raise VintedGoApiError("shipments body is not a list")
        return [item for item in data if isinstance(item, dict)]

    async def async_get_tracking_events(
        self, tracking_code: str
    ) -> dict[str, Any] | None:
        """Return a shipment's timeline via the public endpoint, or ``None``.

        ``None`` on 404 (not scanned yet). Best-effort: never raises for a single
        parcel — a timeline hiccup must not fail the whole poll.
        """
        url = TRACKING_EVENTS_URL.format(tracking_code=tracking_code)
        try:
            async with self._session.get(url) as resp:
                if resp.status == 404:
                    return None
                if resp.status != 200:
                    return None
                data = await _json(resp)
        except (aiohttp.ClientError, VintedGoApiError):
            return None
        return data if isinstance(data, dict) else None


async def _json(resp: aiohttp.ClientResponse) -> Any:
    """Parse a JSON body, tolerating a text/plain content type."""
    try:
        return await resp.json(content_type=None)
    except ValueError as err:
        raise VintedGoApiError(f"unparseable body ({err})") from err
