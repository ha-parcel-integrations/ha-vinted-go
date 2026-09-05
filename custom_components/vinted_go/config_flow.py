"""Config flow for the Vinted Go parcel tracker integration.

Passwordless e-mail login in two steps: enter an e-mail (Vinted Go sends a
verification link), then paste that link (or its token). Reauth reuses the same
two steps to mint a fresh refresh token.
"""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import VintedGoApiClient, VintedGoApiError, VintedGoInvalidToken
from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_EMAIL,
    CONF_INCLUDE_HISTORY,
    CONF_REFRESH_INTERVAL,
    CONF_REFRESH_TOKEN,
    CONF_USER_ID,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    DEFAULT_INCLUDE_HISTORY,
    DEFAULT_NEW_REFRESH_INTERVAL,
    DEFAULT_REFRESH_INTERVAL,
    DOMAIN,
    REFRESH_INTERVAL_AUTO,
    REFRESH_INTERVAL_OPTIONS,
)

_LOGGER = logging.getLogger(__name__)


def extract_token(value: str) -> str:
    """Return the verification token from a pasted link or a raw token.

    Accepts the whole ``https://app.vintedgo.com/auth/verify?token=…`` link or
    just the token, trimming whitespace either way.
    """
    value = (value or "").strip()
    if "token=" in value:
        query = parse_qs(urlparse(value).query)
        if query.get("token"):
            return query["token"][0]
        match = re.search(r"token=([^&\s]+)", value)
        if match:
            return match.group(1)
    return value


def _interval_selector() -> selector.SelectSelector:
    """Return the refresh-interval dropdown selector."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[REFRESH_INTERVAL_AUTO]
            + [str(m) for m in REFRESH_INTERVAL_OPTIONS],
            translation_key=CONF_REFRESH_INTERVAL,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


class VintedGoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the passwordless e-mail login flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise transient flow state."""
        self._email: str | None = None
        self._reauth_entry: ConfigEntry | None = None

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> VintedGoOptionsFlowHandler:
        """Return the options flow handler."""
        return VintedGoOptionsFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: ask for the e-mail and request a verification link."""
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            client = VintedGoApiClient(async_get_clientsession(self.hass))
            try:
                await client.async_register(email)
            except VintedGoApiError:
                errors["base"] = "cannot_connect"
            else:
                self._email = email
                return await self.async_step_verify()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_EMAIL): str}),
            errors=errors,
        )

    async def async_step_verify(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: confirm the token from the e-mail and create the entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            token = extract_token(user_input["token"])
            client = VintedGoApiClient(async_get_clientsession(self.hass))
            try:
                await client.async_confirm(token)
                user_id = await client.async_get_user_id()
            except VintedGoInvalidToken:
                errors["base"] = "invalid_token"
            except VintedGoApiError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(str(user_id))
                data = {
                    CONF_EMAIL: self._email,
                    CONF_USER_ID: user_id,
                    CONF_REFRESH_TOKEN: client.refresh_token,
                }
                if self._reauth_entry is not None:
                    self._abort_if_unique_id_mismatch(reason="wrong_account")
                    return self.async_update_reload_and_abort(
                        self._reauth_entry, data=data
                    )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Vinted Go ({self._email})",
                    data=data,
                    options={
                        CONF_DELIVERED_FILTER_TYPE: DEFAULT_DELIVERED_FILTER_TYPE,
                        CONF_DELIVERED_FILTER_AMOUNT: DEFAULT_DELIVERED_FILTER_AMOUNT,
                        # New installs default to dynamic polling; an entry
                        # set up before this option existed keeps reading
                        # DEFAULT_REFRESH_INTERVAL via the coordinator's
                        # .get() fallback instead (Section 5.2).
                        CONF_REFRESH_INTERVAL: DEFAULT_NEW_REFRESH_INTERVAL,
                        CONF_INCLUDE_HISTORY: DEFAULT_INCLUDE_HISTORY,
                    },
                )

        return self.async_show_form(
            step_id="verify",
            data_schema=vol.Schema({vol.Required("token"): str}),
            errors=errors,
            description_placeholders={"email": self._email or ""},
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Start reauth — the refresh token no longer works."""
        self._reauth_entry = self._get_reauth_entry()
        self._email = entry_data.get(CONF_EMAIL)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-request a verification link for the same e-mail."""
        errors: dict[str, str] = {}
        if user_input is not None:
            client = VintedGoApiClient(async_get_clientsession(self.hass))
            try:
                await client.async_register(self._email)
            except VintedGoApiError:
                errors["base"] = "cannot_connect"
            else:
                return await self.async_step_verify()

        return self.async_show_form(
            step_id="reauth_confirm",
            errors=errors,
            description_placeholders={"email": self._email or ""},
        )


class VintedGoOptionsFlowHandler(OptionsFlow):
    """Delivered-retention, history and polling — one sectioned form."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and handle the single sectioned options form."""
        if user_input is not None:
            delivered = user_input["delivered"]
            history = user_input["history"]
            polling = user_input["polling"]
            # Reload so a changed interval / retention takes effect immediately;
            # no update listener (deprecated alongside reload-on-update).
            self.hass.config_entries.async_schedule_reload(
                self.config_entry.entry_id
            )
            return self.async_create_entry(
                title="",
                data={
                    CONF_DELIVERED_FILTER_TYPE: delivered[CONF_DELIVERED_FILTER_TYPE],
                    CONF_DELIVERED_FILTER_AMOUNT: int(
                        delivered[CONF_DELIVERED_FILTER_AMOUNT]
                    ),
                    CONF_INCLUDE_HISTORY: bool(history[CONF_INCLUDE_HISTORY]),
                    CONF_REFRESH_INTERVAL: (
                        REFRESH_INTERVAL_AUTO
                        if polling[CONF_REFRESH_INTERVAL] == REFRESH_INTERVAL_AUTO
                        else int(polling[CONF_REFRESH_INTERVAL])
                    ),
                },
            )

        current = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required("delivered"): section(
                    vol.Schema(
                        {
                            vol.Required(
                                CONF_DELIVERED_FILTER_TYPE,
                                default=current.get(
                                    CONF_DELIVERED_FILTER_TYPE,
                                    DEFAULT_DELIVERED_FILTER_TYPE,
                                ),
                            ): selector.SelectSelector(
                                selector.SelectSelectorConfig(
                                    options=["days", "parcels"],
                                    translation_key=CONF_DELIVERED_FILTER_TYPE,
                                    mode=selector.SelectSelectorMode.LIST,
                                )
                            ),
                            vol.Required(
                                CONF_DELIVERED_FILTER_AMOUNT,
                                default=current.get(
                                    CONF_DELIVERED_FILTER_AMOUNT,
                                    DEFAULT_DELIVERED_FILTER_AMOUNT,
                                ),
                            ): selector.NumberSelector(
                                selector.NumberSelectorConfig(
                                    min=1, max=365, step=1,
                                    mode=selector.NumberSelectorMode.BOX,
                                )
                            ),
                        }
                    ),
                    {"collapsed": False},
                ),
                vol.Required("history"): section(
                    vol.Schema(
                        {
                            vol.Required(
                                CONF_INCLUDE_HISTORY,
                                default=current.get(
                                    CONF_INCLUDE_HISTORY, DEFAULT_INCLUDE_HISTORY
                                ),
                            ): selector.BooleanSelector(),
                        }
                    ),
                    {"collapsed": True},
                ),
                vol.Required("polling"): section(
                    vol.Schema(
                        {
                            vol.Required(
                                CONF_REFRESH_INTERVAL,
                                default=str(
                                    current.get(
                                        CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL
                                    )
                                ),
                            ): _interval_selector(),
                        }
                    ),
                    {"collapsed": True},
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
