"""The AtmosEnergy integration."""
import logging

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)
DOMAIN = "atmosenergy"

SENSORS = []

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the AtmosEnergy component."""
    async def handle_update(call: ServiceCall):
        _LOGGER.debug("Manual update service called")
        for sensor in SENSORS:
            sensor.update()
            sensor.async_write_ha_state()
    hass.services.async_register(DOMAIN, "update", handle_update)
    return True

async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up AtmosEnergy from a config entry."""
    await hass.config_entries.async_forward_entry_setups(config_entry, ["sensor"])
    return True

async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Unload an AtmosEnergy config entry."""
    return await hass.config_entries.async_unload_platforms(config_entry, ["sensor"])
