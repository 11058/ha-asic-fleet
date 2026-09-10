# Lease-script body only — paste under IP > DHCP Server > <server> > Lease Script
# if you prefer WinBox over importing setup.rsc.
#
# Identity resolution order:
#   1. DHCP hostname, when the marker list holds an entry with that comment
#   2. `mac:<MAC>` otherwise — this is what covers miners (e.g. Antminer L9)
#      that never send a hostname in their DHCP request
#
# On bind:   refresh the miner's entry in asic_blocked to its current IP and
#            drop live connections so an existing stratum session dies at once.
# On expiry: remove the IP entry. The marker stays — block intent must survive
#            a lease expiring.

:local ip $leaseActIP
:local mac $leaseActMAC
:local name ""
:do { :set name [/ip/dhcp-server/lease/get [find active-address=$ip] host-name] } on-error={}

:local marker ("mac:" . $mac)
:if ($name != "" and [:len [/ip/firewall/address-list/find list=asic_blocked_hosts comment=$name]] > 0) do={
  :set marker $name
}

:if ([:len [/ip/firewall/address-list/find list=asic_blocked_hosts comment=$marker]] > 0) do={
  :if ($leaseBound = 1) do={
    /ip/firewall/address-list/remove [find list=asic_blocked comment=$marker]
    /ip/firewall/address-list/add list=asic_blocked address=$ip comment=$marker
    /ip/firewall/connection/remove [find src-address~("^" . $ip . ":")]
  } else={
    /ip/firewall/address-list/remove [find list=asic_blocked comment=$marker]
  }
}
