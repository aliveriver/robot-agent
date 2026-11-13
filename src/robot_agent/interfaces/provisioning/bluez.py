"""BlueZ GATT application: public identity, encrypted request and status values."""

from __future__ import annotations

import json
import dbus
import dbus.service

from .protocol import INFO_UUID, REQUEST_UUID, SERVICE_UUID, STATUS_UUID, FrameDecoder, ProtocolError

PROPERTIES='org.freedesktop.DBus.Properties'
OBJECTS='org.freedesktop.DBus.ObjectManager'
GATT_SERVICE='org.bluez.GattService1'
CHARACTERISTIC='org.bluez.GattCharacteristic1'
ROOT='/com/xlab/robot/provisioning'


class InvalidRequest(dbus.DBusException):
    """Public GATT error with a stable code, never the original payload."""
    _dbus_error_name='org.bluez.Error.Failed'


class PropertyObject(dbus.service.Object):
    """Expose read-only D-Bus object properties to BlueZ."""
    def __init__(self,bus,path,interface,properties):
        super().__init__(bus,path)
        self.path,self.interface,self.properties=path,interface,properties

    @dbus.service.method(PROPERTIES,in_signature='s',out_signature='a{sv}')
    def GetAll(self,interface):
        return self.properties if interface == self.interface else {}

    @dbus.service.method(PROPERTIES,in_signature='ss',out_signature='v')
    def Get(self,interface,key):
        if interface != self.interface or key not in self.properties:
            raise InvalidRequest('BAD_PROPERTY')
        return self.properties[key]


class Characteristic(PropertyObject):
    """Handle offset-aware reads and validated writes for one characteristic."""
    def __init__(self,bus,path,uuid,flags,controller,decoder):
        super().__init__(bus,path,CHARACTERISTIC,{'UUID':uuid,'Service':dbus.ObjectPath(ROOT+'/service0'),
            'Flags':dbus.Array(flags,signature='s')})
        self.uuid,self.controller,self.decoder=uuid,controller,decoder
        self.bluez_owner=str(bus.get_name_owner('org.bluez'))

    def _authorize(self,sender):
        # Only BlueZ may supply device options: a local D-Bus caller must not
        # impersonate a paired phone to make this root service change Wi-Fi.
        if not sender or str(sender) != self.bluez_owner:
            raise InvalidRequest('NOT_ALLOWED')

    def _peer(self,options):
        peer=str(options.get('device',''))
        if not peer.startswith('/org/bluez/'):
            raise InvalidRequest('NO_PEER')
        return peer

    @dbus.service.method(CHARACTERISTIC,in_signature='a{sv}',out_signature='ay',sender_keyword='sender')
    def ReadValue(self,options,sender=None):
        if self.uuid == INFO_UUID:
            value=self.controller.info()
        elif self.uuid == STATUS_UUID:
            self._authorize(sender)
            value=self.controller.read_status(self._peer(options))
        else:
            raise InvalidRequest('NOT_READABLE')
        raw=json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()
        offset=int(options.get('offset',0))
        if not 0<=offset<=len(raw):
            raise InvalidRequest('BAD_OFFSET')
        return dbus.ByteArray(raw[offset:])

    @dbus.service.method(CHARACTERISTIC,in_signature='aya{sv}',out_signature='',sender_keyword='sender')
    def WriteValue(self,value,options,sender=None):
        self._authorize(sender)
        if self.uuid != REQUEST_UUID or int(options.get('offset',0)) != 0 or options.get('prepare-authorize',False):
            raise InvalidRequest('BAD_WRITE')
        peer=self._peer(options)
        try:
            requests=self.decoder.feed(peer,bytes(value))
            for request in requests:
                self.controller.submit(peer,request)
        except ProtocolError as exc:
            raise InvalidRequest(str(exc)) from None


class Application(dbus.service.Object):
    """Own one service tree and erase partial writes when a phone disconnects."""
    def __init__(self,bus,controller):
        super().__init__(bus,ROOT)
        self.decoder=FrameDecoder()
        self.service=PropertyObject(bus,ROOT+'/service0',GATT_SERVICE,{'UUID':SERVICE_UUID,'Primary':dbus.Boolean(True)})
        self.characteristics=[
            Characteristic(bus,ROOT+'/service0/info',INFO_UUID,['read'],controller,self.decoder),
            Characteristic(bus,ROOT+'/service0/request',REQUEST_UUID,['write','encrypt-write'],controller,self.decoder),
            Characteristic(bus,ROOT+'/service0/status',STATUS_UUID,['read','encrypt-read'],controller,self.decoder),
        ]
        self.receiver=bus.add_signal_receiver(self._changed,dbus_interface=PROPERTIES,signal_name='PropertiesChanged',
            bus_name='org.bluez',path_keyword='path')

    def _changed(self,interface,changed,invalidated,path):
        if interface == 'org.bluez.Device1' and changed.get('Connected') is not None and not changed['Connected']:
            self.decoder.discard(str(path))

    @dbus.service.method(OBJECTS,out_signature='a{oa{sa{sv}}}')
    def GetManagedObjects(self):
        return {obj.path:{obj.interface:obj.properties} for obj in [self.service,*self.characteristics]}


class Advertisement(PropertyObject):
    """Advertise the custom UUID and a short, stable device name."""
    def __init__(self,bus,name):
        super().__init__(bus,ROOT+'/advertisement','org.bluez.LEAdvertisement1',
            {'Type':'peripheral','ServiceUUIDs':dbus.Array([SERVICE_UUID],signature='s'),'LocalName':name})

    @dbus.service.method('org.bluez.LEAdvertisement1')
    def Release(self):
        pass
