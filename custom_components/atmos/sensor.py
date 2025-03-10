"""Sensor platform for AtmosEnergy."""
import csv
import datetime
import logging

import requests
from bs4 import BeautifulSoup

from homeassistant.const import CONF_USERNAME, CONF_PASSWORD
from homeassistant.helpers.entity import Entity

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

    def __init__(self, username, password):
        """Initialize the Latest Consumption sensor."""
        self._username = username
        self._password = password
        self._state = None
        self._attributes = {}
        self._name = "AtmosEnergy Latest Consumption"

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

    def update(self):
        """Fetch the latest consumption data and weather information."""
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
            # Step 1: Retrieve cookies and the hidden formId.
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

            # Step 2: Download the CSV data.
            csv_resp = session.get(data_download_url, headers=headers)
            if csv_resp.status_code != 200:
                _LOGGER.error("Failed to download CSV data. Status code: %s", csv_resp.status_code)
                self._state = None
                return

            # Try decoding CSV using UTF-8; fallback to latin1 if necessary.
            try:
                csv_text = csv_resp.content.decode("utf-8")
            except UnicodeDecodeError:
                _LOGGER.warning("UTF-8 decode failed, trying latin1 encoding")
                csv_text = csv_resp.content.decode("latin1")
            
            csv_reader = csv.reader(csv_text.splitlines())
            rows = list(csv_reader)
            if not rows or len(rows) < 2:
                _LOGGER.error("CSV data is empty or missing data rows.")
                self._state = None
                return

            header = rows[0]
            if "Consumption" not in header:
                _LOGGER.error("CSV header does not include 'Consumption'. Header: %s", header)
                self._state = None
                return

            # Use the last row (most recent data) as the latest record.
            latest_record = rows[-1]
            consumption_index = header.index("Consumption")
            # Get additional weather columns if present.
            weather_date_index = header.index("Weather Date") if "Weather Date" in header else None
            avg_temp_index = header.index("Avg Temp") if "Avg Temp" in header else None
            high_temp_index = header.index("High Temp") if "High Temp" in header else None
            low_temp_index = header.index("Low Temp") if "Low Temp" in header else None

            consumption_value = latest_record[consumption_index]
            self._state = consumption_value
            attributes = {}
            if weather_date_index is not None:
                attributes["weather date"] = latest_record[weather_date_index]
            if avg_temp_index is not None:
                attributes["Avg Temp"] = latest_record[avg_temp_index]
            if high_temp_index is not None:
                attributes["High Temp"] = latest_record[high_temp_index]
            if low_temp_index is not None:
                attributes["Low Temp"] = latest_record[low_temp_index]
            attributes["last_updated"] = datetime.datetime.now().isoformat()
            self._attributes = attributes
            _LOGGER.debug("Updated Latest sensor state with consumption: %s", consumption_value)
        except Exception as e:
            _LOGGER.exception("Error updating AtmosEnergy Latest sensor: %s", e)
            self._state = None

class AtmosEnergyCumulativeSensor(Entity):
    """Sensor showing the cumulative consumption (sum of all days)."""

    def __init__(self, username, password):
        """Initialize the Cumulative Consumption sensor."""
        self._username = username
        self._password = password
        self._state = None
        self._attributes = {}
        self._name = "AtmosEnergy Cumulative Consumption"

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

    def update(self):
        """Fetch and compute cumulative consumption data."""
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

            csv_resp = session.get(data_download_url, headers=headers)
            if csv_resp.status_code != 200:
                _LOGGER.error("Failed to download CSV data. Status code: %s", csv_resp.status_code)
                self._state = None
                return

            try:
                csv_text = csv_resp.content.decode("utf-8")
            except UnicodeDecodeError:
                _LOGGER.warning("UTF-8 decode failed, trying latin1 encoding")
                csv_text = csv_resp.content.decode("latin1")
            
            csv_reader = csv.reader(csv_text.splitlines())
            rows = list(csv_reader)
            if not rows or len(rows) < 2:
                _LOGGER.error("CSV data is empty or missing data rows.")
                self._state = None
                return

            header = rows[0]
            if "Consumption" not in header:
                _LOGGER.error("CSV header does not include 'Consumption'. Header: %s", header)
                self._state = None
                return

            cumulative = 0.0
            for row in rows[1:]:
                try:
                    value = float(row[header.index("Consumption")])
                    cumulative += value
                except ValueError:
                    _LOGGER.warning("Skipping invalid consumption value in row: %s", row)
            self._state = cumulative
            self._attributes["last_updated"] = datetime.datetime.now().isoformat()
            self._attributes["number_of_days"] = len(rows) - 1
            _LOGGER.debug("Updated Cumulative sensor state with consumption: %s", cumulative)
        except Exception as e:
            _LOGGER.exception("Error updating AtmosEnergy Cumulative sensor: %s", e)
            self._state = None
