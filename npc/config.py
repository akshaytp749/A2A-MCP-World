"""NPC persona configs: one codebase, persona as data (ADR 5).

Personas live in world/seed/npcs/*.yaml — one or more YAML documents per file.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class NpcConfig:
    id: str
    name: str
    era: str = ""
    model_tier: str = "cheap"
    persona: str = ""
    voice: dict = field(default_factory=dict)
    goals: list[str] = field(default_factory=list)
    secrets: list[str] = field(default_factory=list)
    quirks: list[str] = field(default_factory=list)
    seed_memories: list[dict] = field(default_factory=list)
    attitudes: dict = field(default_factory=dict)
    hard_lines: list[str] = field(default_factory=list)
    grounding_sources: list[str] = field(default_factory=list)

    @property
    def description(self) -> str:
        return f"Fictional AI portrayal of {self.name} ({self.era}) in The Interval."


def load_personas(npcs_dir: str | Path) -> dict[str, NpcConfig]:
    """Parse every YAML document under world/seed/npcs/ into NpcConfig by id."""
    personas: dict[str, NpcConfig] = {}
    known = {f.name for f in NpcConfig.__dataclass_fields__.values()}
    for path in sorted(Path(npcs_dir).glob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if not doc or "id" not in doc:
                continue
            data = {k: v for k, v in doc.items() if k in known}
            cfg = NpcConfig(**data)
            personas[cfg.id] = cfg
    return personas
