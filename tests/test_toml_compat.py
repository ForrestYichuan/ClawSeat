from __future__ import annotations

import io
import textwrap
from pathlib import Path

import _toml_compat


def test_fallback_parses_project_toml_shapes(monkeypatch) -> None:
    monkeypatch.setattr(_toml_compat, "_toml_module", lambda: None)

    data = _toml_compat.loads_safe(
        textwrap.dedent(
            """\
            version = 1
            name = "cartooner-front"
            repo_root = "/Users/ywf/coding/cartooner"
            monitor_session = "project-cartooner-front-monitor"
            open_detail_windows = false
            engineers = [
              "memory",
              "cartooner-product-solo-planner",
              "cartooner-product2-planner",
            ]
            monitor_engineers = ["memory", "cartooner-product2-planner"]

            [seat_overrides.cartooner-product2-planner]
            tool = "codex"
            auth_mode = "api"
            provider = "xcode-best"
            capabilities = ["root-cause research", "tests"]
            purpose = "负责 Cartooner 产品任务调研、实现、测试和自审。"
            planner_self_contained = true
            """
        )
    )

    assert data["name"] == "cartooner-front"
    assert data["open_detail_windows"] is False
    assert data["engineers"] == [
        "memory",
        "cartooner-product-solo-planner",
        "cartooner-product2-planner",
    ]
    assert data["monitor_engineers"] == ["memory", "cartooner-product2-planner"]
    seat = data["seat_overrides"]["cartooner-product2-planner"]
    assert seat["provider"] == "xcode-best"
    assert seat["capabilities"] == ["root-cause research", "tests"]
    assert seat["purpose"] == "负责 Cartooner 产品任务调研、实现、测试和自审。"
    assert seat["planner_self_contained"] is True


def test_fallback_parses_profile_inline_team_tables(monkeypatch) -> None:
    monkeypatch.setattr(_toml_compat, "_toml_module", lambda: None)

    data = _toml_compat.loads_safe(
        textwrap.dedent(
            """\
            seats = [
              "memory",
              "cartooner-product2-planner",
            ]

            [teams]
            cartooner-product2 = { seats = ["cartooner-product2-planner"], team_type = "subteam", subgroup_profile = "planner-only", planner_count = 1, builder_count = 0, dedicated_reviewer = false, scaling_policy = { max_builders = 0, reviewer_fallback = "planner" } }
            """
        )
    )

    assert data["seats"] == ["memory", "cartooner-product2-planner"]
    team = data["teams"]["cartooner-product2"]
    assert team["seats"] == ["cartooner-product2-planner"]
    assert team["subgroup_profile"] == "planner-only"
    assert team["builder_count"] == 0
    assert team["dedicated_reviewer"] is False
    assert team["scaling_policy"]["reviewer_fallback"] == "planner"


def test_fallback_parses_quoted_dotted_provider_table(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(_toml_compat, "_toml_module", lambda: None)
    home = tmp_path / "home"
    secret_file = home / ".agents" / "secrets" / "claude" / "anthropic.env"

    data = _toml_compat.loads_safe(
        textwrap.dedent(
            f"""\
            version = 1

            [providers."anthropic"]
            tool = "claude"
            kind = "oauth_token"
            family = "anthropic"
            secret_file = "{secret_file}"
            created_at = "2026-05-14T00:00:00Z"
            updated_at = "2026-05-14T00:00:00Z"
            """
        )
    )

    assert sorted(data["providers"]) == ["anthropic"]
    assert data["providers"]["anthropic"]["tool"] == "claude"


def test_fallback_parses_project_seat_overrides_and_multiline_arrays(monkeypatch) -> None:
    monkeypatch.setattr(_toml_compat, "_toml_module", lambda: None)
    seat = "cartooner-product-solo-planner"

    data = _toml_compat.loads_safe(
        textwrap.dedent(
            f"""\
            name = "cartooner-front"
            repo_root = "/repo"
            monitor_session = "project-cartooner-front-monitor"
            engineers = [
              "memory",
              "{seat}",
            ]
            monitor_engineers = ["memory", "{seat}"]

            [seat_overrides.{seat}]
            tool = "claude"
            auth_mode = "oauth_token"
            provider = "anthropic"
            capabilities = ["root-cause research", "tests"]
            active_loop_owner = true
            """
        )
    )

    assert data["engineers"] == ["memory", seat]
    assert data["seat_overrides"][seat]["provider"] == "anthropic"
    assert data["seat_overrides"][seat]["capabilities"] == ["root-cause research", "tests"]
    assert data["seat_overrides"][seat]["active_loop_owner"] is True


def test_fallback_parses_array_of_tables(monkeypatch) -> None:
    monkeypatch.setattr(_toml_compat, "_toml_module", lambda: None)

    data = _toml_compat.load_safe(
        io.BytesIO(
            textwrap.dedent(
                """\
                [[engineers]]
                id = "planner"
                skills = ["a", "b"]

                [[engineers]]
                id = "builder"
                """
            ).encode("utf-8")
        )
    )

    assert data["engineers"] == [
        {"id": "planner", "skills": ["a", "b"]},
        {"id": "builder"},
    ]
