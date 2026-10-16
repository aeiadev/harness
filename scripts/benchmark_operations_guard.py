#!/usr/bin/env python3
"""Measure explicit guard subprocess runs; no inspected command is executed.

Timing is evidence, not a unit-test gate. Output parity is exact. Run on an idle,
fixed runner and compare timing against the separately frozen release baseline.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time

PINNED_RUNTIME = {
    "hooks/guard/danger_cmd_guard.py": "45557084fb1b010d182375d64579d0fbed59b881fdcbd4a9ef241eba26b4a01f",
    "hooks/guard/shell_syntax.py": "e5e972a362ed54fa4294cf212baf41ad6250bace10de4e3cb224c47660aeaf68",
    "hooks/guard/wait-loop-guard.py": "85ace7bebd1327ce93b7a938ff5dbca9197ffbea2b9b55bffb710891e63d0b4b",
    "hooks/guard/danger-cmd-guard.sh": "b302d1981f78e97a58a145b3e03a68a1ec1f12ff1fab5fb909f29ecc128709ad",
    "hooks/pyrun.sh": "315107a5aa09d1a6cf6c9f56606206c9186a91e8d52b4e4b114412f07e723d80",
    "hooks/checkpoint/compat.py": "789ec34a2ebdd08eb755b3096ecdcaa35df160277441176a713993d003522fdd",
}


def cases():
    return {
        "claude-local": {"tool_name": "Bash", "tool_input": {"command": "git status --short"}},
        "codex-local": {"tool_name": "functions.exec_command", "hook_event_name": "PreToolUse",
                        "tool_input": {"cmd": "git status --short"}},
        "ssh-feature-off": {"tool_name": "Bash", "tool_input": {
            "command": "ssh deploy@example.test 'cat /srv/site/VERSION'"}},
        "local-block": {"tool_name": "Bash", "tool_input": {"command": "git push --mirror origin"}},
        "enabled-nonrelevant": {"tool_name": "Bash", "tool_input": {"command": "git status --short"}},
        "enabled-allowed-ssh": {"tool_name": "Bash", "tool_input": {
            "command": "ssh deploy@example.test 'cat /srv/site/VERSION'"}},
        "enabled-warning": {"tool_name": "Bash", "tool_input": {
            "command": "ssh deploy@example.test 'rm /srv/site/current'"}},
    }


def revision(root):
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                            stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
    return result.stdout.decode("ascii").strip() if result.returncode == 0 else None


def source_fingerprints(root):
    names = ("hooks/guard/danger_cmd_guard.py", "hooks/guard/shell_syntax.py",
             "hooks/guard/wait-loop-guard.py",
             "hooks/guard/danger-cmd-guard.sh", "hooks/guard/ssh_operations.py",
             "hooks/guard/ssh_operations_cli.py",
             "hooks/guard/operations_policy.py", "hooks/pyrun.sh", "hooks/checkpoint/compat.py")
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in names if (root / name).is_file()}


def measure(root, payload, samples, warmup, env, cwd):
    import resource  # The benchmark requires a POSIX runner.
    hook = root / "hooks/guard/danger_cmd_guard.py"
    entry = root / "hooks/guard/danger-cmd-guard.sh"
    if not hook.is_file() or not entry.is_file():
        raise ValueError("checkout is missing the guard entry point")
    # The benchmark targets the documented POSIX installed shell entry point.
    # Use this interpreter through PATH exactly as the shell launcher does.
    command = ["/bin/bash", str(entry)]
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    times = []
    raw_samples = []
    outputs = set()
    for index in range(samples + warmup):
        cpu_before = resource.getrusage(resource.RUSAGE_CHILDREN)
        started = time.perf_counter()
        run = subprocess.run(command, input=encoded, capture_output=True, env=env,
                             cwd=cwd, timeout=10)
        elapsed = (time.perf_counter() - started) * 1000
        cpu_after = resource.getrusage(resource.RUSAGE_CHILDREN)
        if index >= warmup:
            times.append(elapsed)
            raw_samples.append({
                "wall_ms": elapsed,
                "child_user_ms": (cpu_after.ru_utime - cpu_before.ru_utime) * 1000,
                "child_system_ms": (cpu_after.ru_stime - cpu_before.ru_stime) * 1000,
                "voluntary_switches": cpu_after.ru_nvcsw - cpu_before.ru_nvcsw,
                "involuntary_switches": cpu_after.ru_nivcsw - cpu_before.ru_nivcsw,
            })
            outputs.add((run.returncode, run.stdout, run.stderr))
    if len(outputs) != 1:
        raise ValueError("guard output changed within a measurement series")
    code, stdout, stderr = outputs.pop()
    return {"samples": samples, "warmup": warmup,
            "raw_samples": raw_samples,
            "p50_ms": statistics.median(times),
            "p95_ms": sorted(times)[max(0, int(len(times) * .95) - 1)],
            "exit": code, "stdout_hex": stdout.hex(), "stderr_hex": stderr.hex(),
            "output_bytes": len(stdout) + len(stderr),
            "input_sha256": hashlib.sha256(encoded).hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=200)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--case", action="append", choices=tuple(cases()),
                        help="repeat to measure selected labelled cases")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.samples < 200 or args.warmup < 1:
        parser.error("at least 200 samples and one warmup are required")
    if os.name != "posix" or not Path("/bin/bash").is_file():
        parser.error("this benchmark requires a qualified POSIX runner with /bin/bash")
    baseline, candidate = args.baseline.resolve(), args.candidate.resolve()
    if any(source_fingerprints(baseline).get(name) != digest for name, digest in PINNED_RUNTIME.items()):
        parser.error("baseline runtime content differs from the pinned 0.4.1 source")
    report = {"schema_version": 1,
              "runner": {"platform": platform.platform(), "python": sys.version.split()[0]},
              "baseline_commit": revision(baseline), "candidate_commit": revision(candidate),
              "runtime_source_sha256": {"baseline": source_fingerprints(baseline),
                                        "candidate": source_fingerprints(candidate)},
              "frozen_p95_ceiling_ms": {"claude-local": 101.157858, "codex-local": 111.935438},
              "claims": "offline hook output/timing only; no model, SSH or billing measurement",
              "cases": {}}
    parity = True
    try:
        with tempfile.TemporaryDirectory(prefix="operations-guard-benchmark-") as temporary:
            home = Path(temporary) / "home"
            home.mkdir()
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith(("HARNESS_", "ROUTER_"))}
            env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / "config"),
                       XDG_STATE_HOME=str(home / "state"), CLAUDE_HOME=str(home / ".claude"),
                       CODEX_HOME=str(home / ".codex"), PYTHONDONTWRITEBYTECODE="1")
            project = home / "project"
            (project / ".harness").mkdir(parents=True)
            policy_bytes = (candidate / "tests/fixtures/ssh-operations.json").read_bytes()
            sample = json.loads(policy_bytes)
            policy_data = json.dumps(sample["policy"]).encode()
            (project / ".harness/operations.json").write_bytes(policy_data)
            registry = home / "config/harness"
            registry.mkdir(parents=True, mode=0o700)
            (registry / "operations-bindings.json").write_text(json.dumps({"schema_version": 1,
                "bindings": [{"binding_key": str(project), "project_root": str(project),
                              "policy_path": ".harness/operations.json",
                              "sha256": hashlib.sha256(policy_data).hexdigest(), "mode": "report"}]}))
            (registry / "operations-bindings.json").chmod(0o600)
            for name, payload in cases().items():
                if args.case and name not in args.case:
                    continue
                payload = dict(payload)
                payload["cwd"] = str(project)
                pair = {}
                active_registry = name.startswith("enabled-")
                case_env = dict(env)
                if not active_registry:
                    case_env["XDG_CONFIG_HOME"] = str(home / "off-config")
                for label, root in (("baseline", baseline), ("candidate", candidate)):
                    pair[label] = measure(root, payload, args.samples, args.warmup, case_env, project)
                pair["output_parity"] = all(pair["baseline"][field] == pair["candidate"][field]
                                            for field in ("exit", "stdout_hex", "stderr_hex"))
                pair["parity_required"] = name != "enabled-warning"
                pair["p95_delta_ms"] = pair["candidate"]["p95_ms"] - pair["baseline"]["p95_ms"]
                parity = parity and (not pair["parity_required"] or pair["output_parity"])
                report["cases"][name] = pair
            report["frozen_thresholds_met"] = all(
                report["cases"][name]["candidate"]["p95_ms"] <= limit
                for name, limit in report["frozen_p95_ceiling_ms"].items()
                if name in report["cases"])
        report["output_parity"] = parity
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print("benchmark failed: " + type(error).__name__, file=sys.stderr)
        return 2
    print("guard output parity: " + ("PASS" if parity else "FAIL"))
    return 0 if parity else 1


if __name__ == "__main__":
    raise SystemExit(main())
