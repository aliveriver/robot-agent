"""Real private D-Bus closure must not terminate the provisioning process."""

from importlib.util import find_spec
from pathlib import Path
import subprocess
import sys
import unittest


HAS_SYSTEM_DBUS = (
    sys.platform.startswith('linux')
    and find_spec('dbus') is not None
    and find_spec('gi') is not None
    and Path('/run/dbus/system_bus_socket').exists()
)

SCRIPT = '''
import types
from unittest.mock import patch
from dbus.mainloop.glib import DBusGMainLoop, threads_init
from gi.repository import GLib
from src.robot_agent.interfaces.provisioning.network_manager import NetworkManagerDriver
from src.robot_agent.interfaces.provisioning.network import NetworkError

DBusGMainLoop(set_as_default=True)
threads_init()
manager=types.SimpleNamespace(GetDeviceByIpIface=lambda name: '/unused/wifi')
# The bus is real; stub only NM device lookup so no network is changed or required.
with patch.object(NetworkManagerDriver,'_iface',return_value=manager), \\
     patch.object(NetworkManagerDriver,'_get',return_value={device_type}):
    try:
        driver=NetworkManagerDriver('test-wifi')
    except NetworkError:
        driver=None
assert (driver is None) == {initialization_fails}
loop=GLib.MainLoop()
def release():
    if driver is not None:
        driver.close()
    GLib.timeout_add(100,lambda:(loop.quit(),False)[1])
    return False
GLib.idle_add(release)
loop.run()
print('process survived private bus cleanup',flush=True)
'''

THREADED_SCRIPT = '''
import concurrent.futures
import time
import types
from unittest.mock import patch
from dbus.mainloop.glib import DBusGMainLoop, threads_init
from gi.repository import GLib
from src.robot_agent.interfaces.provisioning.network_manager import NetworkManagerDriver

DBusGMainLoop(set_as_default=True)
threads_init()
loop=GLib.MainLoop()
manager=types.SimpleNamespace(GetDeviceByIpIface=lambda name: '/unused/wifi')
def worker():
    with patch.object(NetworkManagerDriver,'_iface',return_value=manager), \\
         patch.object(NetworkManagerDriver,'_get',return_value=2):
        for _ in range(10):
            driver=NetworkManagerDriver('test-wifi')
            driver.close()
            del driver
            time.sleep(0.01)
with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
    pending=executor.submit(worker)
    def finished():
        if not pending.done():
            return True
        loop.quit()
        return False
    GLib.timeout_add(20,finished)
    loop.run()
    pending.result()
print('process survived threaded private bus cleanup',flush=True)
'''


@unittest.skipUnless(HAS_SYSTEM_DBUS, 'requires Linux system D-Bus and GLib')
class PrivateBusLifecycleTest(unittest.TestCase):
    """Exercise libdbus dispatch in child processes to detect native _exit(1)."""

    def assert_survives(self, *, device_type: int, initialization_fails: bool) -> None:
        """Keep an unexpected native exit from terminating the test runner itself."""
        script = SCRIPT.format(device_type=device_type, initialization_fails=initialization_fails)
        result = subprocess.run(
            [sys.executable, '-B', '-c', script], capture_output=True, text=True, timeout=6,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('process survived private bus cleanup', result.stdout)

    def test_normal_close_keeps_service_process_alive(self):
        self.assert_survives(device_type=2, initialization_fails=False)

    def test_constructor_failure_cleanup_keeps_service_process_alive(self):
        self.assert_survives(device_type=1, initialization_fails=True)

    def test_repeated_worker_cleanup_does_not_corrupt_glib_loop(self):
        try:
            result = subprocess.run(
                [sys.executable, '-B', '-c', THREADED_SCRIPT],
                capture_output=True, text=True, timeout=4,
            )
        except subprocess.TimeoutExpired as exc:
            self.fail(f'worker cleanup left the GLib loop stuck: {exc.stderr!r}')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, '')
        self.assertIn('process survived threaded private bus cleanup', result.stdout)


if __name__ == '__main__':
    unittest.main()
