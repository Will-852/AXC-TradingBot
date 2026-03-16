"""dashboard — AXC Dashboard package.

Re-exports only the symbols needed by tests. All other code should import
from the specific submodule (e.g., from scripts.dashboard.backtest import ...).
"""

from scripts.dashboard.backtest import (
    handle_bt_run, handle_bt_status, handle_bt_results,
    _bt_jobs, _bt_lock, _get_bt_pool,
)
