"""Minimal async RouterOS REST client.

Only the handful of endpoints the fleet module needs. RouterOS v7 REST verbs:
    GET    /rest/<path>            list
    PUT    /rest/<path>            add
    PATCH  /rest/<path>/<id>       set
    DELETE /rest/<path>/<id>       remove
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
from aiohttp import BasicAuth, ClientError, ClientResponseError, ClientTimeout

from .const import HTTP_TIMEOUT

_LOGGER = logging.getLogger(__name__)


class MikrotikError(Exception):
    """Router did not answer or answered with an error."""


class MikrotikAuthError(MikrotikError):
    """Credentials rejected."""


class MikrotikClient:
    """Talks to one RouterOS box over its REST API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        username: str,
        password: str,
        *,
        tls: bool = False,
        verify_ssl: bool = False,
    ) -> None:
        self._session = session
        self._host = host
        self._auth = BasicAuth(username, password)
        self._base = f"{'https' if tls else 'http'}://{host}/rest"
        # RouterOS' self-signed cert is the norm here; the option exists so a
        # site with a real cert can turn verification back on.
        self._ssl: Any = None if not tls else verify_ssl
        self._timeout = ClientTimeout(total=HTTP_TIMEOUT)

    @property
    def host(self) -> str:
        return self._host

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> Any:
        url = f"{self._base}/{path.lstrip('/')}"
        try:
            async with self._session.request(
                method,
                url,
                auth=self._auth,
                json=payload,
                timeout=self._timeout,
                ssl=self._ssl,
            ) as resp:
                if resp.status in (401, 403):
                    raise MikrotikAuthError(
                        f"RouterOS rejected credentials for {self._host} "
                        f"({resp.status})"
                    )
                resp.raise_for_status()
                if resp.status == 204 or not resp.content_length:
                    text = await resp.text()
                    if not text.strip():
                        return None
                    return _loads(text)
                return await resp.json(content_type=None)
        except ClientResponseError as err:
            raise MikrotikError(
                f"{method} {path} failed: {err.status} {err.message}"
            ) from err
        except (ClientError, TimeoutError, OSError) as err:
            raise MikrotikError(f"{method} {path} failed: {err}") from err

    # --- reads ---------------------------------------------------------------

    async def identity(self) -> dict[str, Any]:
        data = await self._request("GET", "system/identity")
        return data if isinstance(data, dict) else {}

    async def resource(self) -> dict[str, Any]:
        data = await self._request("GET", "system/resource")
        return data if isinstance(data, dict) else {}

    async def leases(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "ip/dhcp-server/lease")
        return data if isinstance(data, list) else []

    async def address_lists(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "ip/firewall/address-list")
        return data if isinstance(data, list) else []

    async def bridge_hosts(self) -> list[dict[str, Any]]:
        """MAC -> bridge port map. Used only as a physical-location hint.

        Not every deployment runs a bridge (routed ports, switch chip only), so
        a failure here is downgraded to an empty map rather than an error.
        """
        try:
            data = await self._request("GET", "interface/bridge/host")
        except MikrotikError as err:
            _LOGGER.debug("bridge host table unavailable: %s", err)
            return []
        return data if isinstance(data, list) else []

    # --- writes --------------------------------------------------------------

    async def add_address_list_entry(
        self, list_name: str, address: str, comment: str
    ) -> dict[str, Any]:
        payload = {"list": list_name, "address": address, "comment": comment}
        data = await self._request("PUT", "ip/firewall/address-list", payload)
        return data if isinstance(data, dict) else {}

    async def remove_address_list_entry(self, entry_id: str) -> None:
        await self._request("DELETE", f"ip/firewall/address-list/{entry_id}")

    async def drop_connections_from(self, ip: str) -> None:
        """Kill in-flight connections so a block takes effect immediately.

        Without this a live stratum TCP session survives the new drop rule and
        the miner keeps hashing until the pool times it out.

        RouterOS stores `src-address` as `<ip>:<port>`, and its REST query
        syntax has no regex, so the filtering happens here on a deliberately
        narrow proplist rather than on the router.
        """
        try:
            conns = await self._request(
                "POST",
                "ip/firewall/connection/print",
                {".proplist": [".id", "src-address"]},
            )
        except MikrotikError as err:
            _LOGGER.debug("connection tracking query failed for %s: %s", ip, err)
            return
        if not isinstance(conns, list):
            return

        prefix = f"{ip}:"
        for conn in conns:
            src = conn.get("src-address", "")
            cid = conn.get(".id")
            if not cid or not (src == ip or src.startswith(prefix)):
                continue
            try:
                await self._request("DELETE", f"ip/firewall/connection/{cid}")
            except MikrotikError:  # entry may have expired between calls
                continue


def _loads(text: str) -> Any:
    import json

    try:
        return json.loads(text)
    except ValueError:
        return None
