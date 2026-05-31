from __future__ import annotations

import argparse
import json
import sys
import time

from .controller import AgentController, known_agent_ids
from .models import StatusEvent
from .tray_app import TrayApp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-light",
        description="Cross-platform tray traffic-light status indicator for AI agents.",
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
        "--agent",
        choices=known_agent_ids(),
        help="Agent id to connect in headless mode.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=0.25,
        help="Polling interval in seconds. Default: 0.25.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
                            "pids": candidate.pids,
                            "confidence": candidate.confidence,
                            "matched_by": candidate.matched_by,
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
                pids = ", ".join(str(pid) for pid in candidate.pids)
                reasons = "; ".join(candidate.matched_by)
                print(
                    f"{candidate.display_name} ({candidate.agent_id}) "
                    f"confidence={candidate.confidence} pids=[{pids}] {reasons}"
                )
        return 0

    if args.headless:
        return _run_headless(controller, selected_agent_id=args.agent)

    TrayApp(controller).run()
    return 0


def _run_headless(
    controller: AgentController, selected_agent_id: str | None = None
) -> int:
    candidates = controller.rescan()
    agent_id = selected_agent_id
    if agent_id is None and len(candidates) == 1:
        agent_id = candidates[0].agent_id
    if agent_id is None:
        print("请选择要监听的 Agent：", file=sys.stderr)
        for candidate in candidates:
            print(f"  {candidate.agent_id}: {candidate.display_name}", file=sys.stderr)
        return 2

    def on_status(event: StatusEvent) -> None:
        print(
            json.dumps(
                {
                    "agent_id": event.agent_id,
                    "status": event.status.value,
                    "message": event.message,
                    "milestone": event.milestone,
                    "timestamp": event.timestamp,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    controller.subscribe(on_status)
    controller.connect(agent_id)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        controller.stop()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
