"""Tests for the Vinted Go config, reauth and options flows."""
from unittest.mock import AsyncMock, MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vinted_go.api import VintedGoApiError, VintedGoInvalidToken
from custom_components.vinted_go.config_flow import extract_token
from custom_components.vinted_go.const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_EMAIL,
    CONF_INCLUDE_HISTORY,
    CONF_REFRESH_INTERVAL,
    CONF_REFRESH_TOKEN,
    CONF_USER_ID,
    DOMAIN,
)

LINK = "https://app.vintedgo.com/auth/verify?token=ABC123"


def _mock_client(**overrides) -> MagicMock:
    client = MagicMock()
    client.async_register = AsyncMock()
    client.async_confirm = AsyncMock()
    client.async_get_user_id = AsyncMock(return_value=12345)
    client.refresh_token = "refresh-abc"
    for key, value in overrides.items():
        setattr(client, key, value)
    return client


def _patch(client):
    return patch(
        "custom_components.vinted_go.config_flow.VintedGoApiClient",
        return_value=client,
    )


# --- extract_token ----------------------------------------------------------


def test_extract_token_from_link_and_raw():
    assert extract_token(LINK) == "ABC123"
    assert extract_token("  ABC123  ") == "ABC123"
    assert extract_token("token=XYZ") == "XYZ"


# --- config flow ------------------------------------------------------------


async def test_full_login_flow(hass):
    client = _mock_client()
    with _patch(client):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["step_id"] == "user"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EMAIL: "fam@example.com"}
        )
        assert result["step_id"] == "verify"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"token": LINK}
        )

    assert result["type"] == "create_entry"
    assert result["title"] == "Vinted Go (fam@example.com)"
    assert result["data"] == {
        CONF_EMAIL: "fam@example.com",
        CONF_USER_ID: 12345,
        CONF_REFRESH_TOKEN: "refresh-abc",
    }
    client.async_register.assert_awaited_once_with("fam@example.com")
    client.async_confirm.assert_awaited_once_with("ABC123")


async def test_register_cannot_connect(hass):
    client = _mock_client()
    client.async_register.side_effect = VintedGoApiError("x")
    with _patch(client):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EMAIL: "a@b.c"}
        )
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_invalid_token(hass):
    client = _mock_client()
    client.async_confirm.side_effect = VintedGoInvalidToken
    with _patch(client):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EMAIL: "a@b.c"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"token": "bad"}
        )
    assert result["step_id"] == "verify"
    assert result["errors"] == {"base": "invalid_token"}


async def test_single_instance_only(hass):
    """single_config_entry aborts a second account at flow start."""
    MockConfigEntry(domain=DOMAIN, unique_id="12345").add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "single_instance_allowed"


async def test_reauth_flow(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="12345",
        data={CONF_EMAIL: "fam@example.com", CONF_REFRESH_TOKEN: "old"},
    )
    entry.add_to_hass(hass)
    client = _mock_client()
    with _patch(client):
        result = await entry.start_reauth_flow(hass)
        assert result["step_id"] == "reauth_confirm"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["step_id"] == "verify"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"token": LINK}
        )
    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_REFRESH_TOKEN] == "refresh-abc"


async def test_reauth_register_error(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="12345",
        data={CONF_EMAIL: "fam@example.com", CONF_REFRESH_TOKEN: "old"},
    )
    entry.add_to_hass(hass)
    client = _mock_client()
    client.async_register.side_effect = VintedGoApiError("x")
    with _patch(client):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_reauth_wrong_account(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="12345",
        data={CONF_EMAIL: "fam@example.com", CONF_REFRESH_TOKEN: "old"},
    )
    entry.add_to_hass(hass)
    client = _mock_client()
    client.async_get_user_id = AsyncMock(return_value=9999)  # different account
    with _patch(client):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"token": LINK}
        )
    assert result["type"] == "abort"
    assert result["reason"] == "wrong_account"


# --- options flow -----------------------------------------------------------


async def test_options_flow(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="12345",
        data={CONF_EMAIL: "a@b.c", CONF_REFRESH_TOKEN: "rt", CONF_USER_ID: 12345},
        options={},
    )
    entry.add_to_hass(hass)
    with patch(
        "homeassistant.config_entries.ConfigEntries.async_schedule_reload"
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "delivered": {
                    CONF_DELIVERED_FILTER_TYPE: "parcels",
                    CONF_DELIVERED_FILTER_AMOUNT: 5,
                },
                "history": {CONF_INCLUDE_HISTORY: True},
                "polling": {CONF_REFRESH_INTERVAL: "120"},
            },
        )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_REFRESH_INTERVAL] == 120
    assert result["data"][CONF_INCLUDE_HISTORY] is True
    assert result["data"][CONF_DELIVERED_FILTER_AMOUNT] == 5
