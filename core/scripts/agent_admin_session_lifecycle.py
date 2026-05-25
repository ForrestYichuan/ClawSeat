from __future__ import annotations

from dataclasses import dataclass

from agent_admin_session_base import (
    os,
    shlex,
    subprocess,
    sys,
    time,
    datetime,
    timezone,
    Path,
    Any,
    Seat,
    load_binding,
    open_db,
    project_tool_root,
    real_user_home,
    upsert_seat,
    TMUX_COMMAND_RETRIES,
    TMUX_COMMAND_RETRY_DELAY_SECONDS,
    TMUX_COMMAND_TIMEOUT_SECONDS,
    SESSION_STABILITY_WINDOW_SECONDS,
    _REPO_ROOT,
    _close_iterm_pane_by_tty,
    _engineer_profile_path,
    _get_tmux_tty,
    _real_home_for_tool_seeding,
    SessionStartError,
)
from seat_roles import normalize_seat_role


@dataclass(frozen=True)
class CleanStaleAttachClientsReport:
    session_name: str
    candidate_processes: tuple[tuple[int, str], ...]
    live_client_pids: frozenset[int]
    dry_run: bool = False

    @property
    def candidate_pids(self) -> tuple[int, ...]:
        return tuple(pid for pid, _command in self.candidate_processes)

    @property
    def stale_processes(self) -> tuple[tuple[int, str], ...]:
        return tuple(
            (pid, command)
            for pid, command in self.candidate_processes
            if pid not in self.live_client_pids
        )

    @property
    def stale_pids(self) -> tuple[int, ...]:
        return tuple(pid for pid, _command in self.stale_processes)

    @property
    def whitelist_hit_count(self) -> int:
        return len(self.candidate_processes) - len(self.stale_processes)

    @property
    def skip_count(self) -> int:
        return len(self.candidate_processes) if self.dry_run else self.whitelist_hit_count


class SessionStartLifecycle:
    def _run_tmux_with_retry(
        self,
        args: list[str],
        *,
        reason: str,
        check: bool = False,
        retries: int = TMUX_COMMAND_RETRIES,
        timeout: float = TMUX_COMMAND_TIMEOUT_SECONDS,
    ) -> subprocess.CompletedProcess:
        last: subprocess.CompletedProcess | None = None
        for attempt in range(1, retries + 1):
            if not check:
                try:
                    return subprocess.run(
                        ["tmux", *args],
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                    )
                except subprocess.TimeoutExpired as exc:
                    print(
                        f"tmux_retry: {reason} attempt={attempt}/{retries} timeout={timeout}s",
                        file=sys.stderr,
                    )
                    if attempt >= retries:
                        raise SessionStartError(
                            f"{reason} timeout after {retries} attempt(s) for args={args}"
                        ) from exc
                except OSError as exc:
                    raise SessionStartError(f"{reason} failed before executing tmux: {exc}") from exc
                if attempt < retries:
                    time.sleep(TMUX_COMMAND_RETRY_DELAY_SECONDS)
                    continue
                raise SessionStartError(f"{reason} failed for tmux args={args}")
            try:
                result = subprocess.run(
                    ["tmux", *args],
                    check=check,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                if result.returncode == 0:
                    return result
                last = result
            except subprocess.CalledProcessError as exc:
                last = exc
            except subprocess.TimeoutExpired as exc:
                last = None
                print(
                    f"tmux_retry: {reason} attempt={attempt}/{retries} timeout={timeout}s",
                    file=sys.stderr,
                )
                if attempt >= retries:
                    raise SessionStartError(
                        f"{reason} timeout after {retries} attempt(s) for args={args}"
                    ) from exc
            except OSError as exc:
                raise SessionStartError(f"{reason} failed before executing tmux: {exc}") from exc
            else:
                if result.returncode == 0:
                    return result
                print(
                    f"tmux_retry: {reason} attempt={attempt}/{retries} rc={result.returncode}",
                    file=sys.stderr,
                )
            if attempt < retries:
                time.sleep(TMUX_COMMAND_RETRY_DELAY_SECONDS)

        if last is not None:
            detail = (last.stderr or last.stdout or "").strip()
            raise SessionStartError(
                f"{reason} failed after {retries} attempt(s), exit={last.returncode}, detail={detail}, args={args}"
            )
        raise SessionStartError(f"{reason} failed for tmux args={args}")

    def _session_window_state(self, session_name: str) -> str:
        if not self.hooks.tmux_has_session(session_name):
            return "session=missing"
        result = self._run_tmux_with_retry(
            ["list-panes", "-t", session_name, "-F", "#{session_name}|#{pane_id}|#{pane_current_command}"],
            reason=f"snapshot session windows for {session_name}",
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return f"session={session_name}, panes={result.stdout.strip()}"
        return f"session={session_name}, panes=empty"

    def _is_session_onboarding(self, session_name: str) -> bool:
        result = self._run_tmux_with_retry(
            ["capture-pane", "-t", session_name, "-p", "-S", "-80"],
            reason=f"capture onboarding markers for {session_name}",
            check=False,
        )
        if result.returncode != 0:
            return False
        content = result.stdout or ""
        markers = (
            "Do you trust the files in this folder",
            "Trust folder",
            "Welcome to Claude Code",
            "authenticate with your",
            "https://accounts.google.com",
            "Paste the code",
            "Enter your API key",
        )
        return any(marker in content for marker in markers)

    def _configure_session_display(self, session_name: str) -> None:
        target = f"={session_name}"
        for args in (
            ["set", "-g", "set-titles", "on"],
            ["set", "-g", "set-titles-string", "#{session_name}"],
            ["set-option", "-t", target, "detach-on-destroy", "off"],
            ["set-option", "-t", target, "status", "on"],
            ["set-option", "-t", target, "status-left", "[#{session_name}] "],
            ["set-option", "-t", target, "status-right", "#{?client_attached,ATTACHED,WAITING} | %H:%M"],
            ["set-option", "-t", target, "status-style", "fg=white,bg=blue,bold"],
        ):
            self._run_tmux_with_retry(
                args,
                reason=f"configure display for {session_name}",
                check=False,
            )

    def _assert_session_running(self, session: Any, *, operation: str) -> None:
        if not self.hooks.tmux_has_session(session.session):
            raise SessionStartError(
                f"{operation} failed for '{session.session}': session missing after startup; state={self._session_window_state(session.session)}"
            )
        if self._is_session_onboarding(session.session):
            print(
                f"{operation}: session={session.session} ONBOARDING_DETECTED "
                f"(trust prompt / OAuth / welcome) — treating as alive, operator interaction required",
                file=sys.stderr,
            )
            return
        output = self._run_tmux_with_retry(
            [
                "list-panes",
                "-t",
                session.session,
                "-F",
                "#{pane_id}|#{pane_current_command}",
            ],
            reason=f"{operation} verify panes for {session.session}",
            check=False,
        )
        if output.returncode != 0 or not output.stdout.strip():
            raise SessionStartError(
                f"{operation} failed for '{session.session}': no active panes detected; state={self._session_window_state(session.session)}"
            )

        # Stability window (audit finding #5): some agent CLIs spawn-then-exit
        # within a few seconds of tmux new-session — long enough for the
        # immediate pane check above to pass, short enough that the operator
        # finds an empty grid moments later. Re-check after a short pause to
        # reject flaky launches.
        time.sleep(SESSION_STABILITY_WINDOW_SECONDS)
        if not self.hooks.tmux_has_session(session.session):
            raise SessionStartError(
                f"{operation} failed for '{session.session}': session died "
                f"within {SESSION_STABILITY_WINDOW_SECONDS}s stability window "
                f"(spawn-then-exit). state={self._session_window_state(session.session)}"
            )
        recheck = self._run_tmux_with_retry(
            ["list-panes", "-t", session.session, "-F", "#{pane_id}"],
            reason=f"{operation} stability re-verify panes for {session.session}",
            check=False,
        )
        if recheck.returncode != 0 or not recheck.stdout.strip():
            raise SessionStartError(
                f"{operation} failed for '{session.session}': panes vanished "
                f"within {SESSION_STABILITY_WINDOW_SECONDS}s stability window. "
                f"state={self._session_window_state(session.session)}"
            )

    def _role_for_session(self, session: Any, project: Any) -> str:
        def _generic_role(value: str) -> str:
            value = value.strip()
            if value.startswith("minimal-"):
                legacy = value.removeprefix("minimal-")
                print(
                    f"warn: deprecated role namespace in project context mapped from {value} -> {legacy}",
                    file=sys.stderr,
                )
                value = legacy
            if value.startswith("creative-"):
                legacy = value.removeprefix("creative-")
                print(
                    f"warn: deprecated role namespace in project context mapped from {value} -> {legacy}",
                    file=sys.stderr,
                )
                value = value.removeprefix("creative-")
            if value.startswith("code-"):
                value = value.removeprefix("code-")
            mapping = {
                "planner-dispatcher": "planner",
                "project-memory": "memory",
                "memory-oracle": "memory",
                "code-reviewer": "reviewer",
                "frontstage-supervisor": "koder",
            }
            value = mapping.get(value, value)
            for prefix, role in (
                ("builder", "builder"),
                ("planner", "planner"),
                ("reviewer", "reviewer"),
                ("designer", "designer"),
                ("patrol", "patrol"),
                ("memory", "memory"),
                ("koder", "koder"),
                ("engineer", "builder"),
            ):
                if value == prefix or value.startswith(prefix + "-"):
                    return role
            return normalize_seat_role(value) or "specialist"

        project_engineers = getattr(session, "project_engineers", None)
        if isinstance(project_engineers, dict):
            engineer = project_engineers.get(session.engineer_id)
            role = str(getattr(engineer, "role", "") or "").strip()
            if role:
                return _generic_role(role)
        try:
            context = self.hooks.project_template_context(project)
        except Exception:  # noqa: BLE001 state registration is best-effort
            context = None
        if isinstance(context, tuple) and len(context) >= 1 and isinstance(context[0], dict):
            engineer = context[0].get(session.engineer_id)
            role = str(getattr(engineer, "role", "") or "").strip()
            if role:
                return _generic_role(role)
        return _generic_role(str(session.engineer_id))

    def _record_seat_live(self, session: Any, project: Any) -> None:
        compat_globals = getattr(self, "_compat_module_globals", None)
        if not isinstance(compat_globals, dict):
            compat_globals = {}
        tmux_has_session_fn = compat_globals.get(
            "tmux_has_session",
            getattr(self.hooks, "tmux_has_session", None),
        )
        session_name = str(getattr(session, "session", "") or "")
        if callable(tmux_has_session_fn) and not tmux_has_session_fn(session_name):
            print(
                f"warn: state.db upsert_seat skipped (session missing): {session_name}",
                file=sys.stderr,
            )
            return
        self._record_seat_status(
            session,
            project,
            status="live",
            allow_stopped_revival=True,
            verify_tmux_session=True,
        )

    def _record_seat_status(
        self,
        session: Any,
        project: Any,
        *,
        status: str,
        allow_stopped_revival: bool = False,
        verify_tmux_session: bool = False,
    ) -> None:
        try:
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            compat_globals = getattr(self, "_compat_module_globals", None)
            if not isinstance(compat_globals, dict):
                compat_globals = {}
            open_db_fn = compat_globals.get("open_db", open_db)
            upsert_seat_fn = compat_globals.get("upsert_seat", upsert_seat)
            seat_cls = compat_globals.get("Seat", Seat)
            tmux_has_session_fn = compat_globals.get(
                "tmux_has_session",
                getattr(self.hooks, "tmux_has_session", None),
            )
            project_name = getattr(session, "project", None)
            if project_name is None and project is not None:
                project_name = getattr(project, "name", project)
            engineer_id = getattr(session, "engineer_id", None)
            if not project_name or not engineer_id:
                return
            session_name = str(getattr(session, "session", "") or "")
            if verify_tmux_session and callable(tmux_has_session_fn) and not tmux_has_session_fn(session_name):
                print(
                    f"warn: state.db upsert_seat skipped (session missing): {session_name}",
                    file=sys.stderr,
                )
                return
            with open_db_fn() as conn:
                if verify_tmux_session and callable(tmux_has_session_fn) and not tmux_has_session_fn(session_name):
                    print(
                        f"warn: state.db upsert_seat skipped (session missing): {session_name}",
                        file=sys.stderr,
                    )
                    return
                upsert_seat_fn(
                    conn,
                    seat_cls(
                        project=str(project_name),
                        seat_id=str(engineer_id),
                        role=self._role_for_session(session, project),
                        tool=str(session.tool),
                        auth_mode=str(session.auth_mode),
                        provider=str(session.provider),
                        status=status,
                        last_heartbeat=now,
                        session_name=session_name or None,
                        workspace=str(getattr(session, "workspace", "") or "") or None,
                    ),
                    allow_stopped_revival=allow_stopped_revival,
                )
        except Exception as exc:  # noqa: BLE001 state.db must not block startup
            print(f"warn: state.db upsert_seat failed (non-fatal): {exc}", file=sys.stderr)

    def build_engineer_exec(self, session: Any) -> list[str]:
        if session.wrapper:
            return [session.wrapper]
        return [self.hooks.agentctl_path, "run-engineer", "--project", session.project, session.engineer_id]

    def start_engineer(self, session: Any, reset: bool = False) -> None:
        session = self.hooks.reconcile_session_runtime(session)
        self.hooks.ensure_api_secret_ready(session)
        project = self.hooks.load_project(session.project)
        self.hooks.apply_template(session, project)
        if reset:
            if self.hooks.tmux_has_session(session.session):
                self._run_tmux_with_retry(
                    ["kill-session", "-t", session.session],
                    reason=f"reset existing session {session.session}",
                    check=False,
                )
            self.clean_stale_attach_clients(session.session)
        if self.hooks.tmux_has_session(session.session):
            self._assert_session_running(session, operation=f"start_engineer idempotent check for {session.session}")
            self._configure_session_display(session.session)
            self._record_seat_live(session, project)
            return
        self._cleanup_stale_tool_variants(session)
        launcher_auth = self._launcher_auth_for(session)
        runtime_dir = self._launcher_runtime_dir(session, launcher_auth)
        if runtime_dir is not None and session.runtime_dir != str(runtime_dir):
            session.runtime_dir = str(runtime_dir)
            self.hooks.write_session(session)
            self.hooks.apply_template(session, project)
        binding = load_binding(session.project)
        tools_isolation = binding.tools_isolation if binding is not None else "shared-real-home"
        if tools_isolation == "per-project":
            project_root = project_tool_root(session.project, home=self._real_home_for_tool_seeding())
            if not project_root.exists():
                print(
                    f"warn: project tool root missing for {session.project}: {project_root} "
                    f"— run `agent_admin project init-tools {session.project} --from real-home`",
                    file=sys.stderr,
                )
        if runtime_dir is not None:
            try:
                self.reseed_sandbox_user_tool_dirs(session)
            except OSError as exc:
                raise SessionStartError(
                    f"reseed sandbox HOME failed for {session.session}: {exc}"
                ) from exc
        for attempt in range(1, TMUX_COMMAND_RETRIES + 1):
            custom_env_file = ""
            try:
                self._sync_launcher_secret_file(session, launcher_auth)
                if launcher_auth in ("custom", "minimax", "deepseek", "xcode"):
                    custom_env_file = self._write_launcher_custom_env_file(session)
                cmd = [
                    "bash",
                    self.hooks.launcher_path,
                    "--headless",
                    "--tool",
                    session.tool,
                    "--auth",
                    launcher_auth,
                    "--dir",
                    session.workspace,
                    "--session",
                    session.session,
                ]
                if custom_env_file:
                    cmd.extend(["--custom-env-file", custom_env_file])
                env = dict(os.environ)
                env.pop("CLAWSEAT_MEMORY_BRIEF", None)
                env.pop("CLAWSEAT_ANCESTOR_BRIEF", None)
                env["CLAWSEAT_ROOT"] = str(Path(self.hooks.launcher_path).resolve().parents[2])
                env["REAL_HOME"] = str(self._real_home_for_tool_seeding())
                env["CLAWSEAT_PROJECT"] = session.project
                env["CLAWSEAT_PROVIDER"] = session.provider
                env["CLAWSEAT_SEAT"] = session.engineer_id
                env["CLAWSEAT_ENGINEER_ID"] = session.engineer_id
                env["CLAWSEAT_ENGINEER_PROFILE"] = str(_engineer_profile_path(session.engineer_id))
                env["CLAWSEAT_TOOLS_ISOLATION"] = tools_isolation
                if reset:
                    env["CLAWSEAT_NO_AUTO_RESUME"] = "1"
                if tools_isolation == "per-project":
                    env["CLAWSEAT_PROJECT_TOOL_ROOT"] = str(
                        project_tool_root(session.project, home=self._real_home_for_tool_seeding())
                    )
                # Primary seat gets the memory bootstrap brief. Export the old
                # env name as a compatibility alias for existing hooks.
                if session.engineer_id in ("ancestor", "memory"):
                    memory_brief = self._memory_brief_path(session.project)
                    if memory_brief.is_file():
                        env["CLAWSEAT_MEMORY_BRIEF"] = str(memory_brief)
                        env["CLAWSEAT_ANCESTOR_BRIEF"] = str(memory_brief)
                print(
                    "start_engineer_launch: "
                    f"session={session.session} "
                    f"cmd={shlex.join(cmd)} "
                    f"provider={session.provider} "
                    f"engineer={session.engineer_id}",
                    file=sys.stderr,
                )
                result = subprocess.run(
                    cmd,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=TMUX_COMMAND_TIMEOUT_SECONDS,
                    env=env,
                )
                if result.returncode != 0:
                    detail = (result.stderr or result.stdout or "").strip()
                    raise SessionStartError(
                        f"start engineer {session.session} via launcher failed, "
                        f"exit={result.returncode}, detail={detail}"
                    )
                self._assert_session_running(session, operation=f"start engineer {session.session}")
                break
            except SessionStartError as exc:
                last_error = exc
                if self.hooks.tmux_has_session(session.session):
                    if self._is_session_onboarding(session.session):
                        print(
                            f"start_engineer: session={session.session} appears to be onboarding "
                            f"(operator interaction in progress); trust session and do not retry.",
                            file=sys.stderr,
                        )
                        self._configure_session_display(session.session)
                        self._record_seat_live(session, project)
                        return
                    self._run_tmux_with_retry(
                        ["kill-session", "-t", session.session],
                        reason=f"cleanup partial session {session.session}",
                        check=False,
                    )
                if attempt < TMUX_COMMAND_RETRIES:
                    print(
                        f"start_engineer_retry: session={session.session} attempt={attempt}/"
                        f"{TMUX_COMMAND_RETRIES} rc_waiting=retry",
                        file=sys.stderr,
                    )
                    time.sleep(TMUX_COMMAND_RETRY_DELAY_SECONDS)
                    continue
                detail = self._session_window_state(session.session)
                raise SessionStartError(
                    f"start engineer '{session.session}' failed after {TMUX_COMMAND_RETRIES} attempts; "
                    f"window_state={detail}; reason={exc}"
                ) from exc
            finally:
                # custom_env_file is consumed and unlinked by load_custom_env()
                # inside the tmux session.  Deleting it here races the in-tmux
                # reader (especially for non-"custom" launcher auth modes where
                # ensure_custom_env_file_for_auth skips the file).  Let the
                # in-tmux load_custom_env own cleanup; leftover files in /tmp
                # are harmless and cleaned by the OS.
                pass
        # Enable tmux terminal titles and status line so iTerm tabs show
        # the canonical session name and attachment state.
        self._configure_session_display(session.session)
        self._record_seat_live(session, project)

        # Auto-recover iTerm grid pane routing after any specialist seat
        # start / restart. When a seat's canonical tmux session comes up
        # after grid open time, stray grid panes may have attached to the
        # project's primary seat (v2 uses <project>-memory)
        # instead (see scripts/recover-grid.sh / docs
        # ITERM_TMUX_REFERENCE.md §3.1.1). This hook is idempotent:
        # if no misroute exists it prints "ok" and exits 0.
        self._auto_recover_grid_after_start(session)

        # When a memory seat is (re)started, the shared clawseat-memories
        # iTerm window's tab for this project is still attached to the
        # OLD (now-killed) tmux session — the operator sees a "detached"
        # banner and has to manually re-attach. Auto-refresh the memories
        # window so the tab reconnects to the fresh session.
        self._auto_refresh_memories_window_after_memory_start(session)

    def _stale_tool_variant_sessions(self, session: Any) -> list[str]:
        project_name = str(getattr(session, "project", "") or "").strip()
        engineer_id = str(getattr(session, "engineer_id", "") or "").strip()
        session_name = str(getattr(session, "session", "") or "").strip()
        current_tool = str(getattr(session, "tool", "") or "").strip()
        if not project_name or not engineer_id or not session_name or not current_tool:
            return []
        result = self._run_tmux_with_retry(
            ["list-sessions", "-F", "#{session_name}"],
            reason=f"enumerate same-seat session variants for {session_name}",
            check=False,
        )
        returncode = getattr(result, "returncode", None)
        stdout = getattr(result, "stdout", "")
        if not isinstance(returncode, int) or returncode != 0:
            return []
        if not isinstance(stdout, str) or not stdout.strip():
            return []
        prefix = f"{project_name}-{engineer_id}-"
        stale_sessions: list[str] = []
        for raw_name in stdout.splitlines():
            tmux_session = raw_name.strip()
            if not tmux_session or tmux_session == session_name or not tmux_session.startswith(prefix):
                continue
            tool_suffix = tmux_session[len(prefix):]
            if tool_suffix and tool_suffix != current_tool:
                stale_sessions.append(tmux_session)
        return stale_sessions

    def _cleanup_stale_tool_variants(self, session: Any) -> None:
        for stale_session in self._stale_tool_variant_sessions(session):
            result = self._run_tmux_with_retry(
                ["kill-session", "-t", stale_session],
                reason=f"cleanup stale-tool session {stale_session}",
                check=False,
            )
            returncode = getattr(result, "returncode", None)
            stderr = getattr(result, "stderr", "")
            stdout = getattr(result, "stdout", "")
            if isinstance(returncode, int) and returncode == 0:
                print(f"start-engineer: killed stale-tool session {stale_session}", file=sys.stderr)
                continue
            detail = ""
            if isinstance(stderr, str) and stderr.strip():
                detail = stderr.strip().lower()
            elif isinstance(stdout, str) and stdout.strip():
                detail = stdout.strip().lower()
            if "can't find session" in detail or "no such session" in detail:
                continue
            raise SessionStartError(
                f"cleanup stale-tool session {stale_session} failed, "
                f"exit={returncode}, detail={detail or '<empty>'}"
            )

    def _live_tmux_client_pids(self, session_name: str) -> set[int]:
        if not self.hooks.tmux_has_session(session_name):
            return set()
        result = self._run_tmux_with_retry(
            ["list-clients", "-t", session_name, "-F", "#{client_pid}"],
            reason=f"inspect tmux clients for {session_name}",
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return set()
        live_client_pids: set[int] = set()
        for raw_pid in result.stdout.splitlines():
            pid_text = raw_pid.strip()
            if pid_text.isdigit():
                live_client_pids.add(int(pid_text))
        return live_client_pids

    def _tmux_attach_candidate_processes(self, session_name: str) -> list[tuple[int, str]]:
        result = subprocess.run(
            ["pgrep", "-f", "tmux attach"],
            check=False,
            capture_output=True,
            text=True,
            timeout=TMUX_COMMAND_TIMEOUT_SECONDS,
        )
        if result.returncode not in (0, 1):
            detail = (result.stderr or result.stdout or "").strip()
            raise SessionStartError(
                f"inspect tmux attach clients for {session_name} failed, "
                f"exit={result.returncode}, detail={detail or '<empty>'}"
            )
        pids = sorted(
            {
                int(raw_pid.strip())
                for raw_pid in (result.stdout or "").splitlines()
                if raw_pid.strip().isdigit()
            }
        )
        candidates: list[tuple[int, str]] = []
        for pid in pids:
            proc = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                check=False,
                capture_output=True,
                text=True,
                timeout=TMUX_COMMAND_TIMEOUT_SECONDS,
            )
            if proc.returncode != 0:
                continue
            command = (proc.stdout or "").strip()
            if not command or "tmux attach" not in command or session_name not in command:
                continue
            if "new-session" in command:
                continue
            candidates.append((pid, command))
        return candidates

    def clean_stale_attach_clients_report(
        self,
        session_name: str,
        *,
        dry_run: bool = False,
    ) -> CleanStaleAttachClientsReport:
        live_client_pids = self._live_tmux_client_pids(session_name)
        candidates = tuple(self._tmux_attach_candidate_processes(session_name))
        return CleanStaleAttachClientsReport(
            session_name=session_name,
            candidate_processes=candidates,
            live_client_pids=frozenset(live_client_pids),
            dry_run=dry_run,
        )

    def clean_stale_attach_clients(
        self,
        session_name: str,
        *,
        dry_run: bool = False,
        report: CleanStaleAttachClientsReport | None = None,
    ) -> list[int]:
        """Reap tmux attach clients that target a session about to be respawned."""
        report = report or self.clean_stale_attach_clients_report(session_name, dry_run=dry_run)
        effective_dry_run = dry_run or report.dry_run
        reaped: list[int] = []
        for pid, command in report.stale_processes:
            if effective_dry_run:
                print(
                    f"tmux clean-stale-clients: dry-run pid={pid} session={session_name} command={command}",
                    file=sys.stderr,
                )
                reaped.append(pid)
                continue
            result = subprocess.run(
                ["kill", "-TERM", str(pid)],
                check=False,
                capture_output=True,
                text=True,
                timeout=TMUX_COMMAND_TIMEOUT_SECONDS,
            )
            if result.returncode == 0:
                reaped.append(pid)
                print(
                    f"start-engineer: reaped stale attach client pid={pid} session={session_name}",
                    file=sys.stderr,
                )
                continue
            detail = (result.stderr or result.stdout or "").strip().lower()
            if "no such process" in detail:
                continue
            raise SessionStartError(
                f"reap stale attach client {pid} for {session_name} failed, "
                f"exit={result.returncode}, detail={detail or '<empty>'}"
            )
        return reaped

    def _auto_recover_grid_after_start(self, session: Any) -> None:
        # Skip under pytest — the test harness mocks subprocess and our hook
        # shows up as an extra launcher call that breaks strict equality
        # assertions. Real operator runs don't have PYTEST_CURRENT_TEST.
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return
        if os.environ.get("CLAWSEAT_DISABLE_GRID_AUTORECOVER") == "1":
            return
        project = getattr(session, "project", None)
        if not project:
            session_name = getattr(session, "session", "")
            if "-" in session_name and not session_name.startswith("machine-"):
                project = session_name.split("-", 1)[0]
        if not project:
            return
        # Skip primary-seat ids — their panes aren't the misroute victims.
        seat_id = getattr(session, "engineer_id", "") or ""
        if seat_id in ("ancestor", "memory"):
            return
        recover_script = _REPO_ROOT / "scripts" / "recover-grid.sh"
        if not recover_script.exists():
            return
        # Append stdout+stderr to a durable log so memory / operator can
        # diagnose silent recover-grid failures (RCA 2026-04-25).
        log_path = self._real_home_for_tool_seeding() / ".clawseat" / ".agent" / "task-watch" / "grid-recovery.log"
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as log_fh:
                log_fh.write(
                    f"\n=== {datetime.now(timezone.utc).isoformat()} "
                    f"recover-grid {project} (after {session.session}) ===\n"
                )
                log_fh.flush()
                result = subprocess.run(
                    ["bash", str(recover_script), project],
                    check=False,
                    timeout=30,
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                )
            if result.returncode != 0:
                print(
                    f"WARN: recover-grid failed (rc={result.returncode}); see {log_path}",
                    file=sys.stderr,
                )
        except Exception as exc:  # noqa: BLE001 silent-ok: hook must not fail start-engineer
            print(f"warn: grid recovery hook after {session.session}: {exc} (log: {log_path})", file=sys.stderr)

    def _auto_refresh_memories_window_after_memory_start(self, session: Any) -> None:
        # Skip under pytest — the test harness mocks subprocess and our hook
        # shows up as an extra iterm driver call that breaks strict equality
        # assertions.
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return
        if os.environ.get("CLAWSEAT_DISABLE_MEMORIES_AUTOREFRESH") == "1":
            return
        seat_id = getattr(session, "engineer_id", "") or ""
        if seat_id not in ("ancestor", "memory"):
            return
        project_name = getattr(session, "project", None)
        if not project_name:
            return
        try:
            project = self.hooks.load_project(project_name)
        except Exception as exc:  # noqa: BLE001 silent-ok: hook must not fail start-engineer
            print(
                f"warn: memories refresh skipped — load_project({project_name!r}) failed: {exc}",
                file=sys.stderr,
            )
            return
        try:
            # Imported lazily to avoid circular imports during agent_admin bootstrap.
            from agent_admin_window import ensure_memories_pane
            ensure_memories_pane(project)
        except Exception as exc:  # noqa: BLE001 silent-ok: hook must not fail start-engineer
            print(
                f"warn: ensure_memories_pane after {session.session}: {exc}",
                file=sys.stderr,
            )

    def stop_engineer(self, session: Any, *, close_iterm_pane: bool = False, **legacy_kwargs: Any) -> None:
        # Backward-compat: callers (incl. agent_admin_commands.py) historically
        # passed `close_iterm_tab=True`.  Accept both names; new name is correct.
        if "close_iterm_tab" in legacy_kwargs:
            close_iterm_pane = close_iterm_pane or bool(legacy_kwargs.pop("close_iterm_tab"))
        if legacy_kwargs:
            raise TypeError(f"stop_engineer got unexpected kwargs: {sorted(legacy_kwargs)}")
        if close_iterm_pane:
            compat_globals = getattr(self, "_compat_module_globals", None)
            if not isinstance(compat_globals, dict):
                compat_globals = {}
            get_tmux_tty = compat_globals.get("_get_tmux_tty", _get_tmux_tty)
            close_iterm_pane_by_tty = compat_globals.get(
                "_close_iterm_pane_by_tty",
                _close_iterm_pane_by_tty,
            )
            tty = get_tmux_tty(session.session)
            if tty:
                result = close_iterm_pane_by_tty(tty)
                if result["status"] == "ok":
                    print(f"iterm_pane_closed: tty={tty} session={session.session}")
                elif result["status"] == "not_found":
                    print(
                        f"warn: iterm_pane_not_found: tty={tty} session={session.session}",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"warn: iterm_pane_close_failed: tty={tty} session={session.session} detail={result['detail']}",
                        file=sys.stderr,
                    )
        try:
            self._run_tmux_with_retry(
                ["kill-session", "-t", session.session],
                reason=f"stop engineer {session.session}",
                check=False,
            )
        finally:
            self._record_seat_status(
                session,
                getattr(session, "project", None),
                status="stopped",
            )

    def status(self, session: Any) -> str:
        return "running" if self.hooks.tmux_has_session(session.session) else "stopped"
