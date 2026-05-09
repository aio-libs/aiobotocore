"""Add the plugin's evals module to sys.path for test imports.

The eval scripts live under plugins/aiobotocore-bot/evals/, which isn't a
package. Tests import from `_common` via this path hack so we don't have
to restructure the plugin into a proper Python package just to test it.
"""

import sys
from pathlib import Path

_EVALS_DIR = (
    Path(__file__).resolve().parents[2] / "plugins/aiobotocore-bot/evals"
)
if str(_EVALS_DIR) not in sys.path:
    sys.path.insert(0, str(_EVALS_DIR))
