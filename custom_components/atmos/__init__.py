"""The AtmosEnergy integration."""
import logging
from datetime import datetime
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.event import async_track_time_change

_LOGGER = logging.getLogger(__name__)
DOMAIN = "atmosenergy"

# Global list to store sensor entities
SENSORS = []

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the AtmosEnergy component from YAML configuration if needed."""
    return True

async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up AtmosEnergy from a config entry."""
    # Forward the config entry setup to the sensor platform.
    await hass.config_entries.async_forward_entry_setups(config_entry, ["sensor"])

    # Define a callback to update all registered sensors.
    def scheduled_update(now):
        _LOGGER.debug("Scheduled update triggered at %s", now)
        for sensor in SENSORS:
            sensor.update()
            sensor.async_write_ha_state()

    # Schedule updates at 1:00 AM and 1:00 PM.
    async_track_time_change(hass, scheduled_update, hour=1, minute=0, second=0)
    async_track_time_change(hass, scheduled_update, hour=13, minute=0, second=0)

    async def handle_manual_update(call):
        _LOGGER.debug("Manual update service called")
        for sensor in SENSORS:
            sensor.update()
            sensor.async_write_ha_state()

    hass.services.async_register(DOMAIN, "update", handle_manual_update)
    return True

async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Unload an AtmosEnergy config entry."""
    return await hass.config_entries.async_unload_platforms(config_entry, ["sensor"])
