import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN, CONF_USERNAME, CONF_PASSWORD

_LOGGER = logging.getLogger(__name__)

@config_entries.HANDLERS.register(DOMAIN)
class AtmosEnergyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Atmos Energy."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            # Validate credentials or check for duplicates
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]

            # Example: check if an entry with the same username already exists
            existing_entries = [
                entry for entry in self._async_current_entries()
                if entry.data.get(CONF_USERNAME) == username
            ]
            if existing_entries:
                errors["base"] = "already_configured"
            else:
                # Optionally validate credentials here by scraping or test a login
                # If successful, create the entry
                return self.async_create_entry(title=f"Atmos ({username})", data=user_input)

        schema = vol.Schema({
            vol.Required(CONF_USERNAME): cv.string,
            vol.Required(CONF_PASSWORD): cv.string,
        })

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Define the options flow handler if needed."""
        return AtmosEnergyOptionsFlowHandler(config_entry)

class AtmosEnergyOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow if you want to allow changing scan interval, etc."""
    def __init__(self, config_entry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Example option: let user set a custom update interval
        schema = vol.Schema({
            vol.Optional("scan_interval", default=3600): cv.positive_int
        })

        return self.async_show_form(step_id="init", data_schema=schema)
