import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Set up Atmos Energy from a config entry.
    This is called at Home Assistant startup for each entry.
    """
    # We can set up any shared data or services here, or use a DataUpdateCoordinator in sensor.py
    hass.data.setdefault(DOMAIN, {})
    
    # For example, store config entry in hass.data
    hass.data[DOMAIN][entry.entry_id] = {}

    # Forward the entry setup to the sensor platform
    hass.async_create_task(
        hass.config_entries.async_forward_entry_setup(entry, "sensor")
    )
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Unload sensor platform
    unload_ok = await hass.config_entries.async_forward_entry_unload(entry, "sensor")
    
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    
    return unload_ok
