"""Atmos Energy Integration: sensor.py (with Detailed Debug for Authentication and CSV Fetch)"""

import logging
import requests
import csv
import io
import datetime
from urllib.parse import urlencode
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
    Log request details:
      - Method, URL
      - Data/Payload (with password masked)
      - Headers if provided.
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
    Log response details:
      - URL, status code, headers, and body text (truncated if too long)
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
      - Authenticates with Atmos
      - Downloads the usage CSV file
      - Parses and stores the latest usage data
      - Maintains daily and cumulative usage
      - Supports scheduled refresh at 4 AM and manual refresh via a service
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.hass = hass
        self.entry = entry
        self.data = None  # Parsed row data dictionary
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
        """Fetch new data asynchronously and update usage values."""
        try:
            new_data = await self.hass.async_add_executor_job(self._fetch_data)
            if not new_data:
                _LOGGER.warning("No data returned from Atmos fetch.")
                return

            new_date = new_data.get("weather_date")
            old_date = self.data.get("weather_date") if self.data else None
            if old_date and old_date == new_date:
                _LOGGER.info("Atmos data not updated (same date: %s). Skipping usage update.", new_date)
                return

            self.data = new_data

            # Retrieve and convert the consumption value
            usage_value = new_data.get("consumption")
            if usage_value == "" or usage_value is None:
                _LOGGER.warning("The 'Consumption' field is empty; defaulting usage to 0.0")
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
        Logs in to Atmos, downloads the usage file, and parses the latest row.
        Returns a dictionary with keys:
          "weather_date", "consumption", "temp_area", "units", "avg_temp",
          "high_temp", "low_temp", "billing_month", "billing_period"
        """
        username = self.entry.data.get(CONF_USERNAME)
        password = self.entry.data.get(CONF_PASSWORD)

        session = requests.Session()
        # Set headers to mimic a real browser
        session.headers.update({
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/90.0.4430.212 Safari/537.36"),
            "Accept": "text/csv,application/csv,application/vnd.ms-excel,*/*"
        })

        # --- Step 1: Login ---
        login_url = "https://www.atmosenergy.com/accountcenter/logon/authenticate.html"
        _debug_request("GET login page", "GET", login_url)
        resp_get = session.get(login_url)
        _debug_response("GET login page", resp_get)
        resp_get.raise_for_status()

        # Optionally parse hidden tokens with BeautifulSoup if required
        soup = BeautifulSoup(resp_get.text, "html.parser")
        # e.g., token = soup.find("input", {"name": "csrf_token"}) if needed

        payload = {"username": username, "password": password}
        _debug_request("POST credentials", "POST", login_url, data=payload)
        post_resp = session.post(login_url, data=payload)
        _debug_response("POST credentials", post_resp)
        post_resp.raise_for_status()

        if post_resp.status_code == 200 and ("Logout" in post_resp.text or "Sign Out" in post_resp.text):
            _LOGGER.info("Atmos login successful.")
        else:
            _LOGGER.warning("Atmos login may have failed. Check credentials or site changes.")

        _LOGGER.debug("Session cookies after login: %s", session.cookies.get_dict())

        # --- Step 2: Download Usage CSV ---
        now = datetime.datetime.now()
        timestamp_str = now.strftime("%m%d%Y%H:%M:%S")
        base_url = "https://www.atmosenergy.com/accountcenter/usagehistory/dailyUsageDownload.html"
        params = {"billingPeriod": "Current"}
        csv_url = f"{base_url}?&{urlencode(params)}&{timestamp_str}"
        _debug_request("GET CSV", "GET", csv_url)
        csv_resp = session.get(csv_url)
        _debug_response("GET CSV", csv_resp)
        csv_resp.raise_for_status()

        _LOGGER.debug("Raw CSV content (length %d): %s", len(csv_resp.text), csv_resp.text)

        # --- Step 3: Parse CSV ---
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
    Set up Atmos Energy sensors and register the manual fetch service.
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
