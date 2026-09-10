# =============================================================================
# RouterOS setup for the ASIC Fleet Home Assistant integration.
#
# Idempotent — safe to re-run. Creates / updates:
#   - a dedicated `ha-asic` user restricted to Home Assistant's source IP
#   - the two address-lists' firewall drop rule
#   - a DHCP lease-script that keeps blocked IPs correct across lease renewals,
#     matching either by DHCP hostname or by MAC (for miners like the Antminer
#     L9 that never publish a hostname)
#   - the REST service, restricted to Home Assistant's IP
#
# Run:  /import file-name=setup.rsc      (or paste into a terminal session)
# =============================================================================

# ---------------------------- TUNABLES ---------------------------------------
:local haUserPassword   "REPLACE_WITH_LONG_RANDOM_PASSWORD"
:local haAddress        "10.100.20.5/32"     ;# Home Assistant's IP
:local wanList          "WAN"                ;# /interface/list/print
:local dhcpServer       "dhcp-servers"       ;# DHCP server serving the racks
:local useTls           false                ;# true => www-ssl (443), false => www (80)
# -----------------------------------------------------------------------------

:local blockList  "asic_blocked"
:local markerList "asic_blocked_hosts"

:put "==> ASIC Fleet — RouterOS setup"

# 1. Restricted API user. `rest-api` policy is required for the REST endpoint;
#    `write` is needed to edit address-lists.
:if ([:len [/user/group/find name=ha-api]] = 0) do={
  /user/group/add name=ha-api policy=read,write,api,rest-api
  :put "  created group ha-api"
} else={
  /user/group/set [/user/group/find name=ha-api] policy=read,write,api,rest-api
  :put "  updated group ha-api"
}
:if ([:len [/user/find name=ha-asic]] = 0) do={
  /user/add name=ha-asic group=ha-api password=$haUserPassword address=$haAddress \
            comment="ASIC Fleet HA integration"
  :put "  created user ha-asic (restricted to $haAddress)"
} else={
  /user/set [/user/find name=ha-asic] group=ha-api address=$haAddress \
            comment="ASIC Fleet HA integration"
  :put "  updated user ha-asic (password left as-is)"
}

# 2. The drop rule. Everything the integration does ultimately depends on this
#    one rule existing and sitting above any accept rule for the same traffic.
:if ([:len [/ip/firewall/filter/find comment="asic-fleet-block"]] = 0) do={
  /ip/firewall/filter/add chain=forward src-address-list=$blockList \
      out-interface-list=$wanList action=drop comment="asic-fleet-block" \
      place-before=([:pick [/ip/firewall/filter/find chain=forward] 0])
  :put "  added drop rule ($blockList -> $wanList)"
} else={
  :put "  drop rule already present"
}

# 3. Lease-script. Block intent lives in $markerList as either the miner's DHCP
#    hostname or `mac:<MAC>`; this script mirrors it into $blockList with the
#    address the miner currently holds.
:put "==> DHCP lease-script on $dhcpServer"
:local leaseScript "\
:local ip \$leaseActIP\
\n:local mac \$leaseActMAC\
\n:local name \"\"\
\n:do {:set name [/ip/dhcp-server/lease/get [find active-address=\$ip] host-name]} on-error={}\
\n:local marker (\"mac:\" . \$mac)\
\n:if (\$name != \"\" and [:len [/ip/firewall/address-list/find list=asic_blocked_hosts comment=\$name]] > 0) do={\
\n  :set marker \$name\
\n}\
\n:if ([:len [/ip/firewall/address-list/find list=asic_blocked_hosts comment=\$marker]] > 0) do={\
\n  :if (\$leaseBound = 1) do={\
\n    /ip/firewall/address-list/remove [find list=asic_blocked comment=\$marker]\
\n    /ip/firewall/address-list/add list=asic_blocked address=\$ip comment=\$marker\
\n    /ip/firewall/connection/remove [find src-address~(\"^\" . \$ip . \":\")]\
\n  } else={\
\n    /ip/firewall/address-list/remove [find list=asic_blocked comment=\$marker]\
\n  }\
\n}"
/ip/dhcp-server/set [find name=$dhcpServer] lease-script=$leaseScript
:put "  lease-script installed"

# 4. REST service.
:if ($useTls) do={
  /ip/service/set www-ssl address=$haAddress
  /ip/service/enable www-ssl
  :put "==> www-ssl enabled, restricted to $haAddress"
} else={
  /ip/service/set www address=$haAddress
  /ip/service/enable www
  :put "==> www (plain HTTP) enabled, restricted to $haAddress"
}

:put ""
:put "==> verify:"
:put "    /user/print where name=ha-asic"
:put "    /ip/firewall/filter/print where comment=asic-fleet-block"
:put "    /ip/firewall/address-list/print where list~\"^asic_\""
:put "    /ip/dhcp-server/print detail where name=$dhcpServer"
