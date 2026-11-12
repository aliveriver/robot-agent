"""NetworkManager D-Bus adapter restricted to one actual Wi-Fi interface.

Uses system Python's dbus-python. Credentials travel in D-Bus dictionaries,
never in shell commands. The adapter is created inside the network worker.
"""

from __future__ import annotations

import hashlib
import time
import uuid

from .network import NetworkError
from .protocol import Request

NM = 'org.freedesktop.NetworkManager'
ROOT = '/org/freedesktop/NetworkManager'
SETTINGS = ROOT+'/Settings'
CONNECTION = NM+'.Settings.Connection'
PROPERTIES = 'org.freedesktop.DBus.Properties'


class NetworkManagerDriver:
    """Implement profile operations without ever selecting an Ethernet device."""
    def __init__(self, interface: str) -> None:
        import dbus
        from dbus.mainloop import NULL_MAIN_LOOP
        self.dbus = dbus
        # Worker calls are synchronous; don't inherit the BLE thread's GLib loop.
        self.bus = dbus.SystemBus(private=True, mainloop=NULL_MAIN_LOOP)
        # GLib dispatches the disconnect from close(); the bus default calls _exit(1).
        # Only this short-lived worker connection should close, not the BLE daemon.
        self.bus.set_exit_on_disconnect(False)
        self.manager = self._iface(ROOT, NM)
        self.settings = self._iface(SETTINGS, NM+'.Settings')
        try:
            self.device = str(self.manager.GetDeviceByIpIface(interface))
            if int(self._get(self.device, NM+'.Device', 'DeviceType')) != 2:
                raise NetworkError('NO_WIFI')
        except Exception:
            self.bus.close()
            raise NetworkError('NO_WIFI') from None

    def _iface(self, path: str, interface: str):
        return self.dbus.Interface(self.bus.get_object(NM,path),interface)

    def _get(self, path: str, interface: str, key: str):
        return self._iface(path,PROPERTIES).Get(interface,key)

    def current_profile(self) -> str | None:
        active = str(self._get(self.device,NM+'.Device','ActiveConnection'))
        return None if active == '/' else str(self._get(active,NM+'.Connection.Active','Connection'))

    def _prefix(self, request: Request) -> str:
        return 'robot-hotspot-'+hashlib.sha256(request.ssid.encode()).hexdigest()[:12]+'-'

    def create_profile(self, request: Request) -> str:
        dbus = self.dbus
        identity = str(uuid.uuid4())
        settings = dbus.Dictionary({
            'connection': dbus.Dictionary({'id':self._prefix(request)+identity[:8],
                'uuid':identity,'type':'802-11-wireless','interface-name':str(self._get(self.device,NM+'.Device','Interface')),
                'autoconnect':dbus.Boolean(True),'autoconnect-priority':dbus.Int32(50)},signature='sv'),
            '802-11-wireless':dbus.Dictionary({'ssid':dbus.ByteArray(request.ssid.encode()),
                'mode':'infrastructure','hidden':dbus.Boolean(request.hidden),
                'security':'802-11-wireless-security'},signature='sv'),
            '802-11-wireless-security':dbus.Dictionary({'key-mgmt':'wpa-psk','psk':request.password},signature='sv'),
            'ipv4':dbus.Dictionary({'method':'auto'},signature='sv'),
            'ipv6':dbus.Dictionary({'method':'auto'},signature='sv'),
        },signature='sa{sv}')
        try:
            path,_ = self.settings.AddConnection2(settings,dbus.UInt32(1),dbus.Dictionary({},signature='sv'))
            return str(path)
        except dbus.DBusException as exc:
            code = 'NOT_ALLOWED' if 'NotAuthorized' in exc.get_dbus_name() or 'PermissionDenied' in exc.get_dbus_name() else 'CONNECT_FAILED'
            raise NetworkError(code) from None

    def activate(self, profile: str) -> None:
        try:
            self.manager.ActivateConnection(profile,self.device,'/')
        except self.dbus.DBusException:
            raise NetworkError('CONNECT_FAILED') from None

    def current_ipv4(self) -> str:
        if int(self._get(self.device,NM+'.Device','State')) != 100:
            raise NetworkError('NOT_CONNECTED')
        path = str(self._get(self.device,NM+'.Device','Ip4Config'))
        if path == '/':
            raise NetworkError('MISSING_IP')
        entries = self._get(path,NM+'.IP4Config','AddressData')
        for entry in entries:
            address = str(entry.get('address',''))
            if address and not address.startswith('169.254.'):
                return address
        raise NetworkError('MISSING_IP')

    def wait_ipv4(self, profile: str, timeout: float) -> str:
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            state=int(self._get(self.device,NM+'.Device','State'))
            if state == 120:
                _,reason=self._get(self.device,NM+'.Device','StateReason')
                raise NetworkError('AUTH_FAILED' if int(reason) in {7,8,9,10,11} else 'CONNECT_FAILED')
            if state == 100 and self.current_profile() == profile:
                return self.current_ipv4()
            time.sleep(.5)
        raise NetworkError('TIMEOUT')

    def remove_profile(self, profile: str) -> None:
        self._iface(profile,CONNECTION).Delete()

    def prune_profiles(self, request: Request, keep: str) -> None:
        for path in self.settings.ListConnections():
            if str(path) == keep:
                continue
            settings=self._iface(str(path),CONNECTION).GetSettings()
            connection=settings.get('connection',{})
            if str(connection.get('id','')).startswith(self._prefix(request)) and connection.get('type') == '802-11-wireless':
                self.remove_profile(str(path))

    def close(self) -> None:
        """Release this worker's private D-Bus connection."""
        self.bus.close()
