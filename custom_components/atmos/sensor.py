import logging
import requests
from bs4 import BeautifulSoup
from datetime import timedelta, datetime

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.helpers.event import async_track_point_in_time

from .const import DOMAIN, DEFAULT_NAME, CONF_USERNAME, CONF_PASSWORD

_LOGGER = logging.getLogger(__name__)

def _get_next_4am() -> datetime:
    """Return a datetime object for the next occurrence of 4:00 AM local time."""
    now = dt_util.now()
    # Set 'target' to today's 4:00 AM
    target = now.replace(hour=4, minute=0, second=0, microsecond=0)
    # If we are already past 4:00 AM today, schedule for tomorrow at 4:00 AM
    if now >= target:
        target += timedelta(days=1)
    return target

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up the Atmos Energy sensor based on a config entry."""
    coordinator = AtmosDailyCoordinator(hass, entry)

    # Create the sensor entity
    sensor = AtmosEnergyUsageSensor(coordinator, entry)
    async_add_entities([sensor])

    # Schedule the first daily update
    coordinator.schedule_daily_update()

class AtmosDailyCoordinator:
    """
    A simple coordinator-like class that handles fetching data
    exactly once per day at 4 AM, rather than using update_interval.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.hass = hass
        self.entry = entry
        self.data = None
        self._unsub_timer = None  # hold a reference to our scheduled callback

    def schedule_daily_update(self):
        """Schedule the next daily update for 4:00 AM local time."""
        if self._unsub_timer:
            # If there's an existing schedule, cancel it to avoid duplicates
            self._unsub_timer()
            self._unsub_timer = None

        next_time = _get_next_4am()
        _LOGGER.debug("Scheduling next Atmos update at %s", next_time)

        # Use async_track_point_in_time to schedule a one-time callback
        self._unsub_timer = async_track_point_in_time(
            self.hass,
            self._scheduled_update_callback,
            next_time
        )

    async def _scheduled_update_callback(self, now):
        """Callback that fetches fresh data, then reschedules the next daily update."""
        _LOGGER.debug("Running daily Atmos update at %s", now)
        await self.async_request_refresh()
        self.schedule_daily_update()

    async def async_request_refresh(self):
        """Manually trigger a data refresh and notify any listeners."""
        try:
            self.data = await self.hass.async_add_executor_job(self._fetch_atmos_usage)
        except Exception as err:
            _LOGGER.error("Error fetching Atmos data: %s", err)
            raise UpdateFailed from err

    def _fetch_atmos_usage(self):
        """Do the actual requests + scraping; return a dict with latest usage info."""
        username = self.entry.data[CONF_USERNAME]
        password = self.entry.data[CONF_PASSWORD]

        login_url = "https://www.atmosenergy.com/accountcenter/login"  # example
        usage_url = "https://www.atmosenergy.com/accountcenter/usage"  # example

        session = requests.Session()
        login_page = session.get(login_url)
        login_page.raise_for_status()

        payload = {
            "username": username,
            "password": password,
            # if needed: "csrf_token": ...
        }
        login_resp = session.post(login_url, data=payload)
        login_resp.raise_for_status()

        if "Logout" not in login_resp.text and "Sign Out" not in login_resp.text:
            _LOGGER.warning("Atmos Energy login may have failed. Check credentials/site changes.")

        usage_resp = session.get(usage_url)
        usage_resp.raise_for_status()

        soup = BeautifulSoup(usage_resp.text, "html.parser")
        table = soup.find("table", {"class": "usage-table"})
        if not table:
            _LOGGER.error("Could not find usage table in response.")
            return None

        rows = table.find_all("tr")
        if len(rows) < 2:
            _LOGGER.warning("No data rows found in usage table.")
            return None

        # Example parsing
        latest_row = rows[1]
        cols = latest_row.find_all("td")
        if len(cols) < 3:
            _LOGGER.warning("Unexpected usage row format.")
            return None

        date_val = cols[0].get_text(strip=True)
        usage_val = cols[1].get_text(strip=True)
        cost_val = cols[2].get_text(strip=True)

        return {
            "date": date_val,
            "usage": usage_val,
            "cost": cost_val
        }

class AtmosEnergyUsageSensor(SensorEntity):
    """Sensor entity that displays the most recent usage from Atmos Energy."""

    def __init__(self, coordinator: AtmosDailyCoordinator, entry: ConfigEntry):
        self.coordinator = coordinator
        self.entry = entry
        self._attr_name = DEFAULT_NAME
        self._attr_unique_id = f"{entry.entry_id}-daily-usage"
        self._attr_icon = "mdi:gas-cylinder"
        self._attr_native_unit_of_measurement = "Ccf"  # or "Therms", etc.

    async def async_added_to_hass(self):
        """
        Called when the entity is added to Home Assistant.
        We'll listen for coordinator refreshes (though it's only daily).
        """
        await super().async_added_to_hass()

        # We can force an initial update if you want a reading ASAP on first install
        await self.coordinator.async_request_refresh()

        # Whenever the coordinator updates, we call async_write_ha_state()
        self.async_on_remove(
            self.coordinator.hass.bus.async_listen_once(
                "event_atmos_update", lambda _: self.async_write_ha_state()
            )
        )

    @property
    def native_value(self):
        """Return the sensor's primary value: the most recent usage."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("usage")

    @property
    def extra_state_attributes(self):
        """Return additional attributes like date and cost."""
        data = self.coordinator.data
        if not data:
            return {}
        return {
            "date": data.get("date"),
            "cost": data.get("cost"),
        }

    @property
    def should_poll(self) -> bool:
        """Disable polling. We'll manually fetch once per day at 4 AM."""
        return False
