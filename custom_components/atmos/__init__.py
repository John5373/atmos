"""Atmos Energy Integration: __init__.py"""

import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .sensor import AtmosDailyCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Set up Atmos Energy from a config entry.
    This is called at Home Assistant startup for each entry.
    """
    hass.data.setdefault(DOMAIN, {})
    # Create our daily coordinator
    coordinator = AtmosDailyCoordinator(hass, entry)
    
    # Store the coordinator so we can access/cancel it later
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator
    }

    # Forward the entry setup to the sensor platform
    hass.async_create_task(
        hass.config_entries.async_forward_entry_setup(entry, "sensor")
    )

    # Schedule the first daily update (this sets the callback for 4 AM)
    coordinator.schedule_daily_update()

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Retrieve our coordinator from hass.data
    integration_data = hass.data[DOMAIN].get(entry.entry_id)
    coordinator: AtmosDailyCoordinator | None = None
    if integration_data:
        coordinator = integration_data.get("coordinator")

    # Cancel the daily timer if it exists
    if coordinator and coordinator._unsub_timer:
        coordinator._unsub_timer()
        coordinator._unsub_timer = None

    # Unload the sensor platform
    unload_ok = await hass.config_entries.async_forward_entry_unload(entry, "sensor")
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
