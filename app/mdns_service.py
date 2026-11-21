"""
Advertise this server as <hostname>.local on the shop's WiFi, so phones can
reach it without typing an IP address.

Requires: pip install zeroconf
Note: mDNS only works within the same WiFi network / broadcast domain —
it will not resolve across a guest network that isolates clients from each
other or from the router-attached PC, so make sure client isolation is off
for the SSID customers use.
"""
import socket

from zeroconf import ServiceInfo, Zeroconf

from . import config

_zeroconf_instance = None


def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # doesn't actually send anything
        return s.getsockname()[0]
    finally:
        s.close()


def start_mdns():
    global _zeroconf_instance
    ip = get_local_ip()
    hostname = f"{config.MDNS_HOSTNAME}.local."

    info = ServiceInfo(
        "_http._tcp.local.",
        f"{config.MDNS_HOSTNAME}._http._tcp.local.",
        addresses=[socket.inet_aton(ip)],
        port=config.PORT,
        server=hostname,
    )
    _zeroconf_instance = Zeroconf()
    _zeroconf_instance.register_service(info)
    print(f"[mDNS] Advertising {hostname} -> {ip}:{config.PORT}")


def stop_mdns():
    global _zeroconf_instance
    if _zeroconf_instance:
        _zeroconf_instance.close()
        _zeroconf_instance = None
