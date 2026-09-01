"""Tests for Vinted Go diagnostics."""
from datetime import timedelta
from unittest.mock import MagicMock

from custom_components.vinted_go.diagnostics import (
    async_get_config_entry_diagnostics,
)


async def test_diagnostics_redacts_and_counts(hass):
    """Diagnostics get pasted into public issues — nothing identifying survives."""
    entry = MagicMock()
    entry.data = {"email": "fam@example.com", "refresh_token": "secret", "user_id": 12345}
    entry.options = {"refresh_interval": 30}
    parcel = {
        "barcode": "VGS1",
        "status": "in_transit",
        "url": "https://vintedgo.com/en/tracking/VGS1",
        "raw": {
            "tracking_code": "VGS1",
            "content_title": "A dress",
            "resolved_pick_up_code": "1234",
            "point": {"name": "Central Station"},
        },
    }
    entry.runtime_data.coordinator.data = [parcel]
    entry.runtime_data.coordinator.delivered = []
    entry.runtime_data.coordinator.outgoing = []
    entry.runtime_data.coordinator.delivered_outgoing = []
    entry.runtime_data.coordinator.current_tier_minutes = 45
    entry.runtime_data.coordinator.update_interval = timedelta(minutes=45)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["counts"]["incoming_active"] == 1
    assert result["polling"]["current_tier_minutes"] == 45
    assert result["polling"]["update_interval_seconds"] == 45 * 60
    assert result["entry_data"]["email"] == "**REDACTED**"
    assert result["entry_data"]["refresh_token"] == "**REDACTED**"
    incoming = result["incoming"][0]
    assert incoming["barcode"] == "**REDACTED**"
    assert incoming["url"] == "**REDACTED**"
    assert incoming["raw"]["tracking_code"] == "**REDACTED**"
    assert incoming["raw"]["content_title"] == "**REDACTED**"
    assert incoming["raw"]["resolved_pick_up_code"] == "**REDACTED**"
    assert incoming["raw"]["point"] == "**REDACTED**"
    # non-identifying fields survive
    assert incoming["status"] == "in_transit"


async def test_diagnostics_polling_handles_no_update_interval(hass):
    """A fixed-interval entry has no current tier; ``update_interval`` can
    also be ``None`` (e.g. before the first successful refresh)."""
    entry = MagicMock()
    entry.data = {"email": "fam@example.com"}
    entry.options = {"refresh_interval": 30}
    entry.runtime_data.coordinator.data = []
    entry.runtime_data.coordinator.delivered = []
    entry.runtime_data.coordinator.outgoing = []
    entry.runtime_data.coordinator.delivered_outgoing = []
    entry.runtime_data.coordinator.current_tier_minutes = None
    entry.runtime_data.coordinator.update_interval = None

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["polling"]["current_tier_minutes"] is None
    assert result["polling"]["update_interval_seconds"] is None
