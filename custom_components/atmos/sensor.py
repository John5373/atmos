"""Atmos Energy Integration: sensor.py (Parsing XLS File)"""

import logging
import requests
import xlrd
import datetime
from urllib.parse import urlencode

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
    """Return a datetime object for the next occurrence of 4:00 AM local time."""
    now = dt_util.now()
    target = now.replace(hour=4, minute=0, second=0, microsecond=0)
    if now >= target:
        target += datetime.timedelta(days=1)
    return target


class AtmosDailyCoordinator:
    """
    Coordinator that:
      - Logs in to Atmos
      - Downloads an XLS file with usage data
      - Parses and stores daily & cumulative usage
      - Supports a daily auto-refresh at 4 AM and manual refresh via service
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.hass = hass
        self.entry = entry
        self.data = None  # Dictionary with parsed XLS row
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

            # Use weather_date for duplicate checking.
            new_date = new_data.get("weather_date")
            old_date = self.data.get("weather_date") if self.data else None
            if old_date and old_date == new_date:
                _LOGGER.info("Atmos data not updated (same date: %s). Skipping usage update.", new_date)
                return

            self.data = new_data

            # Retrieve consumption as a number.
            usage_value = new_data.get("consumption")
            if usage_value == "" or usage_value is None:
                _LOGGER.warning("The 'Consumption' field is empty in the XLS; defaulting usage to 0.0")
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
        Logs in to Atmos, downloads the XLS file, and parses the latest row.
        Expected XLS headers (case-insensitive): 
          "Temp Area", "Consumption", "Units", "Weather Date", "Avg Temp", "High Temp", "Low Temp", "Billing Month", "Billing Period"
        Returns a dictionary with normalized keys.
        """
        username = self.entry.data.get(CONF_USERNAME)
        password = self.entry.data.get(CONF_PASSWORD)

        session = requests.Session()
        # Set a browser-like User-Agent header.
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36"
        })

        # --- Step 1: Login ---
        login_url = "https://www.atmosenergy.com/accountcenter/logon/authenticate.html"
        # Do an initial GET to retrieve cookies.
        resp_get = session.get(login_url)
        resp_get.raise_for_status()

        # POST credentials.
        payload = {
            "username": username,
            "password": password,
        }
        post_resp = session.post(login_url, data=payload)
        post_resp.raise_for_status()

        if post_resp.status_code == 200 and ("Logout" in post_resp.text or "Sign Out" in post_resp.text):
            _LOGGER.info("Atmos login successful.")
        else:
            _LOGGER.warning("Atmos login may have failed. Check credentials or site changes.")

        # --- Step 2: Download XLS File ---
        now = datetime.datetime.now()
        timestamp_str = now.strftime("%m%d%Y%H:%M:%S")
        base_csv_url = "https://www.atmosenergy.com/accountcenter/usagehistory/dailyUsageDownload.html"
        params = {"billingPeriod": "Current"}
        csv_url = f"{base_csv_url}?&{urlencode(params)}&{timestamp_str}"
        csv_resp = session.get(csv_url)
        csv_resp.raise_for_status()

        _LOGGER.debug("Raw XLS content received, %d bytes", len(csv_resp.content))

        # --- Step 3: Parse XLS ---
        # Open the workbook from the binary content.
        workbook = xlrd.open_workbook(file_contents=csv_resp.content)
        sheet = workbook.sheet_by_index(0)
        if sheet.nrows < 2:
            _LOGGER.warning("Not enough rows in the XLS file.")
            return None

        # Assume first row is header; convert headers to lowercase.
        headers = [str(sheet.cell_value(0, col)).strip().lower() for col in range(sheet.ncols)]
        latest_row_values = [sheet.cell_value(sheet.nrows - 1, col) for col in range(sheet.ncols)]
        row_dict = {headers[i]: latest_row_values[i] for i in range(len(headers))}

        _LOGGER.debug("Parsed XLS last row: %s", row_dict)

        # Map to normalized keys.
        return {
            "weather_date": str(row_dict.get("weather date", "")).strip(),
            "consumption": str(row_dict.get("consumption", "")).strip(),
            "temp_area": str(row_dict.get("temp area", "")).strip(),
            "units": str(row_dict.get("units", "")).strip(),
            "avg_temp": str(row_dict.get("avg temp", "")).strip(),
            "high_temp": str(row_dict.get("high temp", "")).strip(),
            "low_temp": str(row_dict.get("low temp", "")).strip(),
            "billing_month": str(row_dict.get("billing month", "")).strip(),
            "billing_period": str(row_dict.get("billing period", "")).strip(),
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
