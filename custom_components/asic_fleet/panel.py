"""Sidebar panel "ASIC": one web component served from the integration."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components import panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

WWW = Path(__file__).parent / "www"
BUNDLE = "asic-fleet-panel.js"
STATIC = "/asic_fleet_static"
KEY = f"{DOMAIN}_panel"


async def async_setup_panel(hass: HomeAssistant) -> None:
    """Register once per HA run; later entries reuse it."""
    if hass.data.get(KEY):
        return
    hass.data[KEY] = True
    from .websocket_api import async_register_websocket

    async_register_websocket(hass)
    if not (WWW / BUNDLE).is_file():
        _LOGGER.warning("Panel bundle missing, ASIC panel not registered")
        return
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC, str(WWW), cache_headers=False)]
    )
    version = (await async_get_integration(hass, DOMAIN)).version or "0"
    # Also on every dashboard: the same page as a card (custom:asic-fleet-card).
    from homeassistant.components.frontend import add_extra_js_url

    add_extra_js_url(hass, f"{STATIC}/{BUNDLE}?v={version}")
    await panel_custom.async_register_panel(
        hass,
        webcomponent_name="asic-fleet-panel",
        frontend_url_path="asic-fleet",
        module_url=f"{STATIC}/{BUNDLE}?v={version}",
        sidebar_title="ASIC",
        sidebar_icon="mdi:pickaxe",
        require_admin=False,
        config={},
    )
