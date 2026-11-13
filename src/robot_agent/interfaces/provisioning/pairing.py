"""No-display pairing agent for the provisioning service's encrypted BLE link."""

import dbus
import dbus.service

from .protocol import SERVICE_UUID

AGENT='org.bluez.Agent1'
PATH='/com/xlab/robot/provisioning/agent'


class Rejected(dbus.DBusException):
    _dbus_error_name='org.bluez.Error.Rejected'


class PairingAgent(dbus.service.Object):
    """Accept Just Works pairing; do not authorize unrelated new profiles."""
    def __init__(self,bus): super().__init__(bus,PATH)

    @dbus.service.method(AGENT)
    def Release(self): pass

    @dbus.service.method(AGENT,in_signature='ou')
    def RequestConfirmation(self,device,passkey): pass

    @dbus.service.method(AGENT,in_signature='o')
    def RequestAuthorization(self,device): pass

    @dbus.service.method(AGENT,in_signature='os')
    def AuthorizeService(self,device,uuid):
        if str(uuid).lower() not in {SERVICE_UUID,'00001800-0000-1000-8000-00805f9b34fb','00001801-0000-1000-8000-00805f9b34fb'}:
            raise Rejected('UNSUPPORTED_PROFILE')

    @dbus.service.method(AGENT)
    def Cancel(self): pass
