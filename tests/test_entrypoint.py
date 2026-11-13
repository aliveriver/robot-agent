"""Process-level checks for Agent exit status, without loading robot hardware."""

from pathlib import Path
import subprocess
import sys
import unittest


class EntrypointTest(unittest.TestCase):
    """Exercise real child processes so os._exit and buffered logs are covered."""

    def run_child(self, body):
        """Invoke the lightweight entry point with a supplied async application."""
        program = (
            'from src.robot_agent.bootstrap.entrypoint import run_application\n'
            'async def main():\n' + body + '\nrun_application(main)\n'
        )
        return subprocess.run(
            [sys.executable, '-B', '-c', program],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=10,
        )

    def test_normal_completion_preserves_buffered_output(self):
        result = self.run_child('    print("startup complete")')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('startup complete', result.stdout)

    def test_startup_failure_returns_nonzero_and_reports_cause(self):
        result = self.run_child('    raise RuntimeError("startup probe failed")')
        self.assertEqual(result.returncode, 1)
        self.assertIn('RuntimeError: startup probe failed', result.stderr)

    def test_interrupt_is_a_clean_shutdown(self):
        result = self.run_child('    raise KeyboardInterrupt()')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_explicit_exit_code_is_preserved(self):
        result = self.run_child('    raise SystemExit(7)')
        self.assertEqual(result.returncode, 7, result.stderr)


if __name__ == '__main__':
    unittest.main()
