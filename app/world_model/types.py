"""Record shapes and type definitions for the Meterless World Model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Union

ISODate = str
EntityId = str
ContextId = str
RelationshipId = str
FactId = str
EventId = str


@dataclass
class Provenance:
    """Provenance tracking for all writes.

    Must contain 'kind' and 'at'. Record timestamps derive from this.
    """
    kind: str
    at: Union[ISODate, datetime]
    by: Optional[str] = None
    url: Optional[str] = None
    checksum: Optional[str] = None
    run_id: Optional[str] = None

    def to_stored(self) -> StoredProvenance:
        at_str = self.at.isoformat() if isinstance(self.at, datetime) else str(self.at)
        return StoredProvenance(
            kind=self.kind,
            at=at_str,
            by=self.by,
            url=self.url,
            checksum=self.checksum,
            run_id=self.run_id,
        )


@dataclass
class StoredProvenance:
    kind: str
    at: ISODate
    by: Optional[str] = None
    url: Optional[str] = None
    checksum: Optional[str] = None
    run_id: Optional[str] = None


@dataclass
class EntityAggregates:
    mention_count: int = 0
    first_seen_at: Optional[ISODate] = None
    last_seen_at: Optional[ISODate] = None
    linked_story_ids: List[str] = field(default_factory=list)


@dataclass
class Entity:
    type: str
    id: EntityId
    attrs: Dict[str, Any]
    schema_version: int
    source: StoredProvenance
    created_at: ISODate
    updated_at: ISODate
    name: Optional[str] = None
    aliases: List[str] = field(default_factory=list)
    state: Literal["emerging", "active", "archived"] = "active"
    aggregates: Optional[EntityAggregates] = None


@dataclass
class Context:
    type: str
    id: ContextId
    attrs: Dict[str, Any]
    schema_version: int
    source: StoredProvenance
    created_at: ISODate
    updated_at: ISODate
    parent: Optional[ContextId] = None
    started_at: Optional[ISODate] = None
    ended_at: Optional[ISODate] = None


@dataclass
class Relationship:
    id: RelationshipId
    from_: str
    type: str
    to: str
    schema_version: int
    source: StoredProvenance
    created_at: ISODate
    context: Optional[ContextId] = None
    attrs: Optional[Dict[str, Any]] = None
    valid_from: Optional[ISODate] = None
    valid_to: Optional[ISODate] = None
    superseded_by: Optional[RelationshipId] = None


@dataclass
class Fact:
    id: FactId
    about: EntityId
    predicate: str
    value: Any
    confidence: float
    source: StoredProvenance
    asserted_at: ISODate
    context: Optional[ContextId] = None
    superseded_by: Optional[FactId] = None


@dataclass
class Snapshot:
    id: str
    at: ISODate
    entities: List[EntityId]
    schema_version: int
    source: StoredProvenance
    context: Optional[ContextId] = None
    attrs: Optional[Dict[str, Any]] = None


@dataclass
class Tombstone:
    target_id: str
    at: ISODate
    reason: Optional[str] = None
    by: Optional[str] = None


@dataclass
class Alias:
    alias_id: EntityId
    primary_id: EntityId
    merged_at: ISODate
    merged_by: str
    reason: str


EventKind = Literal[
    "upsertEntity",
    "upsertContext",
    "relate",
    "assertFact",
    "snapshot",
    "tombstone",
    "alias",
    "view-rebuild",
]


@dataclass
class WorldEvent:
    id: EventId
    seq: int
    kind: EventKind
    payload: Any
    schema_version: int
    at: ISODate


@dataclass
class AuditEntry:
    seq: int
    at: ISODate
    kind: str
    actor: str
    summary: str
    event_id: Optional[str] = None
    run_id: Optional[str] = None


@dataclass
class MergeProposal:
    id: str
    left_id: str
    right_id: str
    left_name: str
    right_name: str
    similarity: float
    reason: str
    status: Literal["pending", "approved", "rejected"]
    proposed_at: ISODate
    resolved_at: Optional[ISODate] = None
    resolved_by: Optional[str] = None


@dataclass
class IngestEntityMention:
    type: str
    name: str


@dataclass
class IngestObservation:
    external_id: str
    title: str
    at: ISODate
    source: Provenance
    text: Optional[str] = None
    type: Optional[str] = "item"
    entities: List[IngestEntityMention] = field(default_factory=list)


@dataclass
class ObservationRecord:
    id: str
    external_id: str
    entity_id: str
    checksum: str
    at: ISODate
    entity_ids: List[str]
    stale: bool = False
    story_id: Optional[str] = None


@dataclass
class StoryAggregate:
    id: str
    title: str
    member_ids: List[str]
    entity_names: List[str]
    entity_ids: List[str]
    sources: List[str]
    first_at: ISODate
    last_at: ISODate
    milestones: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class ScoreSnapshot:
    story_id: str
    at: ISODate
    velocity1h: float
    velocity24h: float
    velocity7d: float
    trend_direction: int


@dataclass
class IngestReport:
    run_id: str
    processed: int
    skipped_unchanged: int
    clusters: int
    events_appended: int
    changed: bool
    step_timings_ms: List[Dict[str, Any]] = field(default_factory=list)
