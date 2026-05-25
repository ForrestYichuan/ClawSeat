"""ClawSeat v3 fuzz harness (Phase 3, custom — no hypothesis dep).

Reads `brief.fuzz_spec` and generates random test cases per spec §4.7.3.
Supports three generator types:

- expression: random AST built from a list of primitives + value types
- combinatorial: Cartesian product of named dimensions (card × relic × …)
- random_value: simple random scalar (int / string / enum) per constraint

Each test case is passed to a user-supplied target function or shell command;
exception / non-zero exit → recorded with seed for reproduction.

Deterministic when seed is provided. Caller supplies iteration count.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


class FuzzError(RuntimeError):
    pass


@dataclass
class FuzzCase:
    seed: int
    payload: Any
    descriptor: str

    def to_dict(self) -> dict:
        return {"seed": self.seed, "payload": self.payload, "descriptor": self.descriptor}


@dataclass
class FuzzResult:
    spec_name: str
    generator: str
    iterations: int
    cases_run: int = 0
    unique_payloads: int = 0
    failures: list[dict] = field(default_factory=list)
    elapsed_ms: int = 0

    @property
    def ok(self) -> bool:
        return not self.failures


# -------------------- Generators --------------------


def _gen_expression(spec: dict, rnd: random.Random) -> FuzzCase:
    """Generate a random nested expression. spec keys:
    - primitives: list[str]  required
    - max_depth: int (default 3)
    - leaves: list[Any] (default [1, "x"])
    """
    primitives = spec.get("primitives") or []
    if not primitives:
        raise FuzzError("expression generator requires 'primitives' list")
    max_depth = int(spec.get("max_depth", 3))
    leaves = spec.get("leaves") or [1, 2, 3, "x", "y"]

    def build(depth: int) -> Any:
        if depth <= 0 or rnd.random() < 0.3:
            return rnd.choice(leaves)
        op = rnd.choice(primitives)
        argc = rnd.randint(1, 3)
        return {"op": op, "args": [build(depth - 1) for _ in range(argc)]}

    seed = rnd.randint(0, 2**31 - 1)
    payload = build(max_depth)
    return FuzzCase(seed=seed, payload=payload, descriptor=f"expr depth≤{max_depth}")


def _gen_combinatorial(spec: dict, rnd: random.Random) -> FuzzCase:
    """Random pick from product of named dimensions (used by sampling mode).

    spec.dimensions = {dim_name: [values]}.
    Each case is a dict {dim: value}.

    Note: post-retest #8 — this is the FALLBACK sampler. Cartesian coverage
    is preferred when iterations ≥ product size; see _build_cartesian_plan
    + run_fuzz coverage handling.
    """
    dims = spec.get("dimensions") or {}
    if not dims:
        raise FuzzError("combinatorial generator requires 'dimensions' map")
    payload = {dim: rnd.choice(list(values)) for dim, values in dims.items()}
    seed = rnd.randint(0, 2**31 - 1)
    return FuzzCase(seed=seed, payload=payload, descriptor=f"combo of {sorted(dims)}")


def _cartesian_size(dims: dict) -> int:
    size = 1
    for values in dims.values():
        size *= max(1, len(values))
    return size


def _build_cartesian_plan(dims: dict) -> list[dict]:
    """Post-retest #8: enumerate full Cartesian product deterministically."""
    import itertools
    keys = list(dims.keys())
    value_lists = [list(dims[k]) for k in keys]
    return [
        dict(zip(keys, combo))
        for combo in itertools.product(*value_lists)
    ]


def _gen_random_value(spec: dict, rnd: random.Random) -> FuzzCase:
    """Simple scalar generation per type/bounds."""
    t = spec.get("type", "int")
    if t == "int":
        lo, hi = spec.get("bounds", [0, 100])
        payload = rnd.randint(int(lo), int(hi))
    elif t == "enum":
        choices = spec.get("choices") or []
        if not choices:
            raise FuzzError("random_value enum requires 'choices'")
        payload = rnd.choice(choices)
    elif t == "string":
        chars = spec.get("chars", "abcdefghij")
        length = rnd.randint(1, int(spec.get("max_length", 8)))
        payload = "".join(rnd.choice(chars) for _ in range(length))
    else:
        raise FuzzError(f"unknown random_value type {t!r}")
    seed = rnd.randint(0, 2**31 - 1)
    return FuzzCase(seed=seed, payload=payload, descriptor=f"random {t}")


_GENERATORS: dict[str, Callable[[dict, random.Random], FuzzCase]] = {
    "expression": _gen_expression,
    "combinatorial": _gen_combinatorial,
    "random_value": _gen_random_value,
}


# -------------------- Runner --------------------


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def run_fuzz(
    spec: dict,
    target_fn: Callable[[Any], None] | None = None,
    target_command: str | None = None,
    iterations: int = 100,
    seed: int | None = None,
    out_dir: Path | None = None,
) -> FuzzResult:
    """Execute fuzz spec. Exactly one of target_fn or target_command required.

    target_fn(payload) -> None must raise on failure.
    target_command receives the payload as JSON on stdin.

    Post-retest #8: combinatorial generator now enumerates full Cartesian
    product when iterations ≥ product size. Spec can opt back into sampling
    via spec.combinatorial_mode = "sample".
    """
    if (target_fn is None) == (target_command is None):
        raise FuzzError("exactly one of target_fn / target_command required")

    generator_name = spec.get("generator") or "expression"
    if generator_name not in _GENERATORS:
        raise FuzzError(f"unknown generator {generator_name!r}; "
                        f"valid: {sorted(_GENERATORS)}")
    gen = _GENERATORS[generator_name]

    spec_name = str(spec.get("name") or generator_name)
    seed = seed if seed is not None else spec.get("seed")
    rnd = random.Random(seed) if seed is not None else random.Random()

    result = FuzzResult(spec_name=spec_name, generator=generator_name, iterations=iterations)
    start = time.monotonic()

    # Post-retest #8: deterministic full-coverage path for combinatorial
    cartesian_plan: list[dict] | None = None
    if generator_name == "combinatorial":
        dims = spec.get("dimensions") or {}
        product_size = _cartesian_size(dims)
        mode = str(spec.get("combinatorial_mode", "coverage"))
        if mode == "coverage" and iterations >= product_size:
            cartesian_plan = _build_cartesian_plan(dims)

    seen_payloads: set[str] = set()
    for i in range(iterations):
        if cartesian_plan is not None:
            if i < len(cartesian_plan):
                payload = cartesian_plan[i]
                case = FuzzCase(seed=i, payload=payload, descriptor=f"cartesian #{i}")
            else:
                # Beyond coverage iterations falls back to random sampling
                case = gen(spec, rnd)
        else:
            case = gen(spec, rnd)
        seen_payloads.add(json.dumps(case.payload, sort_keys=True, default=str))
        result.cases_run += 1
        try:
            if target_fn is not None:
                target_fn(case.payload)
            else:
                proc = subprocess.run(
                    target_command,
                    shell=True,
                    input=json.dumps(case.payload, ensure_ascii=False),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if proc.returncode != 0:
                    raise RuntimeError(
                        f"exit {proc.returncode}: {(proc.stderr or proc.stdout)[:300]}"
                    )
        except Exception as exc:  # noqa: BLE001
            result.failures.append({
                "iteration": i,
                "case": case.to_dict(),
                "error": str(exc),
            })

    result.elapsed_ms = int((time.monotonic() - start) * 1000)
    result.unique_payloads = len(seen_payloads)

    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        log_path = out_dir / f"fuzz__{spec_name}__{_utc_now().replace(':', '-')}.json"
        log_path.write_text(
            json.dumps({
                "spec_name": result.spec_name,
                "generator": result.generator,
                "iterations": result.iterations,
                "cases_run": result.cases_run,
                "elapsed_ms": result.elapsed_ms,
                "failures": result.failures,
                "ok": result.ok,
            }, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ClawSeat v3 fuzz harness (custom, no external deps)."
    )
    parser.add_argument("--spec-file", required=True, dest="spec_file",
                        help="JSON file with fuzz spec")
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--target-command", required=True, dest="target_command",
                        help="Shell command; payload passed on stdin as JSON")
    parser.add_argument("--out-dir", default=None, dest="out_dir")
    args = parser.parse_args(argv)

    spec = json.loads(Path(args.spec_file).read_text(encoding="utf-8"))
    try:
        result = run_fuzz(
            spec,
            target_command=args.target_command,
            iterations=args.iterations,
            seed=args.seed,
            out_dir=Path(args.out_dir) if args.out_dir else None,
        )
    except FuzzError as exc:
        print(f"fuzz error: {exc}", file=sys.stderr)
        return 2

    print(f"spec: {result.spec_name} generator: {result.generator}")
    print(f"cases_run: {result.cases_run}/{result.iterations}")
    print(f"failures: {len(result.failures)}")
    print(f"elapsed_ms: {result.elapsed_ms}")
    if result.failures:
        for f in result.failures[:3]:
            print(f"  - iter {f['iteration']} seed={f['case']['seed']}: {f['error'][:120]}")
        if len(result.failures) > 3:
            print(f"  (+ {len(result.failures) - 3} more failures)")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
