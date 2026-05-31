from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .definitions import AGENT_DEFINITIONS
from .models import AgentCandidate, AgentDefinition, ProcessInfo
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
        candidates: list[AgentCandidate] = []

        for definition in self.definitions:
            matches = self.matcher.matches_for_definition(definition, processes)
            if not matches:
                continue
            confidence = min(100, sum(match.score for match in matches[:3]))
            matched_by = tuple(
                sorted({reason for match in matches for reason in match.reasons})
            )
            candidates.append(
                AgentCandidate(
                    definition=definition,
                    processes=tuple(match.process for match in matches),
                    matched_by=matched_by,
                    confidence=confidence,
                )
            )

        candidates.sort(key=lambda candidate: candidate.confidence, reverse=True)
        return candidates
