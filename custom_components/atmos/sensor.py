"""Atmos Energy Integration: sensor.py (CSV Parsing Fix)"""

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


def _debug_request(prefix: str, method: str, url: str, **kwargs):
    """
    Helper function to log request details.
    """
    _LOGGER.debug("REQUEST [%s] - Method: %s, URL: %s", prefix, method, url)
    data = kwargs.get("data")
    if data:
        if isinstance(data, dict):
            masked_data = dict(data)
            if "password" in masked_data:
                masked_data["password"] = "********"
            _LOGGER.debug("REQUEST [%s] - Payload: %s", prefix, masked_data)
        else:
            _LOGGER.debug("REQUEST [%s] - Payload (raw): %s", prefix, data)
    headers = kwargs.get("headers")
    if headers:
        _LOGGER.debug("REQUEST [%s] - Headers: %s", prefix, headers)


def _debug_response(prefix: str, response: requests.Response, max_len=3000):
    """
    Helper function to log response details.
    """
    _LOGGER.debug("RESPONSE [%s] - URL: %s", prefix, response.url)
    _LOGGER.debug("RESPONSE [%s] - Status Code: %s", prefix, response.status_code)
    _LOGGER.debug("RESPONSE [%s] - Headers: %s", prefix, response.headers)
    body = response.text
    if len(body) > max_len:
        _LOGGER.debug("RESPONSE [%s] - Body (truncated):\n%s...[TRUNCATED]...", prefix, body[:max_len])
    else:
        _LOGGER.debug("RESPONSE [%s] - Body:\n%s", prefix, body)


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
      - Downloads the CSV with usage data
      - Parses the CSV to extract the latest row
      - Tracks daily and cumulative usage
      - Supports a daily auto-refresh (e.g., at 4 AM)
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.hass = hass
        self.entry = entry
        self.data = None
        self._unsub_timer = None

        self.current_daily_usage = 0.0
        self.cumulative_usage = 0.0

    def schedule_daily_update(self):
        """Schedule the next update at 4:00 AM local time."""
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
        """Fetch new data asynchronously."""
        try:
            new_data = await self.hass.async_add_executor_job(self._fetch_data)
            if not new_data:
                _LOGGER.warning("No data returned from Atmos fetch.")
                return

            # If the new weather_date is same as before, skip updating to prevent double-counting.
            new_date = new_data.get("weather_date")
            old_date = self.data["weather_date"] if self.data else None
            if old_date and old_date == new_date:
                _LOGGER.info("Atmos data not updated (same date: %s). Skipping usage update.", new_date)
                return

            self.data = new_data

            # Retrieve consumption as a number
            usage_value = new_data.get("consumption")
            if usage_value == "" or usage_value is None:
                _LOGGER.warning("The 'Consumption' field is empty in the CSV; defaulting usage to 0.0")
                usage_float = 0.0
            else:
                try:
                    usage_float = float(str(usage_value).replace(",", "").strip())
                except ValueError:
                    _LOGGER.warning("Could not convert 'Consumption' value '%s' to float.", usage_value)
                    usage_float = 0.0

            self.current_daily_usage = usage_float
            self.cumulative_usage += usage_float

            _LOGGER.debug("Fetched new data: date=%s, usage=%s, cumulative=%s",
                          new_data.get("weather_date"), usage_float, self.cumulative_usage)

        except Exception as err:
            _LOGGER.error("Error refreshing Atmos data: %s", err)
            raise UpdateFailed from err

    def _fetch_data(self):
        """
        Logs in to Atmos, downloads the CSV, and parses the latest row.
        Returns a dictionary with keys:
        "weather_date", "consumption", "temp_area", "units", "avg_temp",
        "high_temp", "low_temp", "billing_month", "billing_period"
        """
        username = self.entry.data.get(CONF_USERNAME)
        password = self.entry.data.get(CONF_PASSWORD)

        session = requests.Session()

        # --- Step 1: Login to Atmos ---
        login_url = "https://www.atmosenergy.com/accountcenter/logon/authenticate.html"
        _debug_request("GET login page", "GET", login_url)
        resp_get = session.get(login_url)
        _debug_response("GET login page", resp_get)
        resp_get.raise_for_status()

        # --- Step 2: POST credentials ---
        payload = {
            "username": username,
            "password": password,
        }
        _debug_request("POST credentials", "POST", login_url, data=payload)
        post_resp = session.post(login_url, data=payload)
        _debug_response("POST credentials", post_resp)
        post_resp.raise_for_status()

        if post_resp.status_code == 200 and ("Logout" in post_resp.text or "Sign Out" in post_resp.text):
            _LOGGER.info("Atmos login successful.")
        else:
            _LOGGER.warning("Atmos login may have failed. Check credentials or site changes.")

        # --- Step 3: Download CSV ---
        now = datetime.datetime.now()
        timestamp_str = now.strftime("%m%d%Y%H:%M:%S")
        csv_url = ("https://www.atmosenergy.com/accountcenter/usagehistory/dailyUsageDownload.html"
                   f"?&billingPeriod=Current&{timestamp_str}")
        _debug_request("GET CSV", "GET", csv_url)
        csv_resp = session.get(csv_url)
        _debug_response("GET CSV", csv_resp)
        csv_resp.raise_for_status()

        # --- Step 4: Parse CSV ---
        csv_file = io.StringIO(csv_resp.text)
        reader = csv.DictReader(csv_file)
        rows = list(reader)

        if not rows:
            _LOGGER.warning("No rows found in the CSV file.")
            return None

        latest_row = rows[-1]  # Assume the last row is the most recent
        _LOGGER.debug("Parsed CSV last row: %s", latest_row)

        return {
            "weather_date": latest_row.get("Weather Date", "").strip(),
            "consumption": latest_row.get("Consumption", "").strip(),
            "temp_area": latest_row.get("Temp Area", "").strip(),
            "units": latest_row.get("Units", "").strip(),
            "avg_temp": latest_row.get("Avg Temp", "").strip(),
            "high_temp": latest_row.get("High Temp", "").strip(),
            "low_temp": latest_row.get("Low Temp", "").strip(),
            "billing_month": latest_row.get("Billing Month", "").strip(),
            "billing_period": latest_row.get("Billing Period", "").strip(),
        }


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """
    Called from __init__.py after the config entry is set up.
    Creates the daily and cumulative sensors and registers a manual fetch service.
    """
    integration_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: AtmosDailyCoordinator = integration_data["coordinator"]

    await coordinator.async_request_refresh()

    entities = [
        AtmosEnergyDailyUsageSensor(coordinator, entry),
        AtmosEnergyCumulativeUsageSensor(coordinator, entry),
    ]
    async_add_entities(entities)

    if not hass.services.has_service(DOMAIN, "fetch_now"):
        async def async_handle_fetch_now(call: ServiceCall):
            _LOGGER.info("Manual fetch_now service called for Atmos Energy.")
            for eid, data in hass.data[DOMAIN].items():
                c: AtmosDailyCoordinator = data["coordinator"]
                await c.async_request_refresh()
            _LOGGER.info("Manual fetch_now service complete for all Atmos entries.")
        hass.services.async_register(DOMAIN, "fetch_now", async_handle_fetch_now)


class AtmosEnergyDailyUsageSensor(SensorEntity):
    """Sensor for the most recent daily usage (Consumption)."""

    def __init__(self, coordinator: AtmosDailyCoordinator, entry: ConfigEntry):
        self.coordinator = coordinator
        self.entry = entry
        self._attr_name = "Atmos Energy Daily Usage"
        self._attr_native_unit_of_measurement = "CCF"
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
    """Cumulative usage sensor for Atmos Energy (total_increasing)."""

    def __init__(self, coordinator: AtmosDailyCoordinator, entry: ConfigEntry):
        self.coordinator = coordinator
        self.entry = entry
        self._attr_name = "Atmos Energy Cumulative Usage"
        self._attr_native_unit_of_measurement = "CCF"
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
                _LOGGER.warning("Could not parse old state '%s' as float", last_state.state)
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
