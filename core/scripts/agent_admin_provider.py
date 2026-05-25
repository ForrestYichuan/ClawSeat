from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any

from providers import (
    Provider,
    ProviderConflictError,
    ProviderError,
    ProviderMutationResult,
    ProviderNotFoundError,
    ProviderReferenceError,
    ProviderSecretMissingError,
    ProviderValidationError,
    add_provider,
    get_provider,
    list_providers,
    migrate_legacy_provider_secrets,
    remove_provider,
    rename_provider,
    update_provider,
)


@dataclass(frozen=True)
class ProviderCommandResult:
    provider: Provider | None = None
    providers: tuple[Provider, ...] = ()
    session_refs: tuple[Any, ...] = ()


class ProviderHandlers:
    def __init__(self, error_cls: type[Exception]) -> None:
        self.error_cls = error_cls
        self._legacy_migrated = False

    def _ensure_legacy_migration(self) -> None:
        if self._legacy_migrated:
            return
        migrate_legacy_provider_secrets()
        self._legacy_migrated = True

    @staticmethod
    def _json(data: dict[str, Any]) -> None:
        print(json.dumps(data, ensure_ascii=False, sort_keys=True))

    @staticmethod
    def _session_ref_payload(ref: Any) -> dict[str, str]:
        return {
            "project": str(getattr(ref, "project", "")),
            "seat_id": str(getattr(ref, "seat_id", "")),
            "path": str(getattr(ref, "path", "")),
            "provider": str(getattr(ref, "provider", "")),
            "secret_file": str(getattr(ref, "secret_file", "")),
        }

    def _emit_provider(self, provider: Provider, *, json_mode: bool) -> None:
        if json_mode:
            self._json({"provider": provider.as_dict()})
            return
        for key, value in provider.as_dict().items():
            if key == "has_secret":
                value = "yes" if value else "no"
            print(f"{key} = {value}")

    def _emit_provider_list(self, providers: list[Provider], *, json_mode: bool) -> None:
        if json_mode:
            self._json({"providers": [provider.as_dict() for provider in providers]})
            return
        for provider in providers:
            print(provider.as_human_line())

    def _emit_error(self, exc: Exception) -> None:
        print(str(exc), file=sys.stderr)

    def _emit_mutation(
        self,
        result: ProviderMutationResult,
        *,
        json_mode: bool,
        action: str,
    ) -> None:
        if json_mode:
            payload = {
                "provider": result.provider.as_dict(),
                "session_refs": [self._session_ref_payload(ref) for ref in result.session_refs],
                "action": action,
            }
            self._json(payload)
            return
        print(f"{action} {result.provider.name}")
        if result.session_refs:
            print("session_refs =")
            for ref in result.session_refs:
                print(f"  {ref.project}\t{ref.seat_id}\t{ref.path}")

    def list(self, args: argparse.Namespace) -> int:
        try:
            self._ensure_legacy_migration()
            providers = list_providers(getattr(args, "tool", None))
        except ProviderError as exc:
            self._emit_error(exc)
            return 2
        self._emit_provider_list(providers, json_mode=bool(getattr(args, "json", False)))
        return 0

    def get(self, args: argparse.Namespace) -> int:
        try:
            self._ensure_legacy_migration()
            provider = get_provider(args.name)
        except ProviderError as exc:
            self._emit_error(exc)
            return 2
        if provider is None:
            self._emit_error(ProviderNotFoundError(f"provider {args.name!r} not found"))
            return 1
        self._emit_provider(provider, json_mode=bool(getattr(args, "json", False)))
        return 0

    def add(self, args: argparse.Namespace) -> int:
        if not getattr(args, "secret_stdin", False):
            self._emit_error(self.error_cls("provider add requires --secret-stdin"))
            return 2
        try:
            self._ensure_legacy_migration()
            secret = sys.stdin.read()
            result = add_provider(
                Provider(
                    name=args.name,
                    tool=args.tool,
                    kind=args.kind,
                    family=args.family,
                    secret_file="",
                    base_url=str(getattr(args, "base_url", "") or ""),
                    model=str(getattr(args, "model", "") or ""),
                ),
                secret,
            )
        except ProviderConflictError as exc:
            self._emit_error(exc)
            return 1
        except ProviderSecretMissingError as exc:
            self._emit_error(exc)
            return 3
        except ProviderValidationError as exc:
            self._emit_error(exc)
            return 2
        except ProviderError as exc:
            self._emit_error(exc)
            return 2
        self._emit_mutation(result, json_mode=bool(getattr(args, "json", False)), action="added")
        return 0

    def update(self, args: argparse.Namespace) -> int:
        patch: dict[str, Any] = {}
        if getattr(args, "base_url", None) is not None:
            patch["base_url"] = args.base_url
        if getattr(args, "model", None) is not None:
            patch["model"] = args.model
        secret = None
        if getattr(args, "secret_stdin", False):
            secret = sys.stdin.read()
        if not patch and secret is None:
            self._emit_error(self.error_cls("provider update requires --base-url, --model, or --secret-stdin"))
            return 2
        try:
            self._ensure_legacy_migration()
            result = update_provider(args.name, patch, secret=secret)
        except ProviderNotFoundError as exc:
            self._emit_error(exc)
            return 1
        except ProviderSecretMissingError as exc:
            self._emit_error(exc)
            return 3
        except ProviderValidationError as exc:
            self._emit_error(exc)
            return 2
        except ProviderError as exc:
            self._emit_error(exc)
            return 2
        self._emit_mutation(result, json_mode=bool(getattr(args, "json", False)), action="updated")
        return 0

    def remove(self, args: argparse.Namespace) -> int:
        try:
            self._ensure_legacy_migration()
            result = remove_provider(args.name, force=bool(getattr(args, "force", False)))
        except ProviderReferenceError as exc:
            self._emit_error(exc)
            if bool(getattr(args, "json", False)):
                self._json(
                    {
                        "error": "provider_referenced",
                        "provider": args.name,
                        "session_refs": [self._session_ref_payload(ref) for ref in exc.refs],
                    }
                )
            return 4
        except ProviderNotFoundError as exc:
            self._emit_error(exc)
            return 1
        except ProviderError as exc:
            self._emit_error(exc)
            return 2
        self._emit_mutation(result, json_mode=bool(getattr(args, "json", False)), action="removed")
        return 0

    def rename(self, args: argparse.Namespace) -> int:
        try:
            self._ensure_legacy_migration()
            result = rename_provider(args.from_name, args.to_name)
        except ProviderConflictError as exc:
            self._emit_error(exc)
            return 1
        except ProviderNotFoundError as exc:
            self._emit_error(exc)
            return 1
        except ProviderValidationError as exc:
            self._emit_error(exc)
            return 2
        except ProviderError as exc:
            self._emit_error(exc)
            return 2
        if bool(getattr(args, "json", False)):
            self._json(
                {
                    "action": "renamed",
                    "from": args.from_name,
                    "to": args.to_name,
                    "provider": result.provider.as_dict(),
                    "session_refs": [self._session_ref_payload(ref) for ref in result.session_refs],
                }
            )
            return 0
        print(f"renamed {args.from_name} -> {args.to_name}")
        if result.session_refs:
            print("session_refs =")
            for ref in result.session_refs:
                print(f"  {ref.project}\t{ref.seat_id}\t{ref.path}")
        return 0


PROVIDER_HANDLERS = ProviderHandlers(error_cls=RuntimeError)


def cmd_provider_list(args: argparse.Namespace) -> int:
    return PROVIDER_HANDLERS.list(args)


def cmd_provider_get(args: argparse.Namespace) -> int:
    return PROVIDER_HANDLERS.get(args)


def cmd_provider_add(args: argparse.Namespace) -> int:
    return PROVIDER_HANDLERS.add(args)


def cmd_provider_update(args: argparse.Namespace) -> int:
    return PROVIDER_HANDLERS.update(args)


def cmd_provider_remove(args: argparse.Namespace) -> int:
    return PROVIDER_HANDLERS.remove(args)


def cmd_provider_rename(args: argparse.Namespace) -> int:
    return PROVIDER_HANDLERS.rename(args)
