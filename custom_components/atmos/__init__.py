"""The AtmosEnergy integration."""
import logging
import requests
from bs4 import BeautifulSoup

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)
DOMAIN = "atmosenergy"

def validate_credentials(username, password):
    """Validate credentials by attempting to log in to AtmosEnergy."""
    try:
        login_page_url = "https://www.atmosenergy.com/accountcenter/logon/login.html"
        login_url = "https://www.atmosenergy.com/accountcenter/logon/authenticate.html"
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
        if resp.status_code != 200:
            _LOGGER.error("Error fetching login page: %s", resp.status_code)
            return False

        soup = BeautifulSoup(resp.content, "html.parser")
        form_id_element = soup.find("input", {"name": "formId"})
        form_id = form_id_element.get("value") if form_id_element else ""
        payload = {
            "username": username,
            "password": password,
            "formId": form_id
        }
        auth_resp = session.post(login_url, data=payload, headers=headers)
        if auth_resp.status_code in (200, 304) and (
            "logout" in auth_resp.text.lower() or "account center" in auth_resp.text.lower()
        ):
            return True
        return False
    except Exception as e:
        _LOGGER.exception("Error validating credentials: %s", e)
        return False

S# Global list for sensor entities to allow manual updates.
SENSORS = []

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the AtmosEnergy component."""
    async def handle_update(call: ServiceCall):
        _LOGGER.debug("Manual update service called")
        for sensor in SENSORS:
            sensor.update()
            sensor.async_write_ha_state()
    hass.services.async_register(DOMAIN, "update", handle_update)
    return True

async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up AtmosEnergy from a config entry."""
    await hass.config_entries.async_forward_entry_setups(config_entry, ["sensor"])
    return True

async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Unload an AtmosEnergy config entry."""
    return await hass.config_entries.async_unload_platforms(config_entry, ["sensor"])
