"""BLE framing and credential validation without Bluetooth or hardware dependencies."""

import json
import unittest

from src.robot_agent.interfaces.provisioning.protocol import FrameDecoder, ProtocolError, decode_request


def payload(**changes):
    """Build an example request containing no real network credentials."""
    value = dict(v=1, id='req-1', action='wifi.connect', ssid='手机热点', password='example-password', hidden=False)
    value.update(changes)
    return json.dumps(value, ensure_ascii=False).encode()


class ProtocolTest(unittest.TestCase):
    def test_split_utf8_and_multiple_clients(self):
        decoder = FrameDecoder()
        data = payload()+b'\n'
        received=[]
        for byte in data[:-1]:
            received += decoder.feed('/phone/a', bytes([byte]))
        self.assertEqual(received, [])
        self.assertEqual(decoder.feed('/phone/b', payload(id='req-2')+b'\n')[0].id, 'req-2')
        result=decoder.feed('/phone/a', data[-1:])[0]
        self.assertEqual(result.ssid, '手机热点')
        self.assertNotIn('example-password', repr(result))

    def test_rejects_invalid_credentials_and_versions(self):
        for changes in [dict(v=2),dict(ssid='汉'*11),dict(ssid=''),dict(password='short'),
                        dict(password='a'*65),dict(hidden='false'),dict(action='robot.move'),dict(id='')]:
            with self.subTest(changes=changes),self.assertRaises(ProtocolError):
                decode_request(payload(**changes))

    def test_oversized_or_invalid_frames_do_not_poison_next_attempt(self):
        decoder=FrameDecoder()
        with self.assertRaises(ProtocolError): decoder.feed('/phone/a',b'x'*4097)
        self.assertEqual(decoder.feed('/phone/a',payload()+b'\n')[0].id,'req-1')
        with self.assertRaises(ProtocolError): decoder.feed('/phone/a',b'\xff\n')
        self.assertEqual(decoder.feed('/phone/a',payload()+b'\n')[0].id,'req-1')

    def test_expired_partial_frame_is_discarded(self):
        now=[0.0]
        decoder=FrameDecoder(clock=lambda:now[0])
        decoder.feed('/phone/a',b'broken')
        now[0]=31.0
        self.assertEqual(decoder.feed('/phone/a',payload()+b'\n')[0].id,'req-1')

    def test_status_request_never_accepts_extra_credentials(self):
        request=decode_request(json.dumps(dict(v=1,id='status-1',action='wifi.status')).encode())
        self.assertEqual(request.action,'wifi.status')
        with self.assertRaises(ProtocolError):
            decode_request(payload(action='wifi.status'))


if __name__=='__main__': unittest.main()
