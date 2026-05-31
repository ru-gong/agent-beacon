from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .definitions import AGENT_DEFINITIONS
from .models import AgentCandidate, AgentDefinition, AgentSessionCandidate, ProcessInfo
from .process_source import ProcessSource


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


GENERIC_LAUNCHERS = frozenset(
    {
        "node",
        "npm",
        "npx",
        "pnpm",
        "yarn",
        "python",
        "python3",
        "bash",
        "sh",
        "zsh",
    }
)

APPLICATION_AGENT_IDS = frozenset({"codex_desktop"})


@dataclass(frozen=True)
class ProcessMatch:
    process: ProcessInfo
    score: int
    reasons: tuple[str, ...]


@dataclass
class AgentMatcher:
    min_process_score: int = 25

    def match_process(
        self, definition: AgentDefinition, process: ProcessInfo
    ) -> ProcessMatch | None:
        name = _normalize(process.name)
        command = _normalize(process.command_text)
        haystack = f"{name} {command}".strip()
        if not haystack:
            return None

        for keyword in definition.exclude_keywords:
            if _normalize(keyword) in haystack:
                return None

        score = 0
        reasons: list[str] = []
        process_name_hits: list[tuple[str, int, str]] = []
        cmdline_hit = False

        for keyword in definition.process_name_keywords:
            normalized = _normalize(keyword)
            if normalized and normalized == name:
                process_name_hits.append((f"process-name={keyword}", 45, normalized))
            elif normalized and normalized in name:
                process_name_hits.append((f"process-name~={keyword}", 35, normalized))

        for keyword in definition.cmdline_keywords:
            normalized = _normalize(keyword)
            if normalized and normalized in command:
                score += 25
                reasons.append(f"cmdline~={keyword}")
                cmdline_hit = True

        for reason, hit_score, normalized_name in process_name_hits:
            if normalized_name in GENERIC_LAUNCHERS and not cmdline_hit:
                continue
            score += hit_score
            reasons.append(reason)

        if score < self.min_process_score:
            return None
        return ProcessMatch(process=process, score=score, reasons=tuple(reasons))

    def matches_for_definition(
        self, definition: AgentDefinition, processes: Iterable[ProcessInfo]
    ) -> list[ProcessMatch]:
        matches = [
            match
            for process in processes
            if (match := self.match_process(definition, process)) is not None
        ]
        matches.sort(key=lambda match: match.score, reverse=True)
        return matches


@dataclass
class AgentScanner:
    process_source: ProcessSource
    definitions: Sequence[AgentDefinition] = AGENT_DEFINITIONS
    matcher: AgentMatcher = field(default_factory=AgentMatcher)

    def scan(self) -> list[AgentCandidate]:
        processes = list(self.process_source.snapshot())
        process_by_pid = {process.pid: process for process in processes}
        candidates: list[AgentCandidate] = []

        for definition in self.definitions:
            matches = self.matcher.matches_for_definition(definition, processes)
            if not matches:
                continue
            sessions = self._build_sessions(definition, matches, process_by_pid)
            if not sessions:
                continue
            confidence = min(100, sum(match.score for match in matches[:3]))
            matched_by = tuple(
                sorted({reason for match in matches for reason in match.reasons})
            )
            candidates.append(
                AgentCandidate(
                    definition=definition,
                    sessions=tuple(sessions),
                    matched_by=matched_by,
                    confidence=confidence,
                )
            )

        candidates.sort(key=lambda candidate: candidate.confidence, reverse=True)
        return candidates

    def _build_sessions(
        self,
        definition: AgentDefinition,
        matches: Sequence[ProcessMatch],
        process_by_pid: dict[int, ProcessInfo],
    ) -> list[AgentSessionCandidate]:
        matched_pids = {match.process.pid for match in matches}
        if definition.agent_id in APPLICATION_AGENT_IDS:
            return [self._single_application_session(definition, matches)]

        groups: dict[int, list[ProcessMatch]] = {}

        for match in matches:
            root_pid = self._session_root_pid(
                match.process.pid,
                matched_pids=matched_pids,
                process_by_pid=process_by_pid,
            )
            groups.setdefault(root_pid, []).append(match)

        sessions: list[AgentSessionCandidate] = []
        for root_pid, group_matches in groups.items():
            group_matches.sort(
                key=lambda match: (
                    match.process.pid != root_pid,
                    -(match.process.create_time or 0),
                    match.process.pid,
                )
            )
            confidence = min(100, sum(match.score for match in group_matches[:3]))
            matched_by = tuple(
                sorted({reason for match in group_matches for reason in match.reasons})
            )
            sessions.append(
                AgentSessionCandidate(
                    session_id=f"{definition.agent_id}:{root_pid}",
                    definition=definition,
                    root_pid=root_pid,
                    processes=tuple(match.process for match in group_matches),
                    matched_by=matched_by,
                    confidence=confidence,
                )
            )

        sessions.sort(
            key=lambda session: (
                -(session.root_process.create_time or 0),
                session.root_pid,
            )
        )
        return sessions

    def _single_application_session(
        self,
        definition: AgentDefinition,
        matches: Sequence[ProcessMatch],
    ) -> AgentSessionCandidate:
        root_match = max(
            matches,
            key=lambda match: (
                match.score,
                match.process.name.casefold()
                in {keyword.casefold() for keyword in definition.process_name_keywords},
                -(match.process.create_time or 0),
            ),
        )
        sorted_matches = sorted(
            matches,
            key=lambda match: (
                match.process.pid != root_match.process.pid,
                -(match.process.create_time or 0),
                match.process.pid,
            ),
        )
        return AgentSessionCandidate(
            session_id=f"{definition.agent_id}:{root_match.process.pid}",
            definition=definition,
            root_pid=root_match.process.pid,
            processes=tuple(match.process for match in sorted_matches),
            matched_by=tuple(
                sorted({reason for match in matches for reason in match.reasons})
            ),
            confidence=min(100, sum(match.score for match in matches[:3])),
        )

    def _session_root_pid(
        self,
        pid: int,
        matched_pids: set[int],
        process_by_pid: dict[int, ProcessInfo],
    ) -> int:
        root_pid = pid
        seen: set[int] = set()

        while root_pid not in seen:
            seen.add(root_pid)
            process = process_by_pid.get(root_pid)
            if process is None or process.ppid is None:
                break
            if process.ppid not in matched_pids:
                break
            root_pid = process.ppid

        return root_pid
