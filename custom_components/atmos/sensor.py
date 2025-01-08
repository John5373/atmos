"""Atmos Energy Integration: sensor.py (Revised CSV Headers)"""

import logging
import requests
import csv
import io
import datetime
from bs4 import BeautifulSoup

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.helpers.event import async_track_point_in_time

from .const import DOMAIN, CONF_USERNAME, CONF_PASSWORD

_LOGGER = logging.getLogger(__name__)


def _get_next_4am():
    """
    Return a datetime object for the next occurrence of 4:00 AM local time.
    If you don't want once-per-day scheduling, remove or adjust this logic.
    """
    now = dt_util.now()
    target = now.replace(hour=4, minute=0, second=0, microsecond=0)
    if now >= target:
        target += datetime.timedelta(days=1)
    return target


class AtmosDailyCoordinator:
    """
    A custom coordinator that:
      - Logs in to Atmos
      - Downloads a CSV with columns:
        ["Temp Area", "Consumption", "Units", "Weather Date", "Avg Temp",
         "High Temp", "Low Temp", "Billing Month", "Billing Period"]
      - Parses out daily usage from "Consumption"
      - Treats "Weather Date" as the date for skip-duplicate logic
      - Tracks daily usage & cumulative usage
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.hass = hass
        self.entry = entry
        self.data = None  # Will hold the most recent row parsed from the CSV
        self._unsub_timer = None

        # Daily usage from "Consumption" + a running total
        self.current_daily_usage = 0.0
        self.cumulative_usage = 0.0

    def schedule_daily_update(self):
        """Schedule next update at 4:00 AM local time."""
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None

        next_time = _get_next_4am()
        _LOGGER.debug("Scheduling next Atmos update at %s", next_time)

        self._unsub_timer = async_track_point_in_time(
            self.hass, self._scheduled_update_callback, next_time
        )

    async def _scheduled_update_callback(self, now):
        """Callback at 4 AM to refresh data, then reschedule."""
        _LOGGER.debug("Running daily Atmos update at %s", now)
        await self.async_request_refresh()
        self.schedule_daily_update()

    async def async_request_refresh(self):
        """
        Fetches new data asynchronously (runs _fetch_data in the executor).
        Skips updating if we detect the same date as last time.
        """
        try:
            new_data = await self.hass.async_add_executor_job(self._fetch_data)
            if not new_data:
                _LOGGER.warning("No data returned from Atmos fetch.")
                return

            new_date = new_data.get("weather_date")
            old_date = self.data["weather_date"] if self.data else None

            # Skip if the date is repeated (no new data)
            if old_date and old_date == new_date:
                _LOGGER.info(
                    "Atmos data not updated (same date: %s). Skipping usage update.",
                    new_date
                )
                return

            # Update the coordinator data
            self.data = new_data

            # Convert Consumption to float for daily usage
            usage_str = new_data.get("consumption", "0").replace(",", "")
            try:
                usage_float = float(usage_str)
            except ValueError:
                usage_float = 0.0

            # Update daily usage
            self.current_daily_usage = usage_float

            # Update cumulative
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
        1) Logs into Atmos
        2) Downloads the CSV
        3) Parses the last row to return a dict with keys:
           {
             "weather_date": ...,
             "consumption": ...,
             "temp_area": ...,
             "units": ...,
             "avg_temp": ...,
             "high_temp": ...,
             "low_temp": ...,
             "billing_month": ...,
             "billing_period": ...
           }
        """

        username = self.entry.data.get(CONF_USERNAME)
        password = self.entry.data.get(CONF_PASSWORD)

        session = requests.Session()

        # 1. Login
        login_url = "https://www.atmosenergy.com/accountcenter/logon/authenticate.html"
        resp_get = session.get(login_url)
        resp_get.raise_for_status()

        soup = BeautifulSoup(resp_get.text, "html.parser")
        # If there's a hidden token, parse it here

        payload = {
            "username": username,
            "password": password,
            # "csrf_token": ...
        }

        post_resp = session.post(login_url, data=payload)

        print("DEBUG - Authentication Response Status:", post_resp.status_code)
        print("DEBUG - Authentication Response Headers:", post_resp.headers)
        print("DEBUG - Authentication Response Text:\n", post_resp.text)

        post_resp.raise_for_status()

        if "Logout" not in post_resp.text and "Sign Out" not in post_resp.text:
            _LOGGER.warning("Atmos login may have failed. Check credentials or site changes.")

        # 2. Build CSV download URL with a dynamic timestamp
        now = datetime.datetime.now()
        timestamp_str = now.strftime("%m%d%Y%H:%M:%S")
        csv_url = (
            "https://www.atmosenergy.com/accountcenter/usagehistory/"
            f"dailyUsageDownload.html?&billingPeriod=Current&{timestamp_str}"
        )

        csv_resp = session.get(csv_url)
        csv_resp.raise_for_status()

        # 3. Parse the CSV in-memory
        csv_file = io.StringIO(csv_resp.text)
        reader = csv.DictReader(csv_file)

        rows = list(reader)
        if not rows:
            _LOGGER.warning("No rows found in the daily usage CSV.")
            return None

        # Assume the last row is the most recent
        latest_row = rows[-1]

        # Extract each field, or use a default if missing
        temp_area_val = latest_row.get("Temp Area", "").strip()
        consumption_val = latest_row.get("Consumption", "").strip()
        units_val = latest_row.get("Units", "").strip()
        weather_date_val = latest_row.get("Weather Date", "").strip()
        avg_temp_val = latest_row.get("Avg Temp", "").strip()
        high_temp_val = latest_row.get("High Temp", "").strip()
        low_temp_val = latest_row.get("Low Temp", "").strip()
        billing_month_val = latest_row.get("Billing Month", "").strip()
        billing_period_val = latest_row.get("Billing Period", "").strip()

        return {
            "temp_area": temp_area_val,
            "consumption": consumption_val,
            "units": units_val,
            "weather_date": weather_date_val,
            "avg_temp": avg_temp_val,
            "high_temp": high_temp_val,
            "low_temp": low_temp_val,
            "billing_month": billing_month_val,
            "billing_period": billing_period_val,
        }


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """
    Called from __init__.py after the config entry is set up.
    Creates two sensors:
      - Daily Usage Sensor
      - Cumulative Usage Sensor
    """
    integration_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: AtmosDailyCoordinator = integration_data["coordinator"]

    # Optionally do an initial refresh on startup
    await coordinator.async_request_refresh()

    entities = [
        AtmosEnergyDailyUsageSensor(coordinator, entry),
        AtmosEnergyCumulativeUsageSensor(coordinator, entry),
    ]
    async_add_entities(entities)


class AtmosEnergyDailyUsageSensor(SensorEntity):
    """Shows the most recent daily usage (Consumption)."""

    def __init__(self, coordinator: AtmosDailyCoordinator, entry: ConfigEntry):
        self.coordinator = coordinator
        self.entry = entry
        self._attr_name = "Atmos Energy Daily Usage"
        self._attr_unique_id = f"{entry.entry_id}-daily-usage"
        self._attr_icon = "mdi:gas-cylinder"
        self._attr_unit_of_measurement = "CCf"  # If "Units" is always Ccf, adjust as needed

        # Typically for Energy usage:
        self._attr_device_class = "gas"
        self._attr_state_class = "total"

    @property
    def native_value(self):
        """Returns today's usage as float."""
        return self.coordinator.current_daily_usage

    @property
    def extra_state_attributes(self):
        """
        Expose other columns from the CSV so you can view them in HA:
         - weather_date, temp_area, units, avg_temp, high_temp, low_temp,
           billing_month, billing_period
        """
        data = self.coordinator.data
        if not data:
            return {}

        return {
            "weather_date": data.get("weather_date"),
            "temp_area": data.get("temp_area"),
            "units": data.get("units"),
            "avg_temp": data.get("avg_temp"),
            "high_temp": data.get("high_temp"),
            "low_temp": data.get("low_temp"),
            "billing_month": data.get("billing_month"),
            "billing_period": data.get("billing_period"),
        }

    @property
    def should_poll(self):
        """Disable polling; coordinator updates the sensor."""
        return False


class AtmosEnergyCumulativeUsageSensor(SensorEntity, RestoreEntity):
    """
    A cumulative sensor that sums each day's "Consumption".
    Perfect for the Energy Dashboard (device_class = "gas", state_class = "total_increasing").
    Restores its value across restarts via RestoreEntity.
    """

    def __init__(self, coordinator: AtmosDailyCoordinator, entry: ConfigEntry):
        self.coordinator = coordinator
        self.entry = entry
        self._attr_name = "Atmos Energy Cumulative Usage"
        self._attr_unique_id = f"{entry.entry_id}-cumulative-usage"
        self._attr_icon = "mdi:counter"
        self._attr_unit_of_measurement = "Ccf"
        self._attr_device_class = "gas"
        self._attr_state_class = "total_increasing"

    async def async_added_to_hass(self):
        """Restore previous cumulative value from DB on startup."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state and last_state.state is not None:
            try:
                old_val = float(last_state.state)
                self.coordinator.cumulative_usage = old_val
                _LOGGER.debug("Restored cumulative usage to %s", old_val)
            except ValueError:
                _LOGGER.warning("Could not parse old state '%s' as float", last_state.state)

        self.async_write_ha_state()

    @property
    def native_value(self):
        """Return the cumulative usage so far."""
        return self.coordinator.cumulative_usage

    @property
    def extra_state_attributes(self):
        """Optionally expose details from the latest CSV row."""
        data = self.coordinator.data
        if not data:
            return {}
        return {
            "latest_day": data.get("weather_date"),
            "temp_area": data.get("temp_area"),
            "units": data.get("units"),
            "avg_temp": data.get("avg_temp"),
            "high_temp": data.get("high_temp"),
            "low_temp": data.get("low_temp"),
            "billing_month": data.get("billing_month"),
            "billing_period": data.get("billing_period"),
        }

    @property
    def should_poll(self):
        return False
