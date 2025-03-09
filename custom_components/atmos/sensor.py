"""Atmos Energy Integration: sensor.py (Full, Fixed Version)"""

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
      - Downloads the CSV with usage data
      - Parses and stores daily + cumulative usage
      - Supports scheduled auto-refresh at 4 AM
      - Supports manual refresh via Home Assistant service
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
                new_data.get("weather_date"), usage_float, self.cumulative_usage
            )

        except Exception as err:
            _LOGGER.error("Error refreshing Atmos data: %s", err)
            raise UpdateFailed from err

    def _fetch_data(self):
        """
        1) Logs in to Atmos and confirms success.
        2) Downloads the CSV and extracts the most recent row.
        """

        username = self.entry.data.get(CONF_USERNAME)
        password = self.entry.data.get(CONF_PASSWORD)

        session = requests.Session()

        # --- Step 1: Login to Atmos ---
        login_url = "https://www.atmosenergy.com/accountcenter/logon/authenticate.html"
        post_resp = session.post(login_url, data={"username": username, "password": password})
        post_resp.raise_for_status()

        if post_resp.status_code == 200 and "Logout" in post_resp.text:
            _LOGGER.info("Atmos login successful.")
        else:
            _LOGGER.warning("Atmos login may have failed. Check credentials.")

        # --- Step 2: Download CSV ---
        now = datetime.datetime.now()
        timestamp_str = now.strftime("%m%d%Y%H:%M:%S")
        csv_url = f"https://www.atmosenergy.com/accountcenter/usagehistory/dailyUsageDownload.html?&billingPeriod=Current&{timestamp_str}"

        csv_resp = session.get(csv_url)
        csv_resp.raise_for_status()

        _LOGGER.debug("Raw CSV content:\n%s", csv_resp.text)

        # --- Step 3: Parse CSV ---
        csv_file = io.StringIO(csv_resp.text)
        reader = csv.DictReader(csv_file)
        rows = list(reader)

        if not rows:
            _LOGGER.warning("No rows found in the CSV file.")
            return None

        latest_row = rows[-1]  # Get the last row (most recent data)
        _LOGGER.debug("Parsed CSV last row: %s", latest_row)

        raw_usage = latest_row.get("Consumption", "").strip()
        try:
            usage_float = float(raw_usage.replace(",", "").strip())
        except ValueError:
            _LOGGER.warning("Could not convert 'Consumption' value '%s' to float.", raw_usage)
            usage_float = 0.0

        return {
            "weather_date": latest_row.get("Weather Date", "").strip(),
            "consumption": usage_float,
            "temp_area": latest_row.get("Temp Area", "").strip(),
            "units": latest_row.get("Units", "").strip(),
            "avg_temp": latest_row.get("Avg Temp", "").strip(),
            "high_temp": latest_row.get("High Temp", "").strip(),
            "low_temp": latest_row.get("Low Temp", "").strip(),
            "billing_month": latest_row.get("Billing Month", "").strip(),
            "billing_period": latest_row.get("Billing Period", "").strip(),
        }


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up Atmos Energy sensors and register the fetch_now service."""
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
    """Sensor for Atmos Energy daily usage."""

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


class AtmosEnergyCumulativeUsageSensor(SensorEntity, RestoreEntity):
    """Cumulative usage sensor for Atmos Energy."""

    def __init__(self, coordinator: AtmosDailyCoordinator, entry: ConfigEntry):
        self.coordinator = coordinator
        self.entry = entry
        self._attr_name = "Atmos Energy Cumulative Usage"
        self._attr_native_unit_of_measurement = "CCF"
        self._attr_device_class = "gas"
        self._attr_state_class = "total_increasing"

    @property
    def native_value(self):
        return self.coordinator.cumulative_usage
