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
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
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
    CONF_AUTO_CONFIGURE,
    CONF_BLOCKLIST,
    CONF_FAIL_THRESHOLD,
    CONF_FAN_MIN_RPM,
    CONF_HA_IP,
    CONF_HASHRATE_WARN_PCT,
    CONF_HOSTNAME_PATTERN,
    CONF_HW_ERROR_PCT,
    CONF_MARKER_LIST,
    CONF_OFFLINE_GRACE,
    CONF_PIN_IPS,
    CONF_PROBE_SUBNETS,
    CONF_PROBE_UNKNOWN,
    CONF_ROUTER_HOST,
    CONF_ROUTER_PASSWORD,
    CONF_ROUTER_PORT,
    CONF_ROUTER_TLS,
    CONF_ROUTER_USERNAME,
    CONF_ROUTER_VERIFY_SSL,
    CONF_SCAN_INTERVAL,
    CONF_TEMP_CRIT,
    CONF_TEMP_WARN,
    DEFAULT_ASIC_PORT,
    DEFAULT_ASIC_USERNAME,
    DEFAULT_AUTO_CONFIGURE,
    DEFAULT_BLOCKLIST,
    DEFAULT_FAIL_THRESHOLD,
    DEFAULT_FAN_MIN_RPM,
    DEFAULT_HASHRATE_WARN_PCT,
    DEFAULT_HOSTNAME_PATTERN,
    DEFAULT_HW_ERROR_PCT,
    DEFAULT_MARKER_LIST,
    DEFAULT_OFFLINE_GRACE,
    DEFAULT_PIN_IPS,
    DEFAULT_PROBE_UNKNOWN,
    DEFAULT_ROUTER_USERNAME,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TEMP_CRIT,
    DEFAULT_TEMP_WARN,
    DOMAIN,
)
from .mikrotik import MikrotikAuthError, MikrotikClient, MikrotikError
from .provision import USERNAME, new_password, onboarding_script

_LOGGER = logging.getLogger(__name__)

MANUAL_SCHEMA = vol.Schema(
    {
        vol.Required(
            CONF_ROUTER_USERNAME, default=DEFAULT_ROUTER_USERNAME
        ): TextSelector(),
        vol.Required(CONF_ROUTER_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Optional(CONF_ROUTER_TLS, default=True): BooleanSelector(),
        vol.Optional(CONF_ROUTER_PORT, default=0): NumberSelector(
            NumberSelectorConfig(min=0, max=65535, mode=NumberSelectorMode.BOX)
        ),
        vol.Optional(CONF_ROUTER_VERIFY_SSL, default=False): BooleanSelector(),
    }
)


async def _source_ip(hass, target: str) -> str:
    from homeassistant.components import network

    try:
        return await network.async_get_source_ip(hass, target_ip=target)
    except Exception:
        return ""


async def _suggest_gateway(hass) -> str:
    """HA's own gateway (x.y.z.1): the core router, seen from HA's segment."""
    from homeassistant.components import network

    try:
        ip = await network.async_get_source_ip(hass)
    except Exception:
        return ""
    parts = ip.split(".")
    if len(parts) != 4 or ip.startswith("127."):
        return ""
    return ".".join([*parts[:3], "1"])


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
        self._host = ""
        self._ha_ip = ""
        self._password = ""

    async def _probe_router(self, data: dict[str, Any]) -> str | None:
        client = MikrotikClient(
            async_get_clientsession(self.hass, verify_ssl=False),
            data[CONF_ROUTER_HOST],
            data[CONF_ROUTER_USERNAME],
            data[CONF_ROUTER_PASSWORD],
            tls=data.get(CONF_ROUTER_TLS, True),
            verify_ssl=data.get(CONF_ROUTER_VERIFY_SSL, False),
            port=data.get(CONF_ROUTER_PORT) or None,
        )
        try:
            identity = await client.identity()
        except MikrotikAuthError:
            return "invalid_auth"
        except MikrotikError:
            return "cannot_connect"
        self._router = dict(data)
        self._router["identity"] = identity.get("name")
        return None

    def _site_health_entries(self) -> list:
        return self.hass.config_entries.async_entries("site_health")

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Router address (HA's own gateway by default) and how to log in."""
        if user_input is not None:
            host = user_input[CONF_ROUTER_HOST].strip()
            await self.async_set_unique_id(f"{DOMAIN}_{host}")
            self._abort_if_unique_id_configured()
            self._host = host
            self._ha_ip = await _source_ip(self.hass, host)
            method = user_input["method"]
            if method == "site_health":
                return await self.async_step_site_health()
            if method == "script":
                self._password = new_password()
                return await self.async_step_onboard()
            return await self.async_step_manual()
        methods = ["script", "manual"]
        if self._site_health_entries():
            methods.insert(0, "site_health")
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ROUTER_HOST, default=await _suggest_gateway(self.hass)
                ): TextSelector(),
                vol.Required("method", default=methods[0]): SelectSelector(
                    SelectSelectorConfig(
                        options=methods,
                        translation_key="method",
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_site_health(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reuse the login Site Health already has for this router."""
        entries = self._site_health_entries()
        match = [e for e in entries if e.data.get("host") == self._host] or entries
        sh = match[0].data
        data = {
            CONF_ROUTER_HOST: self._host,
            CONF_ROUTER_USERNAME: sh.get("username"),
            CONF_ROUTER_PASSWORD: sh.get("password"),
            CONF_ROUTER_TLS: sh.get("tls", True),
            CONF_ROUTER_VERIFY_SSL: sh.get("verify_ssl", False),
            CONF_ROUTER_PORT: sh.get("port") or 0,
            CONF_HA_IP: sh.get("ha_ip") or self._ha_ip,
        }
        err = await self._probe_router(data)
        if err:
            return self.async_abort(reason=f"site_health_{err}")
        return await self.async_step_asic()

    async def async_step_onboard(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {
                CONF_ROUTER_HOST: self._host,
                CONF_ROUTER_USERNAME: USERNAME,
                CONF_ROUTER_PASSWORD: self._password,
                CONF_ROUTER_TLS: True,
                CONF_ROUTER_VERIFY_SSL: False,
                CONF_ROUTER_PORT: 0,
                CONF_HA_IP: self._ha_ip,
            }
            err = await self._probe_router(data)
            if not err:
                return await self.async_step_asic()
            errors["base"] = err
        return self.async_show_form(
            step_id="onboard",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={
                "host": self._host,
                "ha_ip": self._ha_ip or "?",
                "script": onboarding_script(
                    ha_ip=self._ha_ip or "HA_IP", password=self._password
                ),
            },
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {
                CONF_ROUTER_HOST: self._host,
                CONF_HA_IP: self._ha_ip,
                **user_input,
            }
            err = await self._probe_router(data)
            if not err:
                return await self.async_step_asic()
            errors["base"] = err
        return self.async_show_form(
            step_id="manual", data_schema=MANUAL_SCHEMA, errors=errors
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
                    CONF_PROBE_SUBNETS,
                    default=options.get(CONF_PROBE_SUBNETS, ""),
                ): TextSelector(),
                vol.Optional(
                    CONF_PIN_IPS,
                    default=options.get(CONF_PIN_IPS, DEFAULT_PIN_IPS),
                ): BooleanSelector(),
                vol.Optional(
                    CONF_AUTO_CONFIGURE,
                    default=options.get(CONF_AUTO_CONFIGURE, DEFAULT_AUTO_CONFIGURE),
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
