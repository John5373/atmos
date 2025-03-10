"""Sensor platform for AtmosEnergy."""
import io
import datetime
import logging

import pandas as pd
import requests
from bs4 import BeautifulSoup

from homeassistant.const import CONF_USERNAME, CONF_PASSWORD
from homeassistant.helpers.entity import Entity

# Import the global sensor list from our integration.
from custom_components.atmosenergy import SENSORS

_LOGGER = logging.getLogger(__name__)
DOMAIN = "atmosenergy"

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up AtmosEnergy sensors from a config entry."""
    username = config_entry.data.get(CONF_USERNAME)
    password = config_entry.data.get(CONF_PASSWORD)
    entities = [
        AtmosEnergyLatestSensor(username, password),
        AtmosEnergyCumulativeSensor(username, password)
    ]
    async_add_entities(entities, True)

class AtmosEnergyLatestSensor(Entity):
    """Sensor showing the most recent consumption data with weather info as attributes."""
    _attr_should_poll = False  # Disable polling; we update via schedule or service

    def __init__(self, username, password):
        """Initialize the Latest Consumption sensor."""
        self._username = username
        self._password = password
        self._state = None
        self._attributes = {}
        self._name = "AtmosEnergy Latest Consumption"

    async def async_added_to_hass(self):
        """When entity is added, register it in the global list."""
        SENSORS.append(self)

    async def async_will_remove_from_hass(self):
        """When entity is removed, remove it from the global list."""
        if self in SENSORS:
            SENSORS.remove(self)

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self):
        """Return the sensor's state (latest consumption)."""
        return self._state

    @property
    def extra_state_attributes(self):
        """Return sensor attributes."""
        return self._attributes

    @property
    def device_class(self):
        """Return the device class."""
        return "gas"

    @property
    def state_class(self):
        """Return the state class."""
        return "total"

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return "CCF"

    def update(self):
        """Fetch the latest consumption data and weather info from the Excel file."""
        try:
            login_page_url = "https://www.atmosenergy.com/accountcenter/logon/login.html"
            login_url = "https://www.atmosenergy.com/accountcenter/logon/authenticate.html"
            # Create a dynamic timestamp (format: ddMMyyyyHH:MM:SS)
            timestamp = datetime.datetime.now().strftime("%d%m%Y%H:%M:%S")
            data_download_url = (
                "https://www.atmosenergy.com/accountcenter/usagehistory/dailyUsageDownload.html"
                f"?&billingPeriod=Current&{timestamp}"
            )
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "Accept-Encoding": "gzip, deflate, br, zstd",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Content-Type": "application/x-www-form-urlencoded",
                "DNT": "1",
                "Host": "www.atmosenergy.com",
                "Origin": "https://www.atmosenergy.com",
                "Pragma": "no-cache",
                "Referer": "https://www.atmosenergy.com/accountcenter/logon/login.html",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
                ),
                "sec-ch-ua": "\"Not(A:Brand\";v=\"99\", \"Google Chrome\";v=\"133\", \"Chromium\";v=\"133\"",
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": "macOS"
            }
            session = requests.Session()
            # Retrieve cookies and the hidden formId.
            resp = session.get(login_page_url, headers=headers)
            soup = BeautifulSoup(resp.content, "html.parser")
            form_id_element = soup.find("input", {"name": "formId"})
            form_id = form_id_element.get("value") if form_id_element else ""
            payload = {
                "username": self._username,
                "password": self._password,
                "formId": form_id
            }
            auth_resp = session.post(login_url, data=payload, headers=headers)
            if auth_resp.status_code not in (200, 304):
                _LOGGER.error("Authentication failed with status code: %s", auth_resp.status_code)
                self._state = None
                return

            # Download the Excel file.
            xls_resp = session.get(data_download_url, headers=headers)
            if xls_resp.status_code != 200:
                _LOGGER.error("Failed to download XLS data. Status code: %s", xls_resp.status_code)
                self._state = None
                return

            xls_file = io.BytesIO(xls_resp.content)
            try:
                df = pd.read_excel(xls_file)
            except Exception as e:
                _LOGGER.error("Error reading Excel file: %s", e)
                self._state = None
                return

            if df.empty:
                _LOGGER.error("Excel file is empty.")
                self._state = None
                return

            if "Consumption" not in df.columns:
                _LOGGER.error("Excel data does not include 'Consumption' column. Columns: %s", df.columns)
                self._state = None
                return

            # Use the last row (most recent record) as the latest data.
            latest_record = df.iloc[-1]
            consumption_value = latest_record["Consumption"]
            self._state = consumption_value
            attributes = {}
            if "Weather Date" in df.columns:
                attributes["weather date"] = latest_record["Weather Date"]
            if "Avg Temp" in df.columns:
                attributes["Avg Temp"] = latest_record["Avg Temp"]
            if "High Temp" in df.columns:
                attributes["High Temp"] = latest_record["High Temp"]
            if "Low Temp" in df.columns:
                attributes["Low Temp"] = latest_record["Low Temp"]
            attributes["last_updated"] = datetime.datetime.now().isoformat()
            self._attributes = attributes
            _LOGGER.debug("Updated Latest sensor state with consumption: %s", consumption_value)
        except Exception as e:
            _LOGGER.exception("Error updating AtmosEnergy Latest sensor: %s", e)
            self._state = None

class AtmosEnergyCumulativeSensor(Entity):
    """Sensor showing the cumulative consumption (sum of all days)."""
    _attr_should_poll = False

    def __init__(self, username, password):
        """Initialize the Cumulative Consumption sensor."""
        self._username = username
        self._password = password
        self._state = None
        self._attributes = {}
        self._name = "AtmosEnergy Cumulative Consumption"

    async def async_added_to_hass(self):
        """When entity is added, register it in the global list."""
        SENSORS.append(self)

    async def async_will_remove_from_hass(self):
        """When entity is removed, remove it from the global list."""
        if self in SENSORS:
            SENSORS.remove(self)

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self):
        """Return the sensor's state (cumulative consumption)."""
        return self._state

    @property
    def extra_state_attributes(self):
        """Return sensor attributes."""
        return self._attributes

    @property
    def device_class(self):
        """Return the device class."""
        return "gas"

    @property
    def state_class(self):
        """Return the state class."""
        return "total_increasing"

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return "CCF"

    def update(self):
        """Fetch and compute cumulative consumption data from the Excel file."""
        try:
            login_page_url = "https://www.atmosenergy.com/accountcenter/logon/login.html"
            login_url = "https://www.atmosenergy.com/accountcenter/logon/authenticate.html"
            timestamp = datetime.datetime.now().strftime("%d%m%Y%H:%M:%S")
            data_download_url = (
                "https://www.atmosenergy.com/accountcenter/usagehistory/dailyUsageDownload.html"
                f"?&billingPeriod=Current&{timestamp}"
            )
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "Accept-Encoding": "gzip, deflate, br, zstd",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Content-Type": "application/x-www-form-urlencoded",
                "DNT": "1",
                "Host": "www.atmosenergy.com",
                "Origin": "https://www.atmosenergy.com",
                "Pragma": "no-cache",
                "Referer": "https://www.atmosenergy.com/accountcenter/logon/login.html",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
                ),
                "sec-ch-ua": "\"Not(A:Brand\";v=\"99\", \"Google Chrome\";v=\"133\", \"Chromium\";v=\"133\"",
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": "macOS"
            }
            session = requests.Session()
            resp = session.get(login_page_url, headers=headers)
            soup = BeautifulSoup(resp.content, "html.parser")
            form_id_element = soup.find("input", {"name": "formId"})
            form_id = form_id_element.get("value") if form_id_element else ""
            payload = {
                "username": self._username,
                "password": self._password,
                "formId": form_id
            }
            auth_resp = session.post(login_url, data=payload, headers=headers)
            if auth_resp.status_code not in (200, 304):
                _LOGGER.error("Authentication failed with status code: %s", auth_resp.status_code)
                self._state = None
                return
            xls_resp = session.get(data_download_url, headers=headers)
            if xls_resp.status_code != 200:
                _LOGGER.error("Failed to download XLS data. Status code: %s", xls_resp.status_code)
                self._state = None
                return
            xls_file = io.BytesIO(xls_resp.content)
            try:
                df = pd.read_excel(xls_file)
            except Exception as e:
                _LOGGER.error("Error reading Excel file: %s", e)
                self._state = None
                return
            if df.empty:
                _LOGGER.error("Excel file is empty.")
                self._state = None
                return
            if "Consumption" not in df.columns:
                _LOGGER.error("Excel data does not include 'Consumption' column. Columns: %s", df.columns)
                self._state = None
                return
            cumulative = df["Consumption"].sum()
            self._state = cumulative
            self._attributes["last_updated"] = datetime.datetime.now().isoformat()
            self._attributes["number_of_days"] = len(df)
            _LOGGER.debug("Updated Cumulative sensor state with consumption: %s", cumulative)
        except Exception as e:
            _LOGGER.exception("Error updating AtmosEnergy Cumulative sensor: %s", e)
            self._state = None
