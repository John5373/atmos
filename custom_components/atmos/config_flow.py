"""Config flow for AtmosEnergy integration."""
import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD

_LOGGER = logging.getLogger(__name__)
DOMAIN = "atmosenergy"

DATA_SCHEMA = vol.Schema({
    vol.Required(CONF_USERNAME): str,
    vol.Required(CONF_PASSWORD): str,
})

class AtmosEnergyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for AtmosEnergy."""
    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]
            try:
                # Defer import to avoid blocking the event loop during initial import
                from . import validate_credentials
                valid = await self.hass.async_add_executor_job(validate_credentials, username, password)
                if valid:
                    return self.async_create_entry(title="AtmosEnergy", data=user_input)
                errors["base"] = "invalid_auth"
            except Exception as err:
                _LOGGER.exception("Error validating credentials: %s", err)
                errors["base"] = "unknown_error"
        return self.async_show_form(step_id="user", data_schema=DATA_SCHEMA, errors=errors)
