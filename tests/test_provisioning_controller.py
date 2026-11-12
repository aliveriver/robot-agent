"""Asynchronous provisioning is isolated per phone and idempotent per request."""

import threading
import time
import unittest

from src.robot_agent.interfaces.provisioning.controller import ProvisioningController
from src.robot_agent.interfaces.provisioning.protocol import ProtocolError, Request


class Backend:
    def __init__(self): self.calls=0;self.release=threading.Event();self.release.set();self.fail=False
    def connect(self,request):
        self.calls+=1;self.release.wait(2)
        if self.fail: raise RuntimeError('sensitive example-password must not leave backend')
        return dict(ip='172.20.10.2',ws_port=8765,agent_ready=False)
    def snapshot(self): return dict(ip='172.20.10.2',ws_port=8765,agent_ready=True)


class ControllerTest(unittest.TestCase):
    def setUp(self):
        self.backend=Backend()
        self.controller=ProvisioningController(self.backend,robot_id='robot-test',name='XL-TEST')
        self.request=Request(id='first',action='wifi.connect',ssid='hotspot',password='example-password')
    def tearDown(self): self.backend.release.set();self.controller.close()
    def wait_finished(self,peer='/phone/a'):
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            status=self.controller.read_status(peer)
            if status['state']!='connecting': return status
            time.sleep(.005)
        self.fail('provisioning worker did not finish')

    def test_duplicate_completed_request_does_not_reconfigure_network(self):
        self.controller.submit('/phone/a',self.request)
        status=self.wait_finished()
        self.assertEqual(status['state'],'connected')
        self.assertEqual(status['robot_id'],'robot-test')
        self.controller.submit('/phone/a',self.request)
        self.assertEqual(self.backend.calls,1)
        self.assertEqual(self.controller.read_status('/phone/b')['state'],'idle')

    def test_busy_rejects_second_request_without_overwriting_first(self):
        self.backend.release.clear();self.controller.submit('/phone/a',self.request)
        with self.assertRaisesRegex(ProtocolError,'BUSY'):
            self.controller.submit('/phone/b',Request(id='second',action='wifi.status'))
        self.assertEqual(self.controller.read_status('/phone/a')['id'],'first')

    def test_failure_does_not_expose_password_or_exception(self):
        self.backend.fail=True;self.controller.submit('/phone/a',self.request)
        status=self.wait_finished()
        self.assertEqual(status['state'],'failed')
        self.assertEqual(status['code'],'CONNECT_FAILED')
        self.assertNotIn('example-password',str(status))

    def test_status_can_return_current_wifi_without_credentials(self):
        self.controller.submit('/phone/a',Request(id='status-1',action='wifi.status'))
        self.assertTrue(self.wait_finished()['agent_ready'])


if __name__=='__main__': unittest.main()
