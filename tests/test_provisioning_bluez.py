"""BlueZ transport authorization, exercised with the robot's system Python."""
import types
import unittest

try:
    from src.robot_agent.interfaces.provisioning.bluez import Characteristic, InvalidRequest
    from src.robot_agent.interfaces.provisioning.protocol import STATUS_UUID, REQUEST_UUID
except ImportError:
    Characteristic=None


@unittest.skipIf(Characteristic is None,'requires Ubuntu system python3-dbus')
class BluezAuthorizationTest(unittest.TestCase):
    def fake(self,uuid):
        value=types.SimpleNamespace(bluez_owner=':1.42',uuid=uuid)
        value._authorize=lambda sender: Characteristic._authorize(value,sender)
        return value

    def test_local_caller_cannot_write_with_spoofed_phone(self):
        with self.assertRaisesRegex(InvalidRequest,'NOT_ALLOWED'):
            Characteristic.WriteValue(self.fake(REQUEST_UUID),b'{}\n',{'device':'/org/bluez/hci0/dev_FAKE'},sender=':1.43')

    def test_private_status_is_not_readable_by_local_callers(self):
        with self.assertRaisesRegex(InvalidRequest,'NOT_ALLOWED'):
            Characteristic.ReadValue(self.fake(STATUS_UUID),{'device':'/org/bluez/hci0/dev_FAKE'},sender=':1.43')

    def test_missing_sender_is_rejected(self):
        with self.assertRaisesRegex(InvalidRequest,'NOT_ALLOWED'):
            Characteristic._authorize(self.fake(REQUEST_UUID),None)

    def test_bluez_owner_is_accepted(self):
        Characteristic._authorize(self.fake(REQUEST_UUID),':1.42')


if __name__=='__main__': unittest.main()
