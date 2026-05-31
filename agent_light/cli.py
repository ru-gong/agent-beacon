from __future__ import annotations

import argparse
import json
import sys
import time

from .controller import AgentController, known_agent_ids
from .hook_events import parse_hook_stdin, write_hook_event_status
from .models import StatusEvent
from .tray_app import TrayApp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-light",
        description="Agent Beacon: cross-platform tray status indicator for AI agents.",
    )
    parser.add_argument("--scan", action="store_true", help="Scan once and print agents.")
    parser.add_argument(
        "--json", action="store_true", help="Use JSON output with --scan."
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run listener without tray UI and print status changes.",
    )
    parser.add_argument(
        "--hook-event",
        action="store_true",
        help="Read one Agent hook JSON event from stdin and update Agent Beacon status.",
    )
    parser.add_argument(
        "--agent",
        choices=known_agent_ids(),
        help="Agent id to connect at startup, or source agent for --hook-event.",
    )
    parser.add_argument(
        "--session",
        help="Session id to connect at startup, such as codex_cli:12345.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=0.25,
        help="Polling interval in seconds. Default: 0.25.",
    )
    parser.add_argument(
        "--provider",
        help="Hook provider label, such as claude, codex-cli, or codex-desktop.",
    )
    parser.add_argument(
        "--monitor-id",
        help="Agent Beacon monitor id that this hook event belongs to.",
    )
    parser.add_argument(
        "--session-root-pid",
        type=int,
        help="Root process id of the selected session, used to avoid cross-session events.",
    )
    parser.add_argument(
        "--event-name",
        help="Override hook event name when the provider payload does not include one.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.hook_event:
        return _handle_hook_event(args)

    controller = AgentController.build_default()
    controller.poll_interval_seconds = args.poll_interval

    if args.scan:
        candidates = controller.rescan()
        if args.json:
            print(
                json.dumps(
                    [
                        {
                            "agent_id": candidate.agent_id,
                            "display_name": candidate.display_name,
                            "confidence": candidate.confidence,
                            "matched_by": candidate.matched_by,
                            "sessions": [
                                {
                                    "session_id": session.session_id,
                                    "root_pid": session.root_pid,
                                    "project_root": session.project_root,
                                    "label": session.menu_label,
                                    "pids": session.pids,
                                    "confidence": session.confidence,
                                    "matched_by": session.matched_by,
                                }
                                for session in candidate.sessions
                            ],
                        }
                        for candidate in candidates
                    ],
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            if not candidates:
                print("未检测到活跃 Agent")
            for candidate in candidates:
                reasons = "; ".join(candidate.matched_by)
                print(
                    f"{candidate.display_name} ({candidate.agent_id}) "
                    f"confidence={candidate.confidence} sessions={candidate.session_count} {reasons}"
                )
                for session in candidate.sessions:
                    pids = ", ".join(str(pid) for pid in session.pids)
                    print(
                        f"  - {session.session_id} root={session.root_pid} "
                        f"project={session.project_root or '-'} pids=[{pids}] {session.menu_label}"
                    )
        return 0

    if args.headless:
        return _run_headless(
            controller,
            selected_agent_id=args.agent,
            selected_session_id=args.session,
        )

    TrayApp(
        controller,
        initial_agent_id=args.agent,
        initial_session_id=args.session,
    ).run()
    return 0


def _handle_hook_event(args: argparse.Namespace) -> int:
    if not args.agent:
        print("--hook-event requires --agent", file=sys.stderr)
        return 2
    try:
        payload = parse_hook_stdin(sys.stdin.read())
        write_hook_event_status(
            agent_id=args.agent,
            payload=payload,
            provider=args.provider,
            monitor_id=args.monitor_id,
            session_root_pid=args.session_root_pid,
            event_name=args.event_name,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Failed to record hook event: {exc}", file=sys.stderr)
        return 1
    return 0


def _run_headless(
    controller: AgentController,
    selected_agent_id: str | None = None,
    selected_session_id: str | None = None,
) -> int:
    candidates = controller.rescan()
    sessions = [
        session
        for candidate in candidates
        if selected_agent_id is None or candidate.agent_id == selected_agent_id
        for session in candidate.sessions
    ]
    if selected_session_id is not None:
        sessions = [
            session for session in sessions if session.session_id == selected_session_id
        ]
    if len(sessions) != 1:
        print("请选择要监听的 Session：", file=sys.stderr)
        for session in sessions:
            print(
                f"  {session.session_id}: {session.display_name} · {session.menu_label}",
                file=sys.stderr,
            )
        return 2
    session = sessions[0]

    def on_status(event: StatusEvent) -> None:
        print(
            json.dumps(
                {
                    "agent_id": event.agent_id,
                    "session_id": event.session_id,
                    "session_label": event.session_label,
                    "status": event.status.value,
                    "message": event.message,
                    "milestone": event.milestone,
                    "timestamp": event.timestamp,
                    "monitor_id": event.monitor_id,
                    "hook_session_id": event.hook_session_id,
                    "hook_event_name": event.hook_event_name,
                    "source": event.source,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    controller.subscribe(on_status)
    controller.connect_session(session)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        controller.stop()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
