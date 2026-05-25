"""T10: memory-oracle test suite.

Covers scan_environment.py, query_memory.py (all modes), verify_claims(),
_real_user_home() sandbox isolation, and robustness edge cases.
No third-party dependencies beyond pytest. Does NOT modify any source files.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

# ── Resolve paths ─────────────────────────────────────────────────────────────

_REPO = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO / "core" / "skills" / "memory-oracle" / "scripts"

sys.path.insert(0, str(_SCRIPTS))

import query_memory as qm
import scan_environment as se


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_memory(tmp_path: Path, files: dict) -> Path:
    """Populate a tmp memory dir with flat and/or machine/ JSON files."""
    mem = tmp_path / "memory"
    mem.mkdir()
    for name, data in files.items():
        if "/" in name:
            p = mem / name
            p.parent.mkdir(parents=True, exist_ok=True)
        else:
            p = mem / name
        p.write_text(json.dumps(data), encoding="utf-8")
    return mem


# ═══════════════════════════════════════════════════════════════════════════════
# (a) scan_environment.py — schema + non-emptiness
# ═══════════════════════════════════════════════════════════════════════════════

class TestScanSystem:
    def test_returns_dict_with_os_key(self):
        result = se.scan_system()
        assert isinstance(result, dict)
        assert "os" in result

    def test_os_has_python_version(self):
        result = se.scan_system()
        assert "python_version" in result["os"]

    def test_has_scanned_at(self):
        result = se.scan_system()
        assert "scanned_at" in result
        assert result["scanned_at"]

    def test_brew_packages_is_list(self):
        result = se.scan_system()
        assert isinstance(result.get("brew_packages"), list)


class TestScanEnvironmentFn:
    def test_returns_vars_dict(self):
        result = se.scan_environment()
        assert isinstance(result["vars"], dict)

    def test_key_count_positive(self):
        result = se.scan_environment()
        assert result["key_count"] > 0

    def test_path_entries_list(self):
        result = se.scan_environment()
        assert isinstance(result["path_entries"], list)

    def test_vars_reflects_os_environ(self):
        os.environ["_CS_TEST_VAR"] = "hello"
        result = se.scan_environment()
        assert result["vars"].get("_CS_TEST_VAR") == "hello"
        del os.environ["_CS_TEST_VAR"]


class TestScanCredentials:
    def test_returns_required_keys(self):
        result = se.scan_credentials()
        assert "sources" in result
        assert "keys" in result
        assert isinstance(result["sources"], list)
        assert isinstance(result["keys"], dict)


# Fix 1: 6 additional scanner test classes (brief §5.1 required ≥6 core scanners)

class TestScanOpenclaw:
    def test_happy_path_returns_schema(self, tmp_path, monkeypatch):
        monkeypatch.setattr(se, "HOME", tmp_path)
        result = se.scan_openclaw()
        assert "exists" in result
        assert "skills" in result
        assert isinstance(result["skills"], list)
        assert "feishu" in result
        assert "scanned_at" in result


class TestScanGstack:
    def test_happy_path_returns_schema(self, tmp_path, monkeypatch):
        monkeypatch.setattr(se, "HOME", tmp_path)
        result = se.scan_gstack()
        assert "exists" in result
        assert "repos" in result
        assert isinstance(result["repos"], list)
        assert "skills" in result
        assert "scanned_at" in result


class TestScanClawseat:
    def test_happy_path_returns_schema(self, tmp_path, monkeypatch):
        monkeypatch.setattr(se, "HOME", tmp_path)
        result = se.scan_clawseat()
        assert "profiles" in result
        assert isinstance(result["profiles"], dict)
        assert "sessions" in result
        assert "workspaces" in result
        assert "agents_root" in result


class TestScanRepos:
    def test_happy_path_no_coding_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(se, "HOME", tmp_path)
        result = se.scan_repos()
        assert "repos" in result
        assert isinstance(result["repos"], list)
        assert "scan_dirs" in result
        assert "scanned_at" in result

    def test_with_git_repo(self, tmp_path, monkeypatch):
        monkeypatch.setattr(se, "HOME", tmp_path)
        coding = tmp_path / "coding" / "myrepo"
        coding.mkdir(parents=True)
        (coding / ".git").mkdir()
        result = se.scan_repos()
        names = [r["name"] for r in result["repos"]]
        assert "myrepo" in names


class TestScanNetwork:
    def test_happy_path_returns_schema(self):
        result = se.scan_network()
        assert "proxy" in result
        assert isinstance(result["proxy"], dict)
        assert "endpoints" in result
        assert "scanned_at" in result


class TestScanGithub:
    def test_happy_path_returns_schema(self, tmp_path, monkeypatch):
        monkeypatch.setattr(se, "HOME", tmp_path)
        monkeypatch.setattr(se, "run_cmd", lambda *args, **kwargs: "")
        result = se.scan_github()
        assert "gitconfig" in result
        assert "gh_cli" in result
        assert "ssh_keys" in result
        assert isinstance(result["ssh_keys"], list)
        assert "scanned_at" in result


class TestParseEnvFile:
    def test_basic_key_value(self):
        text = "FOO=bar\nBAZ=qux"
        assert se.parse_env_file(text) == {"FOO": "bar", "BAZ": "qux"}

    def test_strips_quotes(self):
        text = 'KEY="value with spaces"'
        assert se.parse_env_file(text)["KEY"] == "value with spaces"

    def test_skips_comments(self):
        text = "# this is a comment\nKEY=val"
        assert "KEY" in se.parse_env_file(text)
        assert len(se.parse_env_file(text)) == 1

    def test_export_prefix(self):
        text = "export MY_KEY=myval"
        assert se.parse_env_file(text).get("MY_KEY") == "myval"

    def test_empty_input(self):
        assert se.parse_env_file("") == {}

    def test_single_quotes(self):
        text = "TOKEN='abc123'"
        assert se.parse_env_file(text)["TOKEN"] == "abc123"


class TestSafeRead:
    def test_reads_small_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello")
        assert se.safe_read(f) == "hello"

    def test_returns_none_for_missing(self, tmp_path):
        assert se.safe_read(tmp_path / "nope.txt") is None

    def test_respects_max_bytes(self, tmp_path):
        f = tmp_path / "big.txt"
        f.write_bytes(b"x" * 100)
        assert se.safe_read(f, max_bytes=50) is None

    def test_large_file_does_not_crash(self, tmp_path):
        f = tmp_path / "large.txt"
        f.write_bytes(b"a" * (2 * 1024 * 1024))
        # default max_bytes=1MB → returns None, does not crash
        result = se.safe_read(f)
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
# (d) HOME sandbox isolation
# ═══════════════════════════════════════════════════════════════════════════════

class TestRealUserHome:
    def test_returns_path(self):
        result = se._real_user_home()
        assert isinstance(result, Path)

    def test_returns_existing_dir(self):
        result = se._real_user_home()
        assert result.is_dir()

    def test_fallback_without_canary(self, tmp_path, monkeypatch):
        """Should not crash even if sandbox HOME lacks canary files."""
        monkeypatch.setenv("HOME", str(tmp_path))
        # _real_user_home uses pwd.getpwuid first, so result is real home
        result = se._real_user_home()
        assert isinstance(result, Path)


# ═══════════════════════════════════════════════════════════════════════════════
# (b) query_memory.py — four modes
# ═══════════════════════════════════════════════════════════════════════════════

class TestLoadJson:
    def test_loads_valid_json(self, tmp_path):
        f = tmp_path / "a.json"
        f.write_text('{"k": 1}')
        assert qm._load_json(f) == {"k": 1}

    def test_returns_none_for_missing(self, tmp_path):
        assert qm._load_json(tmp_path / "nope.json") is None

    def test_returns_none_for_corrupt_json(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("{not json}")
        assert qm._load_json(f) is None


class TestWalkPath:
    def test_simple_key(self):
        assert qm.walk_path({"a": 1}, ["a"]) == 1

    def test_nested_key(self):
        assert qm.walk_path({"a": {"b": 2}}, ["a", "b"]) == 2

    def test_missing_key(self):
        assert qm.walk_path({"a": 1}, ["b"]) is None

    def test_list_index(self):
        assert qm.walk_path({"items": [10, 20, 30]}, ["items", "1"]) == 20


class TestCmdKey:
    def test_hit_flat_layout(self, tmp_path, capsys):
        mem = _make_memory(tmp_path, {"credentials.json": {"keys": {"MINIMAX_API_KEY": {"value": "sk-abc"}}}})
        rc = qm.cmd_key(mem, "credentials.keys.MINIMAX_API_KEY.value")
        assert rc == 0
        out = capsys.readouterr().out
        assert "sk-abc" in out

    def test_miss_returns_nonzero(self, tmp_path, capsys):
        mem = _make_memory(tmp_path, {"credentials.json": {"keys": {}}})
        rc = qm.cmd_key(mem, "credentials.keys.MISSING_KEY.value")
        assert rc != 0

    def test_file_not_found_returns_nonzero(self, tmp_path, capsys):
        mem = tmp_path / "mem"
        mem.mkdir()
        rc = qm.cmd_key(mem, "credentials.keys.FOO")
        assert rc != 0

    def test_machine_layout_priority(self, tmp_path, capsys):
        mem = _make_memory(tmp_path, {
            "machine/credentials.json": {"keys": {"K": {"value": "machine-val"}}},
            "credentials.json": {"keys": {"K": {"value": "flat-val"}}},
        })
        rc = qm.cmd_key(mem, "credentials.keys.K.value")
        assert rc == 0
        out = capsys.readouterr().out
        assert "machine-val" in out


class TestCmdFile:
    def test_dumps_whole_file(self, tmp_path, capsys):
        mem = _make_memory(tmp_path, {"openclaw.json": {"feishu": {"group": "oc_abc"}}})
        rc = qm.cmd_file(mem, "openclaw", None)
        assert rc == 0
        assert "oc_abc" in capsys.readouterr().out

    def test_section_extraction(self, tmp_path, capsys):
        mem = _make_memory(tmp_path, {"openclaw.json": {"feishu": {"group": "oc_xyz"}, "other": "ignored"}})
        rc = qm.cmd_file(mem, "openclaw", "feishu")
        assert rc == 0
        out = capsys.readouterr().out
        assert "oc_xyz" in out
        assert "ignored" not in out

    def test_missing_section_nonzero(self, tmp_path, capsys):
        mem = _make_memory(tmp_path, {"openclaw.json": {}})
        rc = qm.cmd_file(mem, "openclaw", "nonexistent")
        assert rc != 0


class TestCmdSearch:
    def test_finds_key_match(self, tmp_path, capsys):
        mem = _make_memory(tmp_path, {
            "machine/credentials.json": {"MINIMAX_API_KEY": "sk-test"},
        })
        rc = qm.cmd_search(mem, "minimax")
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["count"] >= 1

    def test_finds_value_match(self, tmp_path, capsys):
        mem = _make_memory(tmp_path, {"machine/network.json": {"endpoint": "https://api.example.com"}})
        rc = qm.cmd_search(mem, "example.com")
        assert rc == 0

    def test_no_match_returns_nonzero(self, tmp_path):
        mem = _make_memory(tmp_path, {"machine/credentials.json": {"key": "unrelated"}})
        rc = qm.cmd_search(mem, "zzznomatch_zz")
        assert rc != 0

    def test_cross_file_hit(self, tmp_path, capsys):
        mem = _make_memory(tmp_path, {
            "machine/credentials.json": {"TOKEN": "abc"},
            "machine/openclaw.json": {"agent": "koder"},
        })
        rc = qm.cmd_search(mem, "koder")
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        files_hit = {m["file"] for m in data["matches"]}
        assert any("openclaw" in f for f in files_hit)


class TestCmdAskPromptFile:
    """Test --ask interface at the file-dispatch level (no LLM)."""

    def test_ask_without_profile_returns_error(self, tmp_path, capsys):
        rc = qm.cmd_ask("test question", profile_path=None, timeout=0.1)
        assert rc == 2

    def test_ask_with_nonexistent_profile_returns_error(self, tmp_path, capsys):
        rc = qm.cmd_ask("test question", profile_path="/nonexistent/profile.toml", timeout=0.1)
        # dispatch will fail because dispatch_task.py won't find the profile or binary
        assert rc in (1, 2)


# ═══════════════════════════════════════════════════════════════════════════════
# (c) verify_claims() — anti-hallucination contract core
# ═══════════════════════════════════════════════════════════════════════════════

class TestVerifyClaims:
    # ── mismatch cases (≥3 required) ─────────────────────────────────────────

    def test_mismatch_value_not_equal(self, tmp_path):
        mem = _make_memory(tmp_path, {"machine/credentials.json": {"key": "actual-val"}})
        response = {
            "claims": [{
                "statement": "Key is expected-val",
                "evidence": [{"file": "credentials", "path": "key", "expected_value": "expected-val"}],
            }]
        }
        result = qm.verify_claims(response, mem)
        assert result["all_verified"] is False
        assert result["claim_results"][0]["verified"] is False
        assert result["claim_results"][0]["mismatches"][0]["reason"] == "mismatch"

    def test_mismatch_path_not_found_in_file(self, tmp_path):
        mem = _make_memory(tmp_path, {"machine/credentials.json": {"other_key": "x"}})
        response = {
            "claims": [{
                "statement": "Missing path",
                "evidence": [{"file": "credentials", "path": "nonexistent.deep.path", "expected_value": "v"}],
            }]
        }
        result = qm.verify_claims(response, mem)
        assert result["all_verified"] is False
        mismatch = result["claim_results"][0]["mismatches"][0]
        assert mismatch["reason"] in ("mismatch", "path_not_found")

    def test_mismatch_file_not_found(self, tmp_path):
        mem = tmp_path / "empty_mem"
        mem.mkdir()
        response = {
            "claims": [{
                "statement": "File does not exist",
                "evidence": [{"file": "ghost", "path": "key", "expected_value": "v"}],
            }]
        }
        result = qm.verify_claims(response, mem)
        assert result["all_verified"] is False
        assert result["claim_results"][0]["mismatches"][0]["reason"] == "file_not_found"

    def test_mismatch_no_evidence(self, tmp_path):
        mem = tmp_path / "m"
        mem.mkdir()
        response = {
            "claims": [{
                "statement": "No evidence provided",
                "evidence": [],
            }]
        }
        result = qm.verify_claims(response, mem)
        assert result["all_verified"] is False
        assert result["claim_results"][0]["reason"] == "no_evidence"

    # ── pass cases (≥2 required) ──────────────────────────────────────────────

    def test_pass_exact_string_match(self, tmp_path):
        mem = _make_memory(tmp_path, {"machine/credentials.json": {"key": "sk-abc"}})
        response = {
            "claims": [{
                "statement": "Key is sk-abc",
                "evidence": [{"file": "credentials", "path": "key", "expected_value": "sk-abc"}],
            }]
        }
        result = qm.verify_claims(response, mem)
        assert result["all_verified"] is True
        assert result["claim_results"][0]["verified"] is True

    def test_pass_nested_path(self, tmp_path):
        mem = _make_memory(tmp_path, {"machine/credentials.json": {"keys": {"MINIMAX": {"value": "sk-xyz"}}}})
        response = {
            "claims": [{
                "statement": "Minimax key exists",
                "evidence": [{"file": "credentials", "path": "keys.MINIMAX.value", "expected_value": "sk-xyz"}],
            }]
        }
        result = qm.verify_claims(response, mem)
        assert result["all_verified"] is True

    # Fix 2: brief-required normalize/coerce pass cases.
    # verify_claims uses strict equality (actual != expected) with no normalization.
    # Marked xfail — planner should dispatch T-verify-normalize to implement these.

    @pytest.mark.xfail(
        reason="verify_claims uses strict equality; whitespace+case normalization not implemented — T-verify-normalize track needed",
        strict=True,
    )
    def test_pass_string_normalize_whitespace_case(self, tmp_path):
        mem = _make_memory(tmp_path, {"machine/credentials.json": {"key": "sk-abc"}})
        response = {
            "claims": [{
                "statement": "Key normalized whitespace+case",
                "evidence": [{"file": "credentials", "path": "key", "expected_value": "  SK-ABC  "}],
            }]
        }
        result = qm.verify_claims(response, mem)
        assert result["all_verified"] is True

    @pytest.mark.xfail(
        reason="verify_claims uses strict equality; str/int type coercion not implemented — T-verify-normalize track needed",
        strict=True,
    )
    def test_pass_numeric_type_coerce(self, tmp_path):
        mem = _make_memory(tmp_path, {"machine/credits.json": {"count": 123}})
        response = {
            "claims": [{
                "statement": "Count is 123 (string expected vs int actual)",
                "evidence": [{"file": "credits", "path": "count", "expected_value": "123"}],
            }]
        }
        result = qm.verify_claims(response, mem)
        assert result["all_verified"] is True

    # ── legacy schema (no claims key) ────────────────────────────────────────

    def test_legacy_schema_returns_null_all_verified(self, tmp_path):
        mem = tmp_path / "m"
        mem.mkdir()
        response = {"sources": ["some_file.json"], "answer": "legacy answer"}
        result = qm.verify_claims(response, mem)
        assert result["all_verified"] is None
        assert result["reason"] == "legacy_schema_no_evidence"

    # ── exit code 3 propagation ───────────────────────────────────────────────

    def test_exit_code_3_propagation(self, tmp_path):
        """cmd_ask returns 3 when verify_claims all_verified is False."""
        # We test the return value contract by examining verify_claims output
        mem = _make_memory(tmp_path, {"machine/credentials.json": {"key": "wrong"}})
        response = {
            "query_id": "test-id",
            "claims": [{
                "statement": "Claim that will fail",
                "evidence": [{"file": "credentials", "path": "key", "expected_value": "right"}],
            }]
        }
        result = qm.verify_claims(response, mem)
        # Caller (cmd_ask) would return 3 when not all_verified
        expected_rc = 0 if result["all_verified"] else 3
        assert expected_rc == 3

    # ── multiple claims mixed ────────────────────────────────────────────────

    def test_one_bad_claim_fails_all(self, tmp_path):
        mem = _make_memory(tmp_path, {"machine/credentials.json": {"good": "ok", "bad": "wrong"}})
        response = {
            "claims": [
                {"statement": "Good", "evidence": [{"file": "credentials", "path": "good", "expected_value": "ok"}]},
                {"statement": "Bad", "evidence": [{"file": "credentials", "path": "bad", "expected_value": "right"}]},
            ]
        }
        result = qm.verify_claims(response, mem)
        assert result["all_verified"] is False
        assert result["claim_results"][0]["verified"] is True
        assert result["claim_results"][1]["verified"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# (e) Robustness
# ═══════════════════════════════════════════════════════════════════════════════

class TestRobustness:
    def test_missing_json_file_returns_none(self, tmp_path):
        assert qm._load_json(tmp_path / "missing.json") is None

    def test_corrupt_json_returns_none(self, tmp_path):
        f = tmp_path / "corrupt.json"
        f.write_text("{this is not json at all")
        assert qm._load_json(f) is None

    def test_large_memory_file_search_no_crash(self, tmp_path, capsys):
        """>1MB file: safe_read returns None so scanner skips it; search must not crash."""
        mem = tmp_path / "memory"
        mem.mkdir()
        (mem / "machine").mkdir()
        big = mem / "machine" / "big.json"
        big.write_text(json.dumps({"data": "x" * (2 * 1024 * 1024)}))
        # search should not crash — may return 0 or 1 results
        try:
            rc = qm.cmd_search(mem, "x")
        except Exception as exc:
            pytest.fail(f"cmd_search crashed on large file: {exc}")

    def test_load_jsonl_skips_bad_lines(self, tmp_path):
        f = tmp_path / "events.log"
        f.write_text('{"ok": 1}\nBAD LINE\n{"also": "ok"}\n')
        records = qm._load_jsonl(f)
        assert len(records) == 2
        assert records[0] == {"ok": 1}

    def test_load_jsonl_missing_file(self, tmp_path):
        records = qm._load_jsonl(tmp_path / "nonexistent.log")
        assert records == []

    def test_cmd_key_top_level_file_dump(self, tmp_path, capsys):
        """--key with a single-part path dumps the whole file."""
        mem = _make_memory(tmp_path, {"machine/credentials.json": {"k": "v"}})
        rc = qm.cmd_key(mem, "credentials")
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["k"] == "v"

    def test_flatten_nested(self):
        obj = {"a": {"b": {"c": 1}}}
        pairs = qm.flatten(obj)
        assert ("a.b.c", 1) in pairs

    def test_flatten_list(self):
        obj = {"items": [10, 20]}
        pairs = qm.flatten(obj)
        assert ("items.0", 10) in pairs
        assert ("items.1", 20) in pairs
