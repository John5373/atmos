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
    """Return a datetime object for the next occurrence of 4:00 AM local time."""
    now = dt_util.now()
    target = now.replace(hour=4, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return target


class AtmosDailyCoordinator:
    """
    Coordinator-like class that:
      - Fetches usage data (login + scrape)
      - Schedules once-per-day updates at 4 AM
      - Skips repeated dates
      - Tracks daily and cumulative usage
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.hass = hass
        self.entry = entry
        self.data = None  # Holds the latest fetched dict: {"date":..., "usage":..., "cost":...}
        self._unsub_timer = None

        # Separate fields for daily usage (most recent) & cumulative
        self.current_daily_usage = 0.0
        self.cumulative_usage = 0.0

    def schedule_daily_update(self):
        """Schedule the next daily update at 4:00 AM local time."""
        if self._unsub_timer:
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
        """Callback that triggers the daily data fetch, then reschedules."""
        _LOGGER.debug("Running daily Atmos update at %s", now)
        await self.async_request_refresh()
        self.schedule_daily_update()

    async def async_request_refresh(self):
        """
        Asynchronous method to fetch new data.
        Called from sensor setup, daily schedule, or a manual service.
        """
        try:
            new_data = await self.hass.async_add_executor_job(self._fetch_data)
            if not new_data:
                _LOGGER.warning("No data returned from Atmos fetch.")
                return

            new_date = new_data.get("date")
            old_date = self.data.get("date") if self.data else None

            # Skip if the date is repeated (i.e., site not updated yet)
            if old_date and old_date == new_date:
                _LOGGER.info(
                    "Atmos data not updated (same date: %s). Skipping usage update.",
                    new_date
                )
                return

            # It's a new date, so update self.data
            self.data = new_data

            # Parse usage as a float
            usage_str = new_data.get("usage", "0").replace(",", "")
            try:
                usage_float = float(usage_str)
            except ValueError:
                usage_float = 0.0

            # Update daily usage
            self.current_daily_usage = usage_float

            # Add to cumulative
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
        The synchronous login + scrape process.
        Returns {"date": "...", "usage": "...", "cost": "..."} or None on errors.
        """

        username = self.entry.data.get(CONF_USERNAME)
        password = self.entry.data.get(CONF_PASSWORD)

        # 1) The discovered authentication endpoint
        login_url = "https://www.atmosenergy.com/accountcenter/logon/authenticate.html"

        # If usage is under a different path, update accordingly
        usage_url = "https://www.atmosenergy.com/accountcenter/usage"

        session = requests.Session()

        # 2) First, do a GET to gather cookies / potential hidden fields
        initial_resp = session.get(login_url)
        initial_resp.raise_for_status()

        # If there's a hidden token, parse it here
        soup = BeautifulSoup(initial_resp.text, "html.parser")
        # Example: If you see <input name="authenticity_token" ...>
        # token_el = soup.find("input", {"name": "authenticity_token"})
        # csrf_token = token_el["value"] if token_el else ""

        # 3) Construct your POST payload (adjust field names to match DevTools)
        payload = {
            "username": username,
            "password": password,
            # If there's a CSRF token or other hidden fields, add them:
            # "authenticity_token": csrf_token
        }

        # 4) Send POST to authenticate
        login_resp = session.post(login_url, data=payload)
        login_resp.raise_for_status()

        if "Logout" not in login_resp.text and "Sign Out" not in login_resp.text:
            _LOGGER.warning("Atmos login may have failed. Check credentials or site changes.")

        # 5) After login, fetch the usage page
        usage_resp = session.get(usage_url)
        usage_resp.raise_for_status()

        soup_usage = BeautifulSoup(usage_resp.text, "html.parser")
        table = soup_usage.find("table", {"class": "usage-table"})
        if not table:
            _LOGGER.error("Could not find usage table on the usage page.")
            return None

        rows = table.find_all("tr")
        if len(rows) < 2:
            _LOGGER.warning("No data rows found in usage table.")
            return None

        # Example: parse the first row after the header
        latest_row = rows[1]
        cols = latest_row.find_all("td")
        if len(cols) < 3:
            _LOGGER.warning("Unexpected row format in usage table.")
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
      - Daily Usage Sensor
      - Cumulative Usage Sensor (for the Energy Dashboard)
    """
    integration_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: AtmosDailyCoordinator = integration_data["coordinator"]

    # Optionally, do an immediate fetch so sensors have data right away
    await coordinator.async_request_refresh()

    entities = [
        AtmosEnergyDailyUsageSensor(coordinator, entry),
        AtmosEnergyCumulativeUsageSensor(coordinator, entry),
    ]
    async_add_entities(entities)


class AtmosEnergyDailyUsageSensor(SensorEntity):
    """Shows the latest day's usage in Ccf (or Therms, etc.)."""

    def __init__(self, coordinator: AtmosDailyCoordinator, entry: ConfigEntry):
        self.coordinator = coordinator
        self.entry = entry
        self._attr_name = "Atmos Energy Daily Usage"
        self._attr_unique_id = f"{entry.entry_id}-daily-usage"
        self._attr_unit_of_measurement = "Ccf"  # or "Therms", "ft³", etc.
        self._attr_icon = "mdi:gas-cylinder"

        # These fields let it be recognized as a gas usage sensor,
        # though typically "total_increasing" is what's used for the Energy Dashboard
        self._attr_device_class = "gas"
        self._attr_state_class = "measurement"

    @property
    def native_value(self):
        """Return the daily usage from the coordinator."""
        return self.coordinator.current_daily_usage

    @property
    def extra_state_attributes(self):
        """Expose the date/cost from the coordinator.data."""
        if not self.coordinator.data:
            return {}
        return {
            "date": self.coordinator.data.get("date"),
            "cost": self.coordinator.data.get("cost"),
        }

    @property
    def should_poll(self):
        """No polling; coordinator handles updates."""
        return False


class AtmosEnergyCumulativeUsageSensor(SensorEntity, RestoreEntity):
    """
    A *cumulative* sensor that sums each day's usage.
    Persists across Home Assistant restarts by restoring previous state.
    Suitable for the Energy Dashboard.
    """

    def __init__(self, coordinator: AtmosDailyCoordinator, entry: ConfigEntry):
        self.coordinator = coordinator
        self.entry = entry
        self._attr_name = "Atmos Energy Cumulative Usage"
        self._attr_unique_id = f"{entry.entry_id}-cumulative-usage"
        self._attr_icon = "mdi:counter"
        self._attr_unit_of_measurement = "Ccf"

        # Required fields for the Energy Dashboard
        self._attr_device_class = "gas"
        self._attr_state_class = "total_increasing"

    async def async_added_to_hass(self):
        """
        Restore the last known state from the DB so we don't lose our running total on restart.
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
                    "Could not parse old state '%s' as float for %s",
                    last_state.state, self._attr_unique_id
                )

        self.async_write_ha_state()

    @property
    def native_value(self):
        """Return the running total usage from the coordinator."""
        return self.coordinator.cumulative_usage

    @property
    def extra_state_attributes(self):
        """Optionally expose the latest day's date or usage."""
        if not self.coordinator.data:
            return {}
        return {
            "latest_day": self.coordinator.data.get("date"),
            "latest_usage": self.coordinator.current_daily_usage,
        }

    @property
    def should_poll(self):
        """Disable polling in favor of coordinator-based updates."""
        return False
