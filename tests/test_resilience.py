"""Exercise missing peripherals and network recovery without robot hardware."""
import asyncio
import unittest
from unittest.mock import patch

from src.robot_agent.bootstrap.health import RuntimeHealth
from src.robot_agent.bootstrap.resilience import supervise, monitor_cloud, tcp_reachable
from src.robot_agent.bootstrap.audio_readiness import require_pulse_devices, ComponentUnavailable
from src.robot_agent.bootstrap.agent_runtime import run_agent
from src.robot_agent.bootstrap.voice_tasks import VoiceTasks, WelcomeOnce
from src.robot_agent.bootstrap.audio_readiness import require_configured_audio


class HealthTest(unittest.TestCase):
    """Validate snapshots and explicit peripheral selection."""

    def test_snapshots_do_not_mutate_live_state(self):
        health = RuntimeHealth()
        health.set('control', 'ready')
        snapshot = health.snapshot()
        snapshot['components']['control']['state'] = 'failed'
        self.assertEqual(health.snapshot()['components']['control']['state'], 'ready')

    def test_expected_microphone_missing_does_not_select_usb_fallback(self):
        def query(kind):
            return ['usb_input'] if kind == 'sources' else ['usb_output']
        with self.assertRaisesRegex(ComponentUnavailable, 'microphone_missing'):
            require_pulse_devices('dji_input', 'usb_output', query)

    def test_explicit_source_and_sink_are_required(self):
        query = lambda kind: ['dji_input'] if kind == 'sources' else ['usb_output']
        require_pulse_devices('dji_input', 'usb_output', query)
        with self.assertRaisesRegex(ComponentUnavailable, 'speaker_missing'):
            require_pulse_devices('dji_input', 'absent', query)

    def test_missing_audio_configuration_cannot_fall_back_to_an_unrelated_mic(self):
        with patch.dict('os.environ', {}, clear=True), patch('os.name', 'posix'):
            with self.assertRaisesRegex(ComponentUnavailable, 'audio_not_configured'):
                require_configured_audio()


class ResilienceTest(unittest.IsolatedAsyncioTestCase):
    """Use real async tasks to verify retry, cancellation and socket failures."""

    async def wait_for_state(self, health, component, state):
        async def wait():
            while health.snapshot()['components'].get(component, {}).get('state') != state:
                await asyncio.sleep(0.001)
        await asyncio.wait_for(wait(), 1)

    async def test_missing_microphone_recovers_without_stopping_control(self):
        health = RuntimeHealth()
        health.set('control', 'ready')
        available = asyncio.Event()
        async def run(ready):
            if not available.is_set():
                raise ComponentUnavailable('microphone_missing')
            ready()
            await asyncio.Future()
        task = asyncio.create_task(supervise('voice', run, health, retry_delay=0.01))
        try:
            await self.wait_for_state(health, 'voice', 'waiting')
            self.assertEqual(health.snapshot()['components']['control']['state'], 'ready')
            available.set()
            await self.wait_for_state(health, 'voice', 'ready')
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(health.snapshot()['components']['voice']['state'], 'stopped')

    async def test_running_voice_failure_retries_and_hides_exception_details(self):
        health = RuntimeHealth()
        calls = 0
        async def run(ready):
            nonlocal calls
            calls += 1
            ready()
            if calls == 1:
                raise RuntimeError('password=do-not-expose')
            await asyncio.Future()
        task = asyncio.create_task(supervise('voice', run, health, retry_delay=0.01))
        try:
            await self.wait_for_state(health, 'voice', 'waiting')
            self.assertNotIn('do-not-expose', str(health.snapshot()))
            await asyncio.sleep(0.025)
            self.assertEqual(calls, 2)
            self.assertEqual(health.snapshot()['components']['voice']['state'], 'ready')
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_cloud_failure_and_recovery_does_not_change_control(self):
        health = RuntimeHealth()
        health.set('control', 'ready')
        available = False
        async def probe(url):
            return available
        task = asyncio.create_task(monitor_cloud({'llm': 'https://cloud.invalid'}, health,
                                                probe=probe, interval=0.01))
        try:
            await self.wait_for_state(health, 'cloud_llm', 'waiting')
            available = True
            await self.wait_for_state(health, 'cloud_llm', 'ready')
            self.assertEqual(health.snapshot()['components']['control']['state'], 'ready')
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_tcp_probe_observes_real_server_and_closed_port(self):
        async def accept(reader, writer):
            writer.close()
            await writer.wait_closed()
        server = await asyncio.start_server(accept, '127.0.0.1', 0)
        port = server.sockets[0].getsockname()[1]
        self.assertTrue(await tcp_reachable(f'http://127.0.0.1:{port}'))
        server.close()
        await server.wait_closed()
        self.assertFalse(await tcp_reachable(f'http://127.0.0.1:{port}'))
        self.assertFalse(await tcp_reachable('not-a-url'))

    async def test_core_starts_before_voice_and_survives_missing_voice(self):
        health = RuntimeHealth()
        events = []
        async def start():
            events.append('control-start')
        async def stop():
            events.append('control-stop')
        async def voice(ready):
            events.append('voice-attempt')
            raise ComponentUnavailable('microphone_missing')
        task = asyncio.create_task(run_agent(start, stop, voice, health, retry_delay=0.01))
        await self.wait_for_state(health, 'voice', 'waiting')
        self.assertEqual(events[0], 'control-start')
        self.assertNotIn('control-stop', events)
        self.assertEqual(health.snapshot()['mode'], 'full-agent')
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(events[-1], 'control-stop')
        self.assertEqual(health.snapshot()['components']['control']['state'], 'stopped')

    async def test_failed_core_start_does_not_launch_voice_and_is_cleaned_up(self):
        health = RuntimeHealth()
        events = []
        async def start():
            raise OSError('port busy')
        async def stop():
            events.append('cleaned')
        async def voice(ready):
            events.append('voice')
        with self.assertRaises(OSError):
            await run_agent(start, stop, voice, health)
        self.assertEqual(events, ['cleaned'])

    async def test_speech_finishing_after_device_cleanup_cannot_submit_a_command(self):
        tasks = VoiceTasks(asyncio.get_running_loop())
        executed = []
        async def command():
            executed.append('motion')
        tasks.close()
        result = await asyncio.to_thread(tasks.submit, command())
        await asyncio.sleep(0)
        self.assertIsNone(result)
        self.assertEqual(executed, [])

    async def test_device_cleanup_cancels_already_submitted_speech(self):
        tasks = VoiceTasks(asyncio.get_running_loop())
        entered = asyncio.Event()
        cancelled = asyncio.Event()
        async def command():
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()
        result = await asyncio.to_thread(tasks.submit, command())
        await asyncio.wait_for(entered.wait(), 1)
        tasks.close()
        await asyncio.wait_for(cancelled.wait(), 1)
        self.assertTrue(result.cancelled())

    async def test_busy_or_offline_speech_is_discarded_instead_of_queued(self):
        online = True
        tasks = VoiceTasks(asyncio.get_running_loop(), can_submit=lambda: online)
        entered = asyncio.Event()
        async def work():
            entered.set()
            await asyncio.Future()
        first = await asyncio.to_thread(tasks.submit, work())
        await entered.wait()
        self.assertIsNone(await asyncio.to_thread(tasks.submit, work()))
        tasks.cancel_current()
        online = False
        self.assertIsNone(await asyncio.to_thread(tasks.submit, work()))
        tasks.close()
        self.assertTrue(first.cancelled())

    async def test_welcome_tracks_echo_and_only_plays_once_across_recovery(self):
        welcome = WelcomeOnce()
        events = []
        class Session:
            graph_lock = asyncio.Lock()
            tts_interrupt_event = object()
            def note_spoken_start(self, text): events.append(('start', text))
            def note_spoken_finish(self): events.append(('finish',))
        class TTS:
            async def speak(self, text, **kwargs): events.append(('speak', text))
        await welcome.play(TTS(), 'hello', 'en', Session())
        await welcome.play(TTS(), 'hello', 'en', Session())
        self.assertEqual(events, [('start', 'hello'), ('speak', 'hello'), ('finish',)])

    async def test_cancelled_speech_keeps_admission_busy_until_async_cleanup_finishes(self):
        tasks = VoiceTasks(asyncio.get_running_loop())
        entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def work():
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cleaning.set()
                await release.wait()
        first = await asyncio.to_thread(tasks.submit, work())
        await asyncio.wait_for(entered.wait(), 1)
        tasks.cancel_current()
        await asyncio.wait_for(cleaning.wait(), 1)
        try:
            self.assertTrue(first.cancelled())
            self.assertIsNone(await asyncio.to_thread(tasks.submit, lambda: asyncio.sleep(0)))
        finally:
            release.set()
            await asyncio.sleep(0.01)
            tasks.close()


if __name__ == '__main__':
    unittest.main()
