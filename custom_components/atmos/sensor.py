"""Atmos Energy Integration: sensor.py"""

import logging
import requests
from bs4 import BeautifulSoup
from datetime import timedelta, datetime

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.helpers.event import async_track_point_in_time

from .const import DOMAIN, CONF_USERNAME, CONF_PASSWORD

_LOGGER = logging.getLogger(__name__)


def _get_next_4am() -> datetime:
    """
    Return a datetime object for the next occurrence of 4:00 AM local time.
    Adjust if you want a different schedule.
    """
    now = dt_util.now()
    target = now.replace(hour=4, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return target


class AtmosDailyCoordinator:
    """
    A custom coordinator that fetches usage data once per day at 4 AM (if desired),
    stores the last retrieved data, and provides an `async_request_refresh` method.
    Also includes logic to skip repeated dates (so we don't double-count).
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.hass = hass
        self.entry = entry
        self.data = None  # Will hold the latest dict from _fetch_data: {"date":..., "usage":..., "cost":...}
        self._unsub_timer = None

        # You can store daily usage and cumulative usage here:
        self.current_daily_usage = 0.0
        self.cumulative_usage = 0.0

    def schedule_daily_update(self):
        """
        Schedule the next daily update at 4:00 AM local time.
        If you don't want once-a-day scheduling, you can omit this
        and rely on manual calls or some other logic.
        """
        if self._unsub_timer:
            # Cancel any existing timer to avoid duplicates
            self._unsub_timer()
            self._unsub_timer = None

        next_time = _get_next_4am()
        _LOGGER.debug("Scheduling next Atmos update at %s", next_time)

        self._unsub_timer = async_track_point_in_time(
            self.hass,
            self._scheduled_update_callback,
            next_time
        )

    async def _scheduled_update_callback(self, now):
        """
        Callback that runs daily at 4 AM, triggers a data refresh,
        then re-schedules for the next day.
        """
        _LOGGER.debug("Running daily Atmos update at %s", now)
        await self.async_request_refresh()
        self.schedule_daily_update()

    async def async_request_refresh(self):
        """
        Manually fetch new usage data. Called by sensor setup or a service or daily schedule.
        This must be `async` so we can do `await ...`.
        """
        try:
            new_data = await self.hass.async_add_executor_job(self._fetch_data)
            if not new_data:
                _LOGGER.warning("No data returned from Atmos fetch.")
                return

            new_date = new_data.get("date")
            old_date = self.data.get("date") if self.data else None

            # --- Skip logic if date is the same as the last known one ---
            if old_date and old_date == new_date:
                _LOGGER.info(
                    "Atmos data not updated (same date: %s). Skipping usage update.",
                    new_date
                )
                return

            # It's a new date, so we can proceed
            self.data = new_data

            # Parse usage as float
            usage_str = new_data.get("usage", "0").replace(",", "")
            try:
                usage_float = float(usage_str)
            except ValueError:
                usage_float = 0.0

            # Update the daily usage
            self.current_daily_usage = usage_float

            # Add to our cumulative usage
            self.cumulative_usage += usage_float

            _LOGGER.debug(
                "Fetched new data: date=%s, usage=%s, cumulative=%s",
                new_date, usage_float, self.cumulative_usage
            )

        except Exception as err:
            _LOGGER.error("Error refreshing Atmos data: %s", err)
            raise UpdateFailed from err

    def _fetch_data(self):
        """
        The synchronous logic to log in and scrape Atmos usage.
        Return a dict like {"date":..., "usage":..., "cost":...} or None on error.
        """
        username = self.entry.data.get(CONF_USERNAME)
        password = self.entry.data.get(CONF_PASSWORD)

        # Adjust these URLs/form fields for Atmos's actual site
        login_url = "https://www.atmosenergy.com/accountcenter/login"
        usage_url = "https://www.atmosenergy.com/accountcenter/usage"

        session = requests.Session()

        # 1) Get login page (potentially parse a CSRF token if needed)
        resp = session.get(login_url)
        resp.raise_for_status()

        # 2) Submit credentials
        payload = {
            "username": username,
            "password": password,
        }
        login_resp = session.post(login_url, data=payload)
        login_resp.raise_for_status()

        if "Logout" not in login_resp.text and "Sign Out" not in login_resp.text:
            _LOGGER.warning("Atmos login may have failed. Check credentials or site changes.")

        # 3) Go to usage page
        usage_resp = session.get(usage_url)
        usage_resp.raise_for_status()

        soup = BeautifulSoup(usage_resp.text, "html.parser")
        table = soup.find("table", {"class": "usage-table"})
        if not table:
            _LOGGER.error("Could not find usage table on the usage page.")
            return None

        rows = table.find_all("tr")
        if len(rows) < 2:
            _LOGGER.warning("No data rows in usage table.")
            return None

        # Example: parse the first row after the header
        latest_row = rows[1]
        cols = latest_row.find_all("td")
        if len(cols) < 3:
            _LOGGER.warning("Unexpected row format.")
            return None

        date_val = cols[0].get_text(strip=True)
        usage_val = cols[1].get_text(strip=True)
        cost_val = cols[2].get_text(strip=True)

        return {
            "date": date_val,
            "usage": usage_val,
            "cost": cost_val
        }


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """
    Called from __init__.py after the config entry is set up.
    Creates two sensors:
      1) Daily Usage Sensor
      2) Cumulative Usage Sensor
    """
    # Get the coordinator from hass.data
    integration_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: AtmosDailyCoordinator = integration_data["coordinator"]

    # Optional: Force an immediate refresh so sensors have data right away
    await coordinator.async_request_refresh()

    entities = [
        AtmosEnergyDailyUsageSensor(coordinator, entry),
        AtmosEnergyCumulativeUsageSensor(coordinator, entry),
    ]
    async_add_entities(entities)


class AtmosEnergyDailyUsageSensor(SensorEntity):
    """
    Shows the *latest* daily usage in Ccf (or Therms, etc.).
    """

    def __init__(self, coordinator: AtmosDailyCoordinator, entry: ConfigEntry):
        self.coordinator = coordinator
        self.entry = entry
        self._attr_name = "Atmos Energy Daily Usage"
        self._attr_unique_id = f"{entry.entry_id}-daily-usage"
        self._attr_unit_of_measurement = "Ccf"  # or "Therms", "ft³", etc.
        self._attr_icon = "mdi:gas-cylinder"

        # Optional: for the Energy Dashboard (though cumulative is typically used)
        self._attr_device_class = "gas"
        self._attr_state_class = "measurement"

    @property
    def native_value(self):
        """Return the last daily usage."""
        return self.coordinator.current_daily_usage

    @property
    def extra_state_attributes(self):
        """Expose the date/cost from the coordinator's data dict."""
        if not self.coordinator.data:
            return {}
        return {
            "date": self.coordinator.data.get("date"),
            "cost": self.coordinator.data.get("cost"),
        }

    @property
    def should_poll(self):
        """Disable polling; coordinator handles updates."""
        return False


class AtmosEnergyCumulativeUsageSensor(SensorEntity, RestoreEntity):
    """
    A *cumulative* sensor that sums each new day of usage,
    persisting across HA restarts via RestoreEntity.
    This is typically used for the Energy Dashboard.
    """

    def __init__(self, coordinator: AtmosDailyCoordinator, entry: ConfigEntry):
        self.coordinator = coordinator
        self.entry = entry
        self._attr_name = "Atmos Energy Cumulative Usage"
        self._attr_unique_id = f"{entry.entry_id}-cumulative-usage"
        self._attr_icon = "mdi:counter"
        self._attr_unit_of_measurement = "Ccf"

        # Required for gas consumption in the Energy Dashboard:
        self._attr_device_class = "gas"
        self._attr_state_class = "total_increasing"

    async def async_added_to_hass(self):
        """
        When the entity is added, try to restore the old state
        so we keep the cumulative value even after restarts.
        """
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state and last_state.state is not None:
            try:
                old_val = float(last_state.state)
                self.coordinator.cumulative_usage = old_val
                _LOGGER.debug(
                    "Restored cumulative usage to %s for %s",
                    old_val, self._attr_unique_id
                )
            except ValueError:
                _LOGGER.warning(
                    "Could not parse old state %s as float for %s",
                    last_state.state, self._attr_unique_id
                )

        # Force an immediate state update so it shows in HA
        self.async_write_ha_state()

    @property
    def native_value(self):
        """Return the running total usage (which is persisted via RestoreEntity)."""
        return self.coordinator.cumulative_usage

    @property
    def extra_state_attributes(self):
        """Optionally expose additional info (like the latest day's date, usage, cost)."""
        if not self.coordinator.data:
            return {}
        return {
            "latest_day": self.coordinator.data.get("date"),
            "latest_usage": self.coordinator.current_daily_usage,
        }

    @property
    def should_poll(self):
        """Disable polling. We rely on the coordinator's scheduling/refresh logic."""
        return False
