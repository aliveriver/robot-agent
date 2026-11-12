"""Run an async Agent process while preserving exit status and flushing logs.

Use ``run_application(main)`` at the executable module boundary. A direct
process exit avoids ROS2 destructor crashes after asyncio has cleaned up.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
import os
import sys
import traceback
from typing import Any, NoReturn


def run_application(main: Callable[[], Coroutine[Any, Any, None]]) -> NoReturn:
    """Exit cleanly on interruption and report application failures to systemd."""
    exit_code = 0
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except SystemExit as exc:
        exit_code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        if exc.code is not None and not isinstance(exc.code, int):
            print(exc.code, file=sys.stderr)
    except BaseException:
        traceback.print_exc()
        exit_code = 1
    finally:
        # os._exit skips normal stream flushing along with unsafe ROS2 destructors.
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except Exception:
                pass
        os._exit(exit_code)
