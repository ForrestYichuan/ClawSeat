# Test Suite Audit - 2026-05-14

## Context

The suite grew through several ClawSeat architecture versions, so the first
question was whether long runtime comes from obsolete functionality and stale
tests. The answer is mixed:

- Runtime cost is mostly from host/process/install smoke coverage, not from
  obviously dead assertions.
- Legacy-looking tests are mostly compatibility, migration, or removal guards.
- The previous `legacy` marker was too narrow because it only matched file
  names. Function names such as `test_bind_backward_compat_missing_new_fields`
  were invisible to `pytest -m legacy`.

## Baseline

After Phase 1 suite layering and the Phase 2 audit helper:

- Fast local suite: `2682 passed, 437 deselected, 2 xfailed in 2:16`
  via `bash scripts/test-fast.sh --tb=short`.
- Full local suite after Phase 3 scan-test dedupe:
  `3116 passed, 3 skipped, 2 xfailed in 8:43`
  via `python3 -m pytest tests/ -q --tb=short --durations=50`.
- CI full suite now emits `--durations=50`; Python 3.11/3.12/3.13 passed on
  run `25831646236`.
- Before Phase 3, the slowest local tests were clustered in repeated
  `tests/test_scan_machine_subset.py` default scans plus
  `tests/test_memory_oracle.py::TestScanGithub`. Phase 3 reduced repeated
  default scans to one module fixture and mocked the schema-only GitHub scanner
  test, cutting full local runtime from `11:19` to `8:43`.
- The remaining slowest local tests are one real default scan smoke plus install
  and process-heavy coverage, not legacy/removal guard tests.
- Phase 4 reused read-only fake-install results in privacy/skill symlink tests.
  The targeted privacy + skill + pre-commit group dropped from about `54s` to
  `36s`; full-suite runtime remains about `8:44` because the remaining install
  smoke tests intentionally exercise separate setup paths.

## Legacy Inventory

Static scan:

```bash
python3 scripts/test-suite-audit.py
```

The scanner looks for legacy/deprecated/retired/dead-code/compat/superseded
language in `tests/test_*.py` and groups files into:

- `migration_or_compat`: current behavior intentionally supports old inputs,
  aliases, schemas, or fallback paths.
- `removal_guard`: asserts retired files, commands, fields, or APIs do not
  return.
- `stale_comment`: text-only historical notes that may be cleaned up without
  changing behavior.
- `review`: needs human classification.

Initial manual review found 89 files with legacy/compat text. The audit script
currently reports 92 files because it also counts path-only hits such as
`tests/test_no_legacy_v07_migration.py`. Only 20 tests were collected by the
old `pytest -m legacy` marker; after nodeid matching, 68 tests are collected.
That mismatch was marker blindness, not proof that unmarked files were garbage.

## Representative Findings

Keep as active compatibility/migration coverage:

- `tests/test_project_binding_schema_v3.py`
- `tests/test_provider_ssot.py`
- `tests/test_memory_query_v2.py`
- `tests/test_dispatch_notify_default.py`
- `tests/test_session_stop_closes_iterm.py`
- `tests/test_project_binding.py`
- `tests/test_ref_docs_in_skill_folders.py`

Keep as removal guards, but consider consolidating later:

- `tests/test_no_legacy_v07_migration.py`
- `tests/test_launcher_no_v05_preflight.py`
- parts of `tests/test_ancestor_final_cleanup.py`
- `tests/test_provider_validation.py::test_legacy_launch_ancestor_entrypoint_removed`
- `tests/test_wait_for_seat_persistent_reattach.py::test_wait_for_seat_rejects_retired_single_arg_interface`

Potential comment cleanup only:

- `tests/test_harness_templates_load.py`
- `tests/test_install_pending_seats_dynamic.py`

## Policy

- Treat `legacy` as "compat/removal surface", not "safe to delete".
- Delete a legacy test only after product support is removed or a sunset is
  documented.
- Prefer consolidating scattered negative removal guards over dropping them.
- For day-to-day speed, use `scripts/test-fast.sh`; for release confidence,
  keep CI on the full suite.

## Next Work

1. Use `scripts/test-suite-audit.py --json` to produce a reviewed allowlist of
   retained compatibility/removal guards.
2. Consolidate small one-assertion removal guards into a single retired-artifact
   test module where it reduces collection overhead and maintenance cost.
3. Continue performance work on install smoke tests by sharing expensive setup
   only when assertions are read-only and behaviorally identical; keep tests
   with distinct preconditions isolated.
4. Decide explicit sunset dates for deprecated CLI aliases such as
   `--skip-notify` and `--feishu-bot-account`.
5. Only then remove obsolete tests together with the product code they protect.
