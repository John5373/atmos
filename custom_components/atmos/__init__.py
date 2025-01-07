"""Atmos Energy Integration: __init__.py"""
import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.const import CONF_USERNAME
from homeassistant.helpers import service
from .const import DOMAIN
from .sensor import AtmosDailyCoordinator

_LOGGER = logging.getLogger(__name__)

SERVICE_CHECK_NOW = "check_now"

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Atmos Energy from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Create our daily coordinator
    coordinator = AtmosDailyCoordinator(hass, entry)

    # Store the coordinator so we can access it later (in the service handler, for example)
    hass.data[DOMAIN][entry.entry_id] = {"coordinator": coordinator}

    # Forward setup to the sensor platform
    hass.async_create_task(
        hass.config_entries.async_forward_entry_setup(entry, "sensor")
    )

    # Schedule the first daily update (if you’re doing once-a-day logic)
    coordinator.schedule_daily_update()

    # Register a service to manually fetch new data
    # If you have multiple entries, you can decide whether to register once or per-entry.
    # For example, register the service once if it’s not already registered:
    if not hass.services.has_service(DOMAIN, SERVICE_CHECK_NOW):
        
        async def async_handle_check_now_service(call: ServiceCall) -> None:
            """
            Service handler to manually fetch new data from Atmos.
            If multiple entries exist, you may want to handle them all or
            pick one based on a service parameter. Here we’ll just fetch from each.
            """
            _LOGGER.debug("Handling 'check_now' service call for Atmos Energy.")
            # Loop over all config entries for this integration:
            for entry_id, data in hass.data[DOMAIN].items():
                coordinator: AtmosDailyCoordinator = data.get("coordinator")
                if coordinator:
                    _LOGGER.debug("Requesting manual refresh for entry_id=%s", entry_id)
                    await coordinator.async_request_refresh()

            # Optionally: Fire an event or log something
            _LOGGER.info("Atmos Energy manual refresh completed.")

        # Register the service with an optional schema if needed
        hass.services.async_register(
            domain=DOMAIN,
            service=SERVICE_CHECK_NOW,
            service_func=async_handle_check_now_service,
            schema=None,  # or use vol.Schema(...) if your service has parameters
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Retrieve the coordinator
    integration_data = hass.data[DOMAIN].get(entry.entry_id)
    coordinator: AtmosDailyCoordinator | None = None
    if integration_data:
        coordinator = integration_data.get("coordinator")

    # Cancel the daily timer if it exists
    if coordinator and coordinator._unsub_timer:
        coordinator._unsub_timer()
        coordinator._unsub_timer = None

    # Unload the sensor platform
    unload_ok = await hass.config_entries.async_forward_entry_unload(entry, "sensor")
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    # If you only want the service available when at least one entry is loaded,
    # and this was the last entry, remove the service:
    if not hass.data[DOMAIN]:
        # No more entries left
        _LOGGER.debug("No more Atmos Energy entries loaded, removing service.")
        hass.services.async_remove(DOMAIN, SERVICE_CHECK_NOW)

    return unload_ok
