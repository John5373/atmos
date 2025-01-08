import logging
import requests
from bs4 import BeautifulSoup
from datetime import timedelta, datetime

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.event import async_track_point_in_time

from .const import DOMAIN, CONF_USERNAME, CONF_PASSWORD

_LOGGER = logging.getLogger(__name__)

def _get_next_4am() -> datetime:
    # ... same as before ...
    now = dt_util.now()
    target = now.replace(hour=4, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return target

class AtmosDailyCoordinator:
    """
    Same daily logic as before, including:
      - schedule_daily_update()
      - async_request_refresh()
      - _fetch_atmos_usage()
      - skipping repeated dates
    """
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.hass = hass
        self.entry = entry
        self.data = None
        self.current_daily_usage = 0.0
        self.cumulative_usage = 0.0
        self._unsub_timer = None

    # schedule_daily_update, _scheduled_update_callback, async_request_refresh, _fetch_atmos_usage
    # same as before, with your skip-if-date-repeats logic

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Creates two sensors: Daily Usage and Cumulative Usage."""
    integration_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: AtmosDailyCoordinator = integration_data["coordinator"]

    # Force an initial refresh so we have data on startup
    await coordinator.async_request_refresh()

    entities = [
        AtmosEnergyDailyUsageSensor(coordinator, entry),
        AtmosEnergyCumulativeUsageSensor(coordinator, entry),  # updated class below
    ]
    async_add_entities(entities)


class AtmosEnergyDailyUsageSensor(SensorEntity):
    """
    Same as before, shows just the latest day's usage.
    Not inheriting from RestoreEntity since we only care about the immediate daily value.
    """
    def __init__(self, coordinator: AtmosDailyCoordinator, entry: ConfigEntry):
        self.coordinator = coordinator
        self.entry = entry
        self._attr_name = "Atmos Energy Daily Usage"
        self._attr_unique_id = f"{entry.entry_id}-daily-usage"
        self._attr_unit_of_measurement = "Ccf"  # or ft³, Therms, etc.
        self._attr_icon = "mdi:gas-cylinder"

        # For the Energy Dashboard, though typically you'd only use the cumulative sensor:
        self._attr_device_class = "gas"
        self._attr_state_class = "measurement"

    @property
    def native_value(self):
        return self.coordinator.current_daily_usage

    @property
    def extra_state_attributes(self):
        if not self.coordinator.data:
            return {}
        return {
            "date": self.coordinator.data.get("date"),
            "cost": self.coordinator.data.get("cost"),
        }

    @property
    def should_poll(self):
        return False


class AtmosEnergyCumulativeUsageSensor(SensorEntity, RestoreEntity):
    """
    A sensor that keeps a running total of daily usage and RESTORES it on restart.
    Perfect for the Energy Dashboard (device_class = "gas", state_class = "total_increasing").
    """

    def __init__(self, coordinator: AtmosDailyCoordinator, entry: ConfigEntry):
        self.coordinator = coordinator
        self.entry = entry

        self._attr_name = "Atmos Energy Cumulative Usage"
        self._attr_unique_id = f"{entry.entry_id}-cumulative-usage"
        self._attr_icon = "mdi:counter"
        self._attr_unit_of_measurement = "Ccf"  # or ft³, Therms, etc.

        # Key fields for the Energy Dashboard
        self._attr_device_class = "gas"
        self._attr_state_class = "total_increasing"

    async def async_added_to_hass(self):
        """
        Called when the entity is added to hass.
        We use RestoreEntity to restore the last known state from HA's database.
        """
        await super().async_added_to_hass()

        # Attempt to restore the previous state from the database
        last_state = await self.async_get_last_state()

        if last_state and last_state.state is not None:
            try:
                old_val = float(last_state.state)
                _LOGGER.debug(
                    "Restoring cumulative usage for %s to %s (from old state).",
                    self._attr_name, old_val
                )
                # Update coordinator's usage so it doesn't reset to 0
                self.coordinator.cumulative_usage = old_val
            except ValueError:
                _LOGGER.warning(
                    "Could not parse old state '%s' as float for %s",
                    last_state.state, self._attr_name
                )

        # Force a new write to state machine so we show the restored value
        self.async_write_ha_state()

    @property
    def native_value(self):
        """
        Return the running total usage. 
        This is now persisted across restarts thanks to RestoreEntity.
        """
        return self.coordinator.cumulative_usage

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        if not data:
            return {}
        return {
            "latest_day": data.get("date"),
            "latest_usage": self.coordinator.current_daily_usage,
        }

    @property
    def should_poll(self):
        return False
import logging
import requests
from bs4 import BeautifulSoup
from datetime import timedelta, datetime

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.event import async_track_point_in_time

from .const import DOMAIN, CONF_USERNAME, CONF_PASSWORD

_LOGGER = logging.getLogger(__name__)

def _get_next_4am() -> datetime:
    # ... same as before ...
    now = dt_util.now()
    target = now.replace(hour=4, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return target

class AtmosDailyCoordinator:
    """
    Same daily logic as before, including:
      - schedule_daily_update()
      - async_request_refresh()
      - _fetch_atmos_usage()
      - skipping repeated dates
    """
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.hass = hass
        self.entry = entry
        self.data = None
        self.current_daily_usage = 0.0
        self.cumulative_usage = 0.0
        self._unsub_timer = None

    # schedule_daily_update, _scheduled_update_callback, async_request_refresh, _fetch_atmos_usage
    # same as before, with your skip-if-date-repeats logic

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Creates two sensors: Daily Usage and Cumulative Usage."""
    integration_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: AtmosDailyCoordinator = integration_data["coordinator"]

    # Force an initial refresh so we have data on startup
    await coordinator.async_request_refresh()

    entities = [
        AtmosEnergyDailyUsageSensor(coordinator, entry),
        AtmosEnergyCumulativeUsageSensor(coordinator, entry),  # updated class below
    ]
    async_add_entities(entities)


class AtmosEnergyDailyUsageSensor(SensorEntity):
    """
    Same as before, shows just the latest day's usage.
    Not inheriting from RestoreEntity since we only care about the immediate daily value.
    """
    def __init__(self, coordinator: AtmosDailyCoordinator, entry: ConfigEntry):
        self.coordinator = coordinator
        self.entry = entry
        self._attr_name = "Atmos Energy Daily Usage"
        self._attr_unique_id = f"{entry.entry_id}-daily-usage"
        self._attr_unit_of_measurement = "Ccf"  # or ft³, Therms, etc.
        self._attr_icon = "mdi:gas-cylinder"

        # For the Energy Dashboard, though typically you'd only use the cumulative sensor:
        self._attr_device_class = "gas"
        self._attr_state_class = "measurement"

    @property
    def native_value(self):
        return self.coordinator.current_daily_usage

    @property
    def extra_state_attributes(self):
        if not self.coordinator.data:
            return {}
        return {
            "date": self.coordinator.data.get("date"),
            "cost": self.coordinator.data.get("cost"),
        }

    @property
    def should_poll(self):
        return False


class AtmosEnergyCumulativeUsageSensor(SensorEntity, RestoreEntity):
    """
    A sensor that keeps a running total of daily usage and RESTORES it on restart.
    Perfect for the Energy Dashboard (device_class = "gas", state_class = "total_increasing").
    """

    def __init__(self, coordinator: AtmosDailyCoordinator, entry: ConfigEntry):
        self.coordinator = coordinator
        self.entry = entry

        self._attr_name = "Atmos Energy Cumulative Usage"
        self._attr_unique_id = f"{entry.entry_id}-cumulative-usage"
        self._attr_icon = "mdi:counter"
        self._attr_unit_of_measurement = "Ccf"  # or ft³, Therms, etc.

        # Key fields for the Energy Dashboard
        self._attr_device_class = "gas"
        self._attr_state_class = "total_increasing"

    async def async_added_to_hass(self):
        """
        Called when the entity is added to hass.
        We use RestoreEntity to restore the last known state from HA's database.
        """
        await super().async_added_to_hass()

        # Attempt to restore the previous state from the database
        last_state = await self.async_get_last_state()

        if last_state and last_state.state is not None:
            try:
                old_val = float(last_state.state)
                _LOGGER.debug(
                    "Restoring cumulative usage for %s to %s (from old state).",
                    self._attr_name, old_val
                )
                # Update coordinator's usage so it doesn't reset to 0
                self.coordinator.cumulative_usage = old_val
            except ValueError:
                _LOGGER.warning(
                    "Could not parse old state '%s' as float for %s",
                    last_state.state, self._attr_name
                )

        # Force a new write to state machine so we show the restored value
        self.async_write_ha_state()

    @property
    def native_value(self):
        """
        Return the running total usage. 
        This is now persisted across restarts thanks to RestoreEntity.
        """
        return self.coordinator.cumulative_usage

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        if not data:
            return {}
        return {
            "latest_day": data.get("date"),
            "latest_usage": self.coordinator.current_daily_usage,
        }

    @property
    def should_poll(self):
        return False
