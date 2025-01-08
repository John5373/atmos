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
    
    # Create your daily coordinator (adjust if you have a different coordinator class)
    coordinator = AtmosDailyCoordinator(hass, entry)
    
    # Store it so you can unload it later
    hass.data[DOMAIN][entry.entry_id] = {"coordinator": coordinator}
    
    # Now forward the entry to the sensor platform using the recommended method
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    
    # Schedule the first daily update if you're doing once-per-day logic
    coordinator.schedule_daily_update()

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    integration_data = hass.data[DOMAIN].get(entry.entry_id)
    coordinator = integration_data.get("coordinator") if integration_data else None
    
    # Cancel daily timer if needed
    if coordinator and coordinator._unsub_timer:
        coordinator._unsub_timer()
        coordinator._unsub_timer = None

    # Unload the sensor platform
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor"])
    
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    
    return unload_ok
