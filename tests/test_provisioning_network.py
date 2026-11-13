"""Wi-Fi activation transactions preserve the old network when provisioning fails."""

import unittest

from src.robot_agent.interfaces.provisioning.network import NetworkConnector, NetworkError
from src.robot_agent.interfaces.provisioning.protocol import Request


class Driver:
    """Stand-in for NetworkManager's external D-Bus boundary."""
    def __init__(self,fail=False): self.calls=[];self.fail=fail
    def current_profile(self): return 'old-wifi'
    def create_profile(self,request): self.calls.append('create');return 'new-wifi'
    def activate(self,profile): self.calls.append(('activate',profile))
    def wait_ipv4(self,profile,timeout):
        if self.fail: raise NetworkError('AUTH_FAILED')
        return '172.20.10.2'
    def remove_profile(self,profile): self.calls.append(('remove',profile))
    def prune_profiles(self,request,keep): self.calls.append(('prune',keep))
    def current_ipv4(self): return '172.20.10.2'


class NetworkTest(unittest.TestCase):
    def setUp(self): self.request=Request(id='one',action='wifi.connect',ssid='test-hotspot',password='example-password')

    def test_success_returns_wifi_address_and_agent_readiness(self):
        driver=Driver(); connector=NetworkConnector(driver,port=8765,probe=lambda port:True)
        result=connector.connect(self.request)
        self.assertEqual(result,dict(ip='172.20.10.2',ws_port=8765,agent_ready=True))
        self.assertNotIn(('remove','old-wifi'),driver.calls)
        self.assertIn(('prune','new-wifi'),driver.calls)

    def test_failed_activation_removes_only_new_profile_and_restores_previous(self):
        driver=Driver(fail=True)
        with self.assertRaisesRegex(NetworkError,'AUTH_FAILED'):
            NetworkConnector(driver).connect(self.request)
        self.assertEqual(driver.calls,['create',('activate','new-wifi'),('remove','new-wifi'),('activate','old-wifi')])

    def test_invalid_wifi_address_rolls_back(self):
        driver=Driver();driver.wait_ipv4=lambda profile,timeout:'127.0.0.1'
        with self.assertRaises(NetworkError): NetworkConnector(driver).connect(self.request)
        self.assertIn(('activate','old-wifi'),driver.calls)

    def test_profile_creation_failure_does_not_deactivate_existing_network(self):
        driver=Driver()
        def fail(request): raise NetworkError('NOT_ALLOWED')
        driver.create_profile=fail
        with self.assertRaises(NetworkError): NetworkConnector(driver).connect(self.request)
        self.assertEqual(driver.calls,[])


if __name__=='__main__': unittest.main()
