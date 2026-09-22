"""Test harness: factory dispatch imports cursor_sdk, which is not installed in CI."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

FACTORY_ROOT = Path(__file__).resolve().parent.parent
if str(FACTORY_ROOT) not in sys.path:
    sys.path.insert(0, str(FACTORY_ROOT))

if "cursor_sdk" not in sys.modules:
    cursor_sdk = MagicMock()

    class CursorAgentError(Exception):
        def __init__(self, message: str = "", *, is_retryable: bool = False) -> None:
            super().__init__(message)
            self.message = message
            self.is_retryable = is_retryable

    cursor_sdk.CursorAgentError = CursorAgentError
    cursor_sdk.Agent = MagicMock()
    cursor_sdk.CloudAgentOptions = MagicMock()
    cursor_sdk.CloudRepository = MagicMock()
    sys.modules["cursor_sdk"] = cursor_sdk
