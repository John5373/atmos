import logging
import requests
from bs4 import BeautifulSoup
from datetime import timedelta

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers import update_coordinator
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.typing import StateType

from .const import DOMAIN, DEFAULT_NAME

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up Atmos Energy sensor based on a config entry."""
    coordinator = AtmosEnergyCoordinator(hass, entry)
    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    # Create one or more sensor entities
    async_add_entities([AtmosEnergyUsageSensor(coordinator, entry)], True)

class AtmosEnergyCoordinator(update_coordinator.DataUpdateCoordinator):
    """Coordinator to fetch usage data from Atmos Energy."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        """Initialize the coordinator."""
        self.entry = entry
        super().__init__(
            hass,
            _LOGGER,
            name="AtmosEnergyCoordinator",
            update_interval=timedelta(
                seconds=entry.options.get("scan_interval", 3600)
            ),  # default 1 hour if not set in options
        )

    async def _async_update_data(self):
        """Fetch data from Atmos's site (runs in executor thread)."""
        return await self.hass.async_add_executor_job(self._fetch_atmos_usage)

    def _fetch_atmos_usage(self):
        """Do the actual requests + scraping; return a dict with latest usage info."""

        # 1. Gather credentials
        username = self.entry.data[CONF_USERNAME]
        password = self.entry.data[CONF_PASSWORD]

        # 2. Start session, get login page, etc. (adjust URLs, parse CSRF tokens as needed)
        login_url = "https://www.atmosenergy.com/accountcenter/login"  # example
        usage_url = "https://www.atmosenergy.com/accountcenter/usage"  # example

        session = requests.Session()
        login_page = session.get(login_url)
        login_page.raise_for_status()

        # Potentially parse CSRF token
        # soup_login = BeautifulSoup(login_page.text, "html.parser")
        # token_element = soup_login.find(...)
        # csrf_token = token_element["value"] if token_element else ""

        payload = {
            "username": username,
            "password": password,
            # "csrfmiddlewaretoken": csrf_token
        }
        login_response = session.post(login_url, data=payload)
        login_response.raise_for_status()

        if "Logout" not in login_response.text and "Sign Out" not in login_response.text:
            _LOGGER.warning("Atmos Energy login may have failed. Check credentials/site changes.")

        # 3. Navigate to usage page
        usage_response = session.get(usage_url)
        usage_response.raise_for_status()

        soup_usage = BeautifulSoup(usage_response.text, "html.parser")

        # 4. Parse the usage table; adapt selectors to real HTML structure
        usage_table = soup_usage.find("table", {"class": "usage-table"})
        if not usage_table:
            _LOGGER.error("Could not find usage table on the usage page.")
            return None

        rows = usage_table.find_all("tr")
        if len(rows) < 2:
            _LOGGER.warning("Usage table has no data rows.")
            return None

        # Assume the first row after the header is the most recent
        latest_row = rows[1]
        cols = latest_row.find_all("td")
        if len(cols) < 3:
            _LOGGER.warning("Unexpected usage row format.")
            return None

        date_val = cols[0].get_text(strip=True)
        usage_val = cols[1].get_text(strip=True)
        cost_val = cols[2].get_text(strip=True)

        # Return a simple dict
        return {
            "date": date_val,
            "usage": usage_val,
            "cost": cost_val
        }

class AtmosEnergyUsageSensor(SensorEntity):
    """Sensor entity that displays the most recent usage from Atmos Energy."""

    def __init__(self, coordinator: AtmosEnergyCoordinator, entry: ConfigEntry):
        """Initialize sensor."""
        self.coordinator = coordinator
        self.entry = entry
        self._attr_name = DEFAULT_NAME
        self._attr_unique_id = f"{entry.entry_id}-daily-usage"

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the units for usage (Ccf, Therms, etc.)."""
        return "Ccf"  # Example

    @property
    def icon(self) -> str:
        """Return icon."""
        return "mdi:gas-cylinder"

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional attributes such as date and cost."""
        data = self.coordinator.data
        if not data:
            return {}
        return {
            "date": data.get("date"),
            "cost": data.get("cost"),
        }

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor (the most recent usage)."""
        data = self.coordinator.data
        if not data:
            return None
        return data.get("usage")

    @property
    def should_poll(self) -> bool:
        """Disable polling. DataUpdateCoordinator will call updates."""
        return False

    def update(self):
        """No-op; updates handled by coordinator."""
        pass

    async def async_update(self):
        """No-op; updates handled by coordinator."""
        pass

    async def async_added_to_hass(self):
        """When entity is added to hass, register for coordinator updates."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )
