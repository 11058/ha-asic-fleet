"""Config and options flow."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_ALLOW_POOL_WRITE,
    CONF_ALLOW_REBOOT,
    CONF_ASIC_PASSWORD,
    CONF_ASIC_PORT,
    CONF_ASIC_USERNAME,
    CONF_BLOCKLIST,
    CONF_FAIL_THRESHOLD,
    CONF_FAN_MIN_RPM,
    CONF_HASHRATE_WARN_PCT,
    CONF_HOSTNAME_PATTERN,
    CONF_HW_ERROR_PCT,
    CONF_MARKER_LIST,
    CONF_OFFLINE_GRACE,
    CONF_PROBE_UNKNOWN,
    CONF_ROUTER_HOST,
    CONF_ROUTER_PASSWORD,
    CONF_ROUTER_TLS,
    CONF_ROUTER_USERNAME,
    CONF_ROUTER_VERIFY_SSL,
    CONF_SCAN_INTERVAL,
    CONF_TEMP_CRIT,
    CONF_TEMP_WARN,
    DEFAULT_ASIC_PORT,
    DEFAULT_ASIC_USERNAME,
    DEFAULT_BLOCKLIST,
    DEFAULT_FAIL_THRESHOLD,
    DEFAULT_FAN_MIN_RPM,
    DEFAULT_HASHRATE_WARN_PCT,
    DEFAULT_HOSTNAME_PATTERN,
    DEFAULT_HW_ERROR_PCT,
    DEFAULT_MARKER_LIST,
    DEFAULT_OFFLINE_GRACE,
    DEFAULT_PROBE_UNKNOWN,
    DEFAULT_ROUTER_USERNAME,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TEMP_CRIT,
    DEFAULT_TEMP_WARN,
    DOMAIN,
)
from .mikrotik import MikrotikAuthError, MikrotikClient, MikrotikError

_LOGGER = logging.getLogger(__name__)

ROUTER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ROUTER_HOST): TextSelector(),
        vol.Required(
            CONF_ROUTER_USERNAME, default=DEFAULT_ROUTER_USERNAME
        ): TextSelector(),
        vol.Required(CONF_ROUTER_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Optional(CONF_ROUTER_TLS, default=False): BooleanSelector(),
        vol.Optional(CONF_ROUTER_VERIFY_SSL, default=False): BooleanSelector(),
    }
)

ASIC_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ASIC_USERNAME, default=DEFAULT_ASIC_USERNAME): TextSelector(),
        vol.Required(CONF_ASIC_PASSWORD, default="root"): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Optional(CONF_ASIC_PORT, default=DEFAULT_ASIC_PORT): NumberSelector(
            NumberSelectorConfig(min=1, max=65535, mode=NumberSelectorMode.BOX)
        ),
    }
)


class AsicFleetConfigFlow(ConfigFlow, domain=DOMAIN):
    """Two-step setup: router first, then the miners' web credentials."""

    VERSION = 1

    def __init__(self) -> None:
        self._router: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            client = MikrotikClient(
                async_get_clientsession(self.hass),
                user_input[CONF_ROUTER_HOST],
                user_input[CONF_ROUTER_USERNAME],
                user_input[CONF_ROUTER_PASSWORD],
                tls=user_input.get(CONF_ROUTER_TLS, False),
                verify_ssl=user_input.get(CONF_ROUTER_VERIFY_SSL, False),
            )
            try:
                identity = await client.identity()
            except MikrotikAuthError:
                errors["base"] = "invalid_auth"
            except MikrotikError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(
                    f"{DOMAIN}_{user_input[CONF_ROUTER_HOST]}"
                )
                self._abort_if_unique_id_configured()
                self._router = dict(user_input)
                self._router["identity"] = identity.get("name")
                return await self.async_step_asic()

        return self.async_show_form(
            step_id="user", data_schema=ROUTER_SCHEMA, errors=errors
        )

    async def async_step_asic(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            identity = self._router.pop("identity", None)
            data = {**self._router, **user_input}
            data[CONF_ASIC_PORT] = int(data.get(CONF_ASIC_PORT, DEFAULT_ASIC_PORT))
            title = f"ASIC Fleet ({identity or data[CONF_ROUTER_HOST]})"
            return self.async_create_entry(title=title, data=data)

        return self.async_show_form(step_id="asic", data_schema=ASIC_SCHEMA)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return AsicFleetOptionsFlow()


class AsicFleetOptionsFlow(OptionsFlow):
    """Polling cadence, discovery behaviour and alert thresholds."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            # Aliases are managed by the assign_name service, not this form —
            # carry the existing map through untouched.
            merged = {**self.config_entry.options, **user_input}
            for key in (
                CONF_SCAN_INTERVAL,
                CONF_TEMP_WARN,
                CONF_TEMP_CRIT,
                CONF_HASHRATE_WARN_PCT,
                CONF_FAN_MIN_RPM,
                CONF_OFFLINE_GRACE,
                CONF_FAIL_THRESHOLD,
            ):
                if key in merged:
                    merged[key] = int(merged[key])
            if CONF_HW_ERROR_PCT in merged:
                merged[CONF_HW_ERROR_PCT] = float(merged[CONF_HW_ERROR_PCT])
            return self.async_create_entry(data=merged)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): NumberSelector(
                    NumberSelectorConfig(min=10, max=600, mode=NumberSelectorMode.BOX)
                ),
                vol.Optional(
                    CONF_HOSTNAME_PATTERN,
                    default=options.get(
                        CONF_HOSTNAME_PATTERN, DEFAULT_HOSTNAME_PATTERN
                    ),
                ): TextSelector(),
                vol.Optional(
                    CONF_PROBE_UNKNOWN,
                    default=options.get(CONF_PROBE_UNKNOWN, DEFAULT_PROBE_UNKNOWN),
                ): BooleanSelector(),
                vol.Optional(
                    CONF_BLOCKLIST,
                    default=options.get(CONF_BLOCKLIST, DEFAULT_BLOCKLIST),
                ): TextSelector(),
                vol.Optional(
                    CONF_MARKER_LIST,
                    default=options.get(CONF_MARKER_LIST, DEFAULT_MARKER_LIST),
                ): TextSelector(),
                vol.Optional(
                    CONF_TEMP_WARN,
                    default=options.get(CONF_TEMP_WARN, DEFAULT_TEMP_WARN),
                ): NumberSelector(
                    NumberSelectorConfig(min=40, max=120, mode=NumberSelectorMode.BOX)
                ),
                vol.Optional(
                    CONF_TEMP_CRIT,
                    default=options.get(CONF_TEMP_CRIT, DEFAULT_TEMP_CRIT),
                ): NumberSelector(
                    NumberSelectorConfig(min=40, max=130, mode=NumberSelectorMode.BOX)
                ),
                vol.Optional(
                    CONF_HASHRATE_WARN_PCT,
                    default=options.get(
                        CONF_HASHRATE_WARN_PCT, DEFAULT_HASHRATE_WARN_PCT
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(min=10, max=100, mode=NumberSelectorMode.BOX)
                ),
                vol.Optional(
                    CONF_FAN_MIN_RPM,
                    default=options.get(CONF_FAN_MIN_RPM, DEFAULT_FAN_MIN_RPM),
                ): NumberSelector(
                    NumberSelectorConfig(min=0, max=10000, mode=NumberSelectorMode.BOX)
                ),
                vol.Optional(
                    CONF_HW_ERROR_PCT,
                    default=options.get(CONF_HW_ERROR_PCT, DEFAULT_HW_ERROR_PCT),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=100, step=0.1, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Optional(
                    CONF_OFFLINE_GRACE,
                    default=options.get(CONF_OFFLINE_GRACE, DEFAULT_OFFLINE_GRACE),
                ): NumberSelector(
                    NumberSelectorConfig(min=30, max=3600, mode=NumberSelectorMode.BOX)
                ),
                vol.Optional(
                    CONF_FAIL_THRESHOLD,
                    default=options.get(CONF_FAIL_THRESHOLD, DEFAULT_FAIL_THRESHOLD),
                ): NumberSelector(
                    NumberSelectorConfig(min=1, max=10, mode=NumberSelectorMode.BOX)
                ),
                vol.Optional(
                    CONF_ALLOW_REBOOT,
                    default=options.get(CONF_ALLOW_REBOOT, True),
                ): BooleanSelector(),
                vol.Optional(
                    CONF_ALLOW_POOL_WRITE,
                    default=options.get(CONF_ALLOW_POOL_WRITE, False),
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
