"""Local operator CLI for Burner 1 control-plane state only."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence, TextIO

from .control_plane import ControlPlane, ControlPlaneError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="EVCOWBBE local deployment-control state")
    parser.add_argument("--state-db", required=True, help="caller-selected SQLite state database path")
    subcommands = parser.add_subparsers(dest="command", required=True)

    status = subcommands.add_parser("status", help="show concise current control state")
    status.add_argument("--verbose", action="store_true", help="include requests, runs, and migration decisions")
    status.add_argument(
        "--trusted-prod-sha",
        help="externally verified prod SHA for inspection only; this local CLI cannot verify it",
    )
    subcommands.add_parser("requests", help="list accepted deployment requests")
    history = subcommands.add_parser("history", help="list append-only audit events")
    history.add_argument("--limit", type=int, default=100)

    submit = subcommands.add_parser("submit", help="accept one exact prod push request")
    submit.add_argument("--event", required=True)
    submit.add_argument("--sha", required=True)
    submit.add_argument("--branch", required=True)
    submit.add_argument("--actor", required=True)
    submit.add_argument("--source-id", required=True)

    for name in ("pause", "resume"):
        command = subcommands.add_parser(name, help=f"{name} automatic reconciliation")
        command.add_argument("--operator", required=True)
        command.add_argument("--reason", required=True)
    for name in ("sideline", "release", "retry"):
        command = subcommands.add_parser(name, help=f"{name} one SHA for automatic reconciliation")
        command.add_argument("sha")
        command.add_argument("--operator", required=True)
        command.add_argument("--reason", required=True)
    cancel_retry = subcommands.add_parser("cancel-retry", help="withdraw one unused SHA retry authorization")
    cancel_retry.add_argument("sha")
    cancel_retry.add_argument("--operator", required=True)
    cancel_retry.add_argument("--reason", required=True)
    for name in ("rollback-hold", "rollback-release"):
        command = subcommands.add_parser(name, help="set or remove a durable automatic-reconciliation hold")
        if name == "rollback-hold":
            command.add_argument("sha")
        command.add_argument("--operator", required=True)
        command.add_argument("--reason", required=True)
    return parser


def main(
    argv: Sequence[str] | None = None, stdout: TextIO | None = None, stderr: TextIO | None = None
) -> int:
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        control_plane = ControlPlane(arguments.state_db)
        if arguments.command == "status":
            result = control_plane.status(verbose=arguments.verbose, trusted_prod_sha=arguments.trusted_prod_sha)
        elif arguments.command == "requests":
            result = {"requests": control_plane.list_requests()}
        elif arguments.command == "history":
            result = {"events": control_plane.history(arguments.limit)}
        elif arguments.command == "submit":
            result = control_plane.submit_request(
                {
                    "event": arguments.event,
                    "sha": arguments.sha,
                    "branch": arguments.branch,
                    "actor": arguments.actor,
                    "source_id": arguments.source_id,
                }
            )
        elif arguments.command == "pause":
            result = control_plane.pause(arguments.operator, arguments.reason)
        elif arguments.command == "resume":
            result = {"released": control_plane.resume(arguments.operator, arguments.reason)}
        elif arguments.command == "sideline":
            result = control_plane.sideline(arguments.sha, arguments.operator, arguments.reason)
        elif arguments.command == "release":
            result = {"released": control_plane.release(arguments.sha, arguments.operator, arguments.reason)}
        elif arguments.command == "retry":
            result = control_plane.authorize_retry(arguments.sha, arguments.operator, arguments.reason)
        elif arguments.command == "cancel-retry":
            result = control_plane.cancel_retry(arguments.sha, arguments.operator, arguments.reason)
        elif arguments.command == "rollback-hold":
            result = control_plane.set_rollback_hold(arguments.sha, arguments.operator, arguments.reason)
        elif arguments.command == "rollback-release":
            result = {"released": control_plane.clear_rollback_hold(arguments.operator, arguments.reason)}
        else:  # pragma: no cover - argparse constrains commands.
            raise AssertionError(f"unhandled command {arguments.command}")
    except ControlPlaneError as exc:
        print(canonical_error(exc), file=stderr)
        return exc.exit_code
    print(json.dumps(result, sort_keys=True, separators=(",", ":")), file=stdout)
    return 0


def canonical_error(error: ControlPlaneError) -> str:
    return json.dumps({"error": error.__class__.__name__, "message": str(error)}, sort_keys=True)


if __name__ == "__main__":
    raise SystemExit(main())
