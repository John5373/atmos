"""Atmos Energy Integration: sensor.py (With Manual Fetch Service)"""

import logging
import requests
import csv
import io
import datetime
from bs4 import BeautifulSoup

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.helpers.event import async_track_point_in_time

from .const import DOMAIN, CONF_USERNAME, CONF_PASSWORD

_LOGGER = logging.getLogger(__name__)


def _get_next_4am():
    """Return a datetime for the next occurrence of 4:00 AM local time."""
    now = dt_util.now()
    target = now.replace(hour=4, minute=0, second=0, microsecond=0)
    if now >= target:
        target += datetime.timedelta(days=1)
    return target


class AtmosDailyCoordinator:
    """
    A coordinator that:
      - Logs in to Atmos
      - Downloads a CSV with columns:
        ["Temp Area", "Consumption", "Units", "Weather Date", "Avg Temp",
         "High Temp", "Low Temp", "Billing Month", "Billing Period"]
      - Parses out daily usage (Consumption)
      - Uses "Weather Date" as the skip-duplicate logic
      - Maintains daily & cumulative usage
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.hass = hass
        self.entry = entry
        self.data = None  # e.g. {"weather_date":..., "consumption":..., etc.}
        self._unsub_timer = None

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
        Fetch new data in an executor job. Skip if the date hasn't changed.
        """
        try:
            new_data = await self.hass.async_add_executor_job(self._fetch_data)
            if not new_data:
                _LOGGER.warning("No data returned from Atmos fetch.")
                return

            new_date = new_data.get("weather_date")
            old_date = self.data["weather_date"] if self.data else None

            if old_date and old_date == new_date:
                _LOGGER.info(
                    "Atmos data not updated (same date: %s). Skipping usage update.",
                    new_date
                )
                return

            self.data = new_data

            usage_str = new_data.get("consumption", "0").replace(",", "")
            try:
                usage_float = float(usage_str)
            except ValueError:
                usage_float = 0.0

            self.current_daily_usage = usage_float
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
        Log in, download CSV, parse columns, return last row as dict.
        Adjust as needed for your actual CSV structure.
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
        }

        post_resp = session.post(login_url, data=payload)

        print("DEBUG - Authentication Response Status:", post_resp.status_code)
        print("DEBUG - Authentication Response Headers:", post_resp.headers)
        print("DEBUG - Authentication Response Text:\n", post_resp.text)

        post_resp.raise_for_status()

        if "Logout" not in post_resp.text and "Sign Out" not in post_resp.text:
            _LOGGER.warning("Atmos login may have failed. Check credentials or site changes.")

        # 2. Build CSV URL with timestamp
        now = datetime.datetime.now()
        timestamp_str = now.strftime("%m%d%Y%H:%M:%S")
        csv_url = (
            "https://www.atmosenergy.com/accountcenter/usagehistory/"
            f"dailyUsageDownload.html?&billingPeriod=Current&{timestamp_str}"
        )

        csv_resp = session.get(csv_url)
        csv_resp.raise_for_status()

        csv_file = io.StringIO(csv_resp.text)
        reader = csv.DictReader(csv_file)

        rows = list(reader)
        if not rows:
            _LOGGER.warning("No rows found in the daily usage CSV.")
            return None

        latest_row = rows[-1]
        return {
            "temp_area": latest_row.get("Temp Area", "").strip(),
            "consumption": latest_row.get("Consumption", "").strip(),
            "units": latest_row.get("Units", "").strip(),
            "weather_date": latest_row.get("Weather Date", "").strip(),
            "avg_temp": latest_row.get("Avg Temp", "").strip(),
            "high_temp": latest_row.get("High Temp", "").strip(),
            "low_temp": latest_row.get("Low Temp", "").strip(),
            "billing_month": latest_row.get("Billing Month", "").strip(),
            "billing_period": latest_row.get("Billing Period", "").strip(),
        }


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """
    Called from __init__.py after the config entry is set up.
    Creates two sensors + registers a manual fetch service.
    """
    integration_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: AtmosDailyCoordinator = integration_data["coordinator"]

    # 1) Optionally do an initial fetch on startup
    await coordinator.async_request_refresh()

    # 2) Register a service to manually fetch new data
    #    We'll call this "fetch_now". If you have multiple entries,
    #    this service will refresh them all.
    if not hass.services.has_service(DOMAIN, "fetch_now"):
        async def async_handle_fetch_now(call: ServiceCall):
            """Handle the manual 'fetch_now' service call."""
            _LOGGER.info("Manual fetch_now service called for Atmos Energy.")
            
            # Loop over all config entries for this domain
            for entry_id, data in hass.data[DOMAIN].items():
                c: AtmosDailyCoordinator = data["coordinator"]
                await c.async_request_refresh()

            _LOGGER.info("Manual fetch_now service complete for all Atmos entries.")

        hass.services.async_register(
            domain=DOMAIN,
            service="fetch_now",
            service_func=async_handle_fetch_now,
        )

    # 3) Create sensor entities
    entities = [
        AtmosEnergyDailyUsageSensor(coordinator, entry),
        AtmosEnergyCumulativeUsageSensor(coordinator, entry),
    ]
    async_add_entities(entities)


class AtmosEnergyDailyUsageSensor(SensorEntity):
    """Displays the most recent daily usage from the CSV (Consumption)."""

    def __init__(self, coordinator: AtmosDailyCoordinator, entry: ConfigEntry):
        self.coordinator = coordinator
        self.entry = entry
        self._attr_name = "Atmos Energy Daily Usage"
        self._attr_unique_id = f"{entry.entry_id}-daily-usage"
        self._attr_icon = "mdi:gas-cylinder"
        # Use an allowed unit for device_class = gas. Could be "CCF", "ft³", or "m³":
        self._attr_native_unit_of_measurement = "CCF"
        self._attr_device_class = "gas"
        self._attr_state_class = "total_increasing"

    @property
    def native_value(self):
        """Return today's usage as float."""
        return self.coordinator.current_daily_usage

    @property
    def extra_state_attributes(self):
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
        """Disable polling. The coordinator updates us."""
        return False


class AtmosEnergyCumulativeUsageSensor(SensorEntity, RestoreEntity):
    """
    A cumulative sensor that sums each day's Consumption.
    Suitable for the Energy Dashboard. Persists across restarts.
    """

    def __init__(self, coordinator: AtmosDailyCoordinator, entry: ConfigEntry):
        self.coordinator = coordinator
        self.entry = entry
        self._attr_name = "Atmos Energy Cumulative Usage"
        self._attr_unique_id = f"{entry.entry_id}-cumulative-usage"
        self._attr_icon = "mdi:counter"
        # Use an allowed unit for device_class=gas:
        self._attr_native_unit_of_measurement = "CCF"
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
        """Return the total usage so far."""
        return self.coordinator.cumulative_usage

    @property
    def extra_state_attributes(self):
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
