"""Atmos Energy Integration: sensor.py (Verbose Debug)"""

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

def _debug_response(prefix: str, response: requests.Response):
    """
    Helper function to print out verbose debug for a response object:
      - URL, status code
      - Headers
      - Body text (truncated if extremely large)
    """
    print(f"DEBUG ({prefix}) - URL: {response.url}")
    print(f"DEBUG ({prefix}) - Status Code: {response.status_code}")
    print(f"DEBUG ({prefix}) - Headers: {response.headers}")
    if len(response.text) > 3000:
        # Truncate very large responses to keep logs manageable
        print(f"DEBUG ({prefix}) - Body (truncated):\n{response.text[:3000]}...[TRUNCATED]...")
    else:
        print(f"DEBUG ({prefix}) - Body:\n{response.text}")

def _get_next_4am():
    """Return a datetime for the next occurrence of 4:00 AM local time."""
    now = dt_util.now()
    target = now.replace(hour=4, minute=0, second=0, microsecond=0)
    if now >= target:
        target += datetime.timedelta(days=1)
    return target

class AtmosDailyCoordinator:
    """
    Coordinator that:
      - Logs in to Atmos
      - Downloads a CSV with columns
      - Tracks daily usage (Consumption) & cumulative usage
      - Allows once-per-day scheduling at 4 AM
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.hass = hass
        self.entry = entry
        self.data = None
        self._unsub_timer = None

        self.current_daily_usage = 0.0
        self.cumulative_usage = 0.0

    def schedule_daily_update(self):
        """Schedule next update at 4:00 AM."""
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
        Fetch new data in an executor job. Logs verbose debug info.
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
        1) Logs in to Atmos
        2) Downloads a CSV
        3) Prints verbose debug for every response
        4) Parses the CSV -> returns last row as dict
        """
        username = self.entry.data.get(CONF_USERNAME)
        password = self.entry.data.get(CONF_PASSWORD)

        session = requests.Session()

        # 1) Initial GET (login page)
        login_url = "https://www.atmosenergy.com/accountcenter/logon/authenticate.html"
        resp_get = session.get(login_url)
        _debug_response("GET login page", resp_get)
        resp_get.raise_for_status()

        # 2) POST credentials
        payload = {
            "username": username,
            "password": password,
            # If there's a CSRF token, parse and add here
        }
        post_resp = session.post(login_url, data=payload)
        _debug_response("POST credentials", post_resp)
        post_resp.raise_for_status()

        if "Logout" not in post_resp.text and "Sign Out" not in post_resp.text:
            _LOGGER.warning("Atmos login may have failed. Check credentials or site changes.")

        # 3) Download CSV
        now = datetime.datetime.now()
        timestamp_str = now.strftime("%m%d%Y%H:%M:%S")
        csv_url = (
            "https://www.atmosenergy.com/accountcenter/usagehistory/"
            f"dailyUsageDownload.html?&billingPeriod=Current&{timestamp_str}"
        )
        csv_resp = session.get(csv_url)
        _debug_response("GET CSV", csv_resp)
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
    """Sets up sensors + registers a manual fetch service if needed."""
    integration_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: AtmosDailyCoordinator = integration_data["coordinator"]

    await coordinator.async_request_refresh()

    # Create sensors
    entities = [
        AtmosEnergyDailyUsageSensor(coordinator, entry),
        AtmosEnergyCumulativeUsageSensor(coordinator, entry),
    ]
    async_add_entities(entities)

    # Register the manual fetch service if you haven't already
    if not hass.services.has_service(DOMAIN, "fetch_now"):
        async def async_handle_fetch_now(call: ServiceCall):
            """Manual fetch service for all Atmos entries."""
            _LOGGER.info("Manual fetch_now service called for Atmos Energy.")
            for eid, data in hass.data[DOMAIN].items():
                c: AtmosDailyCoordinator = data["coordinator"]
                await c.async_request_refresh()
            _LOGGER.info("Manual fetch_now service complete for all Atmos entries.")

        hass.services.async_register(
            domain=DOMAIN,
            service="fetch_now",
            service_func=async_handle_fetch_now,
        )

class AtmosEnergyDailyUsageSensor(SensorEntity):
    """Shows the most recent daily usage (Consumption)."""

    def __init__(self, coordinator: AtmosDailyCoordinator, entry: ConfigEntry):
        self.coordinator = coordinator
        self.entry = entry
        self._attr_name = "Atmos Energy Daily Usage"
        self._attr_unique_id = f"{entry.entry_id}-daily-usage"
        self._attr_icon = "mdi:gas-cylinder"
        self._attr_native_unit_of_measurement = "CCF"  # "CCF", "ft³", "m³"
        self._attr_device_class = "gas"
        self._attr_state_class = "measurement"

    @property
    def native_value(self):
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
        return False

class AtmosEnergyCumulativeUsageSensor(SensorEntity, RestoreEntity):
    """Cumulative usage sensor (total_increasing) for the Energy Dashboard."""

    def __init__(self, coordinator: AtmosDailyCoordinator, entry: ConfigEntry):
        self.coordinator = coordinator
        self.entry = entry
        self._attr_name = "Atmos Energy Cumulative Usage"
        self._attr_unique_id = f"{entry.entry_id}-cumulative-usage"
        self._attr_icon = "mdi:counter"
        self._attr_native_unit_of_measurement = "CCF"  # "CCF", "ft³", "m³"
        self._attr_device_class = "gas"
        self._attr_state_class = "total_increasing"

    async def async_added_to_hass(self):
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state and last_state.state is not None:
            try:
                old_val = float(last_state.state)
                self.coordinator.cumulative_usage = old_val
                _LOGGER.debug("Restored cumulative usage to %s", old_val)
            except ValueError:
                _LOGGER.warning(
                    "Could not parse old state '%s' as float",
                    last_state.state
                )

        self.async_write_ha_state()

    @property
    def native_value(self):
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
