# Plan: Auto Walk-Forward Validation Pipeline

## Goal
grid_search 完成 → 自動跑 walk-forward + monte-carlo → 只輸出通過嘅 combos

## Implementation
Single function `auto_validate_top_combos()` added to `grid_search.py`
Called at end of `run_grid_search()` after save_json

## Design
- Take top N combos from ranked results
- For each combo: run walk_forward + monte_carlo (reuse validate.py logic)
- Data already fetched (reuse `data` dict, 唔重新 fetch)
- Output: filtered JSON with validation status per combo
