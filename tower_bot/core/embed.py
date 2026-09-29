from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    name: str
    value: str
    inline: bool = False


@dataclass(frozen=True)
class EmbedSpec:
    title: str
    description: str = ""
    fields: tuple[Field, ...] = ()
    footer: str = ""
    color: int = 0x5865F2
    url: str | None = None
