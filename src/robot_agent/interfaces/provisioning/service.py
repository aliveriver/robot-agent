"""Independent BLE provisioning daemon, using only system Python and BlueZ/NM."""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
from pathlib import Path
import signal

from .controller import ProvisioningController
from .network import NetworkConnector


class NetworkBackend:
    """Create D-Bus connections on the worker that uses them, and always close them."""
    def __init__(self,interface,port): self.interface,self.port=interface,port
    def _run(self,request=None):
        from .network_manager import NetworkManagerDriver
        driver=NetworkManagerDriver(self.interface)
        try:
            connector=NetworkConnector(driver,port=self.port)
            return connector.connect(request) if request is not None else connector.snapshot()
        finally:
            driver.close()
    def connect(self,request): return self._run(request)
    def snapshot(self): return self._run()


def main() -> None:
    """Register encrypted GATT endpoints and release them on service shutdown."""
    import dbus
    from dbus.mainloop.glib import DBusGMainLoop, threads_init
    from gi.repository import GLib
    from .bluez import Advertisement, Application, ROOT
    from .pairing import PairingAgent, PATH

    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interface',default=os.getenv('PROVISIONING_INTERFACE','wlP1p1s0'))
    parser.add_argument('--adapter',default=os.getenv('PROVISIONING_ADAPTER','hci0'))
    parser.add_argument('--port',type=int,default=int(os.getenv('WS_PORT','8765')))
    args=parser.parse_args()
    if not args.adapter.startswith('hci') or not args.adapter[3:].isdigit():
        parser.error('invalid adapter')
    identity=hashlib.sha256(Path('/etc/machine-id').read_bytes().strip()).hexdigest()[:16]
    name='XL-'+identity[:4].upper()
    controller=ProvisioningController(NetworkBackend(args.interface,args.port),robot_id=identity,name=name)
    logging.basicConfig(level=logging.INFO,format='%(levelname)s %(message)s')
    threads_init();DBusGMainLoop(set_as_default=True)
    bus=dbus.SystemBus()
    adapter='/org/bluez/'+args.adapter
    properties=dbus.Interface(bus.get_object('org.bluez',adapter),'org.freedesktop.DBus.Properties')
    previous_pairable=bool(properties.Get('org.bluez.Adapter1','Pairable'))
    app=Application(bus,controller);advertisement=Advertisement(bus,name);agent=PairingAgent(bus)
    manager=dbus.Interface(bus.get_object('org.bluez','/org/bluez'),'org.bluez.AgentManager1')
    gatt=dbus.Interface(bus.get_object('org.bluez',adapter),'org.bluez.GattManager1')
    advertising=dbus.Interface(bus.get_object('org.bluez',adapter),'org.bluez.LEAdvertisingManager1')
    loop=GLib.MainLoop()
    registered=[]
    bluez_lost=False

    def owner_changed(name,old,new):
        nonlocal bluez_lost
        if str(name)=='org.bluez' and old and old != new:
            bluez_lost=True
            loop.quit()  # systemd restarts us and re-registers the GATT tree.

    bus.add_signal_receiver(owner_changed,signal_name='NameOwnerChanged',
        dbus_interface='org.freedesktop.DBus',bus_name='org.freedesktop.DBus',arg0='org.bluez')

    def failed(error):
        logging.error('Bluetooth registration failed: %s',type(error).__name__)
        loop.quit()

    try:
        properties.Set('org.bluez.Adapter1','Powered',dbus.Boolean(True))
        properties.Set('org.bluez.Adapter1','Pairable',dbus.Boolean(True))
        manager.RegisterAgent(PATH,'NoInputNoOutput');registered.append('agent')
        manager.RequestDefaultAgent(PATH)
        # Async calls allow BlueZ to inspect the exported objects while registering.
        def app_ready():
            registered.append('gatt')
            advertising.RegisterAdvertisement(advertisement.path,{},
                reply_handler=advertised,error_handler=failed)
        def advertised():
            registered.append('advertisement')
            logging.info('Provisioning ready: %s interface=%s',name,args.interface)
        gatt.RegisterApplication(ROOT,{},reply_handler=app_ready,error_handler=failed)
        for signum in [signal.SIGINT,signal.SIGTERM]:
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT,signum,lambda:(loop.quit(),False)[1])
        loop.run()
        if bluez_lost or 'advertisement' not in registered:
            raise RuntimeError('GATT registration failed')
    finally:
        for label,method,path in [('advertisement',advertising.UnregisterAdvertisement,advertisement.path),
                                  ('gatt',gatt.UnregisterApplication,ROOT),('agent',manager.UnregisterAgent,PATH)]:
            if label in registered:
                try: method(path)
                except dbus.DBusException: pass
        try: properties.Set('org.bluez.Adapter1','Pairable',dbus.Boolean(previous_pairable))
        except dbus.DBusException: pass
        controller.close()


if __name__=='__main__': main()
