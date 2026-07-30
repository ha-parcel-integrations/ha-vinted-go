"""Tests for Vinted Go diagnostics."""
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

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["counts"]["incoming_active"] == 1
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
