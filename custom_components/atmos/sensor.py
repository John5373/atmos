"""Sensor platform for AtmosEnergy."""
import csv
import datetime
import logging
import os

import requests
from bs4 import BeautifulSoup

from homeassistant.const import CONF_USERNAME, CONF_PASSWORD
from homeassistant.helpers.entity import Entity

_LOGGER = logging.getLogger(__name__)
DOMAIN = "atmosenergy"

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up AtmosEnergy sensor from a config entry."""
    username = config_entry.data.get(CONF_USERNAME)
    password = config_entry.data.get(CONF_PASSWORD)
    async_add_entities([AtmosEnergySensor(username, password)], True)

class AtmosEnergySensor(Entity):
    """Representation of an AtmosEnergy sensor."""

    def __init__(self, username, password):
        """Initialize the sensor."""
        self._username = username
        self._password = password
        self._state = None
        self._attributes = {}
        self._name = "AtmosEnergy Usage"

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self):
        """Return the sensor state."""
        return self._state

    @property
    def extra_state_attributes(self):
        """Return sensor attributes."""
        return self._attributes

    def update(self):
        """Fetch new state data for the sensor."""
        try:
            login_page_url = "https://www.atmosenergy.com/accountcenter/logon/login.html"
            login_url = "https://www.atmosenergy.com/accountcenter/logon/authenticate.html"
            data_download_url = (
                "https://www.atmosenergy.com/accountcenter/usagehistory/dailyUsageDownload.html"
                "?&billingPeriod=Current&0309202508:56:41"
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
                "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"),
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

            # Try decoding CSV with UTF-8, fallback to latin1 if that fails.
            try:
                csv_text = csv_resp.content.decode("utf-8")
            except UnicodeDecodeError:
                _LOGGER.warning("UTF-8 decode failed, trying latin1 encoding")
                csv_text = csv_resp.content.decode("latin1")
            
            import csv  # Ensure csv is imported
            csv_reader = csv.reader(csv_text.splitlines())
            rows = list(csv_reader)

            if rows and "Usage" in rows[0]:
                usage_index = rows[0].index("Usage")
                if len(rows) > 1:
                    usage_value = rows[1][usage_index]
                    self._state = usage_value
                    self._attributes["last_updated"] = datetime.datetime.now().isoformat()
                    _LOGGER.debug("Updated sensor state with usage: %s", usage_value)
                else:
                    _LOGGER.warning("CSV data has no data rows.")
                    self._state = None
            else:
                _LOGGER.error("CSV header does not include 'Usage'. Header: %s", rows[0] if rows else "Empty")
                self._state = None
        except Exception as e:
            _LOGGER.exception("Error updating AtmosEnergy sensor: %s", e)
            self._state = None
