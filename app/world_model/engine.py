"""World Model engine: event-sourced state fold, single-writer discipline,

bitemporal graph relationships, and canonical serialization.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import threading
from typing import Any, Callable, Dict, List, Optional, Set

from app.world_model.resolve import decide, jaccard, name_similarity
from app.world_model.stable_ids import (
    canonical_json,
    context_id_from_attrs,
    entity_id_from_name,
    external_id,
    fact_id,
    normalize_name,
    relationship_id,
    sha256_hex,
)
from app.world_model.types import (
    AuditEntry,
    Context,
    Entity,
    EventKind,
    Fact,
    IngestObservation,
    IngestReport,
    MergeProposal,
    ObservationRecord,
    Provenance,
    Relationship,
    ScoreSnapshot,
    StoredProvenance,
    StoryAggregate,
    WorldEvent,
)


class FoldedState:
    def __init__(self) -> None:
        self.entities: Dict[str, Entity] = {}
        self.contexts: Dict[str, Context] = {}
        self.relationships: Dict[str, Relationship] = {}
        self.facts: Dict[str, Fact] = {}
        self.aliases: Dict[str, str] = {}  # alias_id -> primary_id


class EventStore:
    def __init__(self) -> None:
        self.events: List[WorldEvent] = []
        self.audit: List[AuditEntry] = []
        self.observations: Dict[str, ObservationRecord] = {}
        self.stories: Dict[str, StoryAggregate] = {}
        self.scores: List[ScoreSnapshot] = []
        self.merge_queue: List[MergeProposal] = []
        self.state = FoldedState()
        self._seq = 0
        self._audit_seq = 0

    def append(self, kind: EventKind, payload: Any, at: str) -> WorldEvent:
        self._seq += 1
        event = WorldEvent(
            id=f"evt_{self._seq}",
            seq=self._seq,
            kind=kind,
            payload=payload,
            schema_version=1,
            at=at,
        )
        self.events.append(event)
        self.fold(event)
        return event

    def fold(self, event: WorldEvent) -> None:
        kind = event.kind
        payload = event.payload

        if kind == "upsertEntity":
            e: Entity = payload
            if e.id in self.state.entities:
                existing = self.state.entities[e.id]
                existing.attrs.update(e.attrs)
                existing.updated_at = e.updated_at
                existing.source = e.source
                if e.name and e.name not in existing.aliases:
                    existing.aliases.append(e.name)
                if e.name:
                    existing.name = e.name
            else:
                self.state.entities[e.id] = copy.deepcopy(e)

        elif kind == "upsertContext":
            c: Context = payload
            if c.id in self.state.contexts:
                existing_ctx = self.state.contexts[c.id]
                existing_ctx.attrs.update(c.attrs)
                existing_ctx.updated_at = c.updated_at
            else:
                self.state.contexts[c.id] = copy.deepcopy(c)

        elif kind == "relate":
            r: Relationship = payload
            # Superseding semantics: a new open edge with same (from, type, context)
            # but different target CLOSES the previous open edge.
            for other in self.state.relationships.values():
                if (
                    other.from_ == r.from_
                    and other.type == r.type
                    and (other.context or "") == (r.context or "")
                    and other.to != r.to
                    and not other.valid_to
                    and not other.superseded_by
                ):
                    other.valid_to = r.valid_from or r.created_at
                    other.superseded_by = r.id

            if r.id not in self.state.relationships:
                self.state.relationships[r.id] = copy.deepcopy(r)

        elif kind == "assertFact":
            f: Fact = payload
            if f.id not in self.state.facts:
                self.state.facts[f.id] = copy.deepcopy(f)

        elif kind == "alias":
            a_payload = payload
            alias_id = a_payload.get("alias_id") if isinstance(a_payload, dict) else a_payload.alias_id
            primary_id = a_payload.get("primary_id") if isinstance(a_payload, dict) else a_payload.primary_id
            self.state.aliases[alias_id] = primary_id

    def resolve_alias(self, id_: str) -> str:
        cur = id_
        seen: Set[str] = set()
        while cur in self.state.aliases and cur not in seen:
            seen.add(cur)
            cur = self.state.aliases[cur]
        return cur

    def log_audit(
        self,
        kind: str,
        actor: str,
        summary: str,
        event_id: Optional[str] = None,
        run_id: Optional[str] = None,
        at: Optional[str] = None,
    ) -> AuditEntry:
        self._audit_seq += 1
        entry = AuditEntry(
            seq=self._audit_seq,
            at=at or datetime.now(timezone.utc).isoformat(),
            kind=kind,
            actor=actor,
            summary=summary,
            event_id=event_id,
            run_id=run_id,
        )
        self.audit.append(entry)
        return entry


class GraphView:
    def __init__(self, store: EventStore) -> None:
        self._store = store

    def entity(self, id_: str) -> Optional[Entity]:
        rid = self._store.resolve_alias(id_)
        return self._store.state.entities.get(rid)

    def neighbors_of(
        self,
        id_: str,
        via: Optional[str] = None,
        in_context: Optional[str] = None,
        at: Optional[str] = None,
    ) -> List[Entity]:
        rid = self._store.resolve_alias(id_)
        out: List[Entity] = []
        for rel in self._store.state.relationships.values():
            if via and rel.type != via:
                continue
            if in_context and rel.context != in_context:
                continue
            if at:
                # Valid at timestamp: valid_from <= at and (valid_to is None or at < valid_to)
                if rel.valid_from and rel.valid_from > at:
                    continue
                if rel.valid_to and at >= rel.valid_to:
                    continue
            else:
                if rel.valid_to:
                    continue  # closed edge excluded from 'now' query

            other = rel.to if rel.from_ == rid else (rel.from_ if rel.to == rid else None)
            if not other:
                continue
            e = self._store.state.entities.get(other)
            if e:
                out.append(e)

        out.sort(key=lambda x: x.id)
        return out

    def incoming(self, id_: str, via: Optional[str] = None) -> List[Entity]:
        rid = self._store.resolve_alias(id_)
        out: List[Entity] = []
        for rel in self._store.state.relationships.values():
            if rel.to != rid:
                continue
            if via and rel.type != via:
                continue
            if rel.valid_to:
                continue
            e = self._store.state.entities.get(rel.from_)
            if e:
                out.append(e)
        out.sort(key=lambda x: x.id)
        return out

    def traverse(
        self,
        start_id: str,
        via: str,
        direction: str = "descendants",
        max_depth: int = 5,
    ) -> List[Entity]:
        out: List[Entity] = []
        visited: Set[str] = set()
        frontier = [self._store.resolve_alias(start_id)]

        for _ in range(max_depth):
            if not frontier:
                break
            next_frontier: List[str] = []
            for cur_id in frontier:
                for rel in self._store.state.relationships.values():
                    if rel.type != via or rel.valid_to:
                        continue
                    target = (
                        (rel.to if rel.from_ == cur_id else None)
                        if direction == "descendants"
                        else (rel.from_ if rel.to == cur_id else None)
                    )
                    if target and target not in visited:
                        visited.add(target)
                        ent = self._store.state.entities.get(target)
                        if ent:
                            out.append(ent)
                        next_frontier.append(target)
            frontier = next_frontier

        return out

    def query(
        self,
        type_: Optional[str] = None,
        where: Optional[Callable[[Entity], bool]] = None,
    ) -> List[Entity]:
        results = [
            e
            for e in self._store.state.entities.values()
            if (not type_ or e.type == type_) and (not where or where(e))
        ]
        results.sort(key=lambda x: x.id)
        return results


class WorldModel:
    """The central World Model facade providing typed event writes, resolution,

    graph views, and single-writer discipline.
    """

    def __init__(
        self,
        namespace: str,
        clock: Optional[Callable[[], datetime]] = None,
        auto_merge_at: float = 0.85,
        review_lower: float = 0.82,
    ) -> None:
        self.namespace = namespace
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.auto_merge_at = auto_merge_at
        self.review_lower = review_lower
        self._store = EventStore()
        self._lock = threading.Lock()

    def _normalize_provenance(self, source: Optional[Provenance]) -> StoredProvenance:
        if not source or not getattr(source, "kind", None) or getattr(source, "at", None) is None:
            raise ValueError(
                "World Model: every write carries provenance (kind + at). No provenance, no write."
            )
        return source.to_stored()

    def upsert_entity(
        self,
        type_: str,
        source: Provenance,
        name: Optional[str] = None,
        external_key: Optional[Dict[str, str]] = None,
        attrs: Optional[Dict[str, Any]] = None,
        state: str = "active",
    ) -> Entity:
        with self._lock:
            stored_source = self._normalize_provenance(source)
            effective_attrs = dict(attrs or {})
            if name and "name" not in effective_attrs:
                effective_attrs["name"] = name
            target_name = name or effective_attrs.get("name")

            if external_key:
                entity_id = external_id(type_, external_key["system"], external_key["id"])
            else:
                if not target_name:
                    raise ValueError("World Model: name or external_key required for entity")
                entity_id = entity_id_from_name(type_, target_name)

            # Resolution pass for name-keyed entities
            if not external_key and target_name:
                for other in list(self._store.state.entities.values()):
                    if other.type != type_ or other.id == entity_id or not other.name:
                        continue
                    sim = name_similarity(target_name, other.name)
                    decision = decide(sim, self.auto_merge_at, self.review_lower)
                    if decision == "auto-merge":
                        self._store.append(
                            "alias",
                            {
                                "alias_id": entity_id,
                                "primary_id": other.id,
                                "merged_at": stored_source.at,
                                "merged_by": "resolver",
                                "reason": f"similarity {sim:.3f}",
                            },
                            stored_source.at,
                        )
                        self._store.log_audit(
                            "auto-merge",
                            "resolver",
                            f"{target_name} -> {other.name} ({sim:.3f})",
                            at=stored_source.at,
                        )
                        return self._fold_entity_upsert(
                            other.id, type_, effective_attrs, stored_source, target_name, state
                        )
                    elif decision == "review":
                        proposal_id = f"merge_{len(self._store.merge_queue) + 1}"
                        already_queued = any(
                            (p.left_id == entity_id and p.right_id == other.id)
                            or (p.left_id == other.id and p.right_id == entity_id)
                            for p in self._store.merge_queue
                        )
                        if not already_queued:
                            proposal = MergeProposal(
                                id=proposal_id,
                                left_id=entity_id,
                                right_id=other.id,
                                left_name=target_name,
                                right_name=other.name,
                                similarity=sim,
                                reason="name similarity in review band [0.82, 0.85)",
                                status="pending",
                                proposed_at=stored_source.at,
                            )
                            self._store.merge_queue.append(proposal)
                            self._store.log_audit(
                                "merge-proposed",
                                "resolver",
                                f"{target_name} ~ {other.name} ({sim:.3f}) queued",
                                at=stored_source.at,
                            )

            return self._fold_entity_upsert(
                entity_id, type_, effective_attrs, stored_source, target_name, state
            )

    def _fold_entity_upsert(
        self,
        id_: str,
        type_: str,
        attrs: Dict[str, Any],
        source: StoredProvenance,
        name: Optional[str],
        state: str,
    ) -> Entity:
        resolved = self._store.resolve_alias(id_)
        entity = Entity(
            type=type_,
            id=resolved,
            attrs=attrs,
            schema_version=1,
            source=source,
            created_at=source.at,
            updated_at=source.at,
            name=name,
            aliases=[name] if name else [],
            state=state,  # type: ignore
        )
        evt = self._store.append("upsertEntity", entity, source.at)
        self._store.log_audit(
            "upsertEntity",
            source.by or "system",
            f"{resolved} ({type_})",
            event_id=evt.id,
            run_id=source.run_id,
            at=source.at,
        )
        return self._store.state.entities[resolved]

    def upsert_context(
        self,
        type_: str,
        source: Provenance,
        attrs: Optional[Dict[str, Any]] = None,
        parent: Optional[str] = None,
        external_key: Optional[Dict[str, str]] = None,
    ) -> Context:
        with self._lock:
            stored_source = self._normalize_provenance(source)
            effective_attrs = dict(attrs or {})
            if external_key:
                cid = external_id(type_, external_key["system"], external_key["id"])
            else:
                cid = context_id_from_attrs(type_, effective_attrs, parent)

            ctx = Context(
                type=type_,
                id=cid,
                attrs=effective_attrs,
                schema_version=1,
                source=stored_source,
                created_at=stored_source.at,
                updated_at=stored_source.at,
                parent=parent,
            )
            evt = self._store.append("upsertContext", ctx, stored_source.at)
            self._store.log_audit(
                "upsertContext",
                stored_source.by or "system",
                f"{cid} ({type_})",
                event_id=evt.id,
                run_id=stored_source.run_id,
                at=stored_source.at,
            )
            return self._store.state.contexts[cid]

    def relate(
        self,
        from_: str,
        to: str,
        type_: str = "",
        type: Optional[str] = None,
        source: Optional[Provenance] = None,
        context: Optional[str] = None,
        attrs: Optional[Dict[str, Any]] = None,
        valid_from: Optional[str] = None,
        valid_to: Optional[str] = None,
        **kwargs: Any,
    ) -> Relationship:
        with self._lock:
            edge_type = type or type_ or kwargs.get("type", "")
            if not edge_type:
                raise ValueError("World Model: relationship type is required")
            edge_source = source or kwargs.get("source")
            stored_source = self._normalize_provenance(edge_source)
            r_from = self._store.resolve_alias(from_)
            r_to = self._store.resolve_alias(to)
            v_from = valid_from or stored_source.at

            rel_id = relationship_id(r_from, edge_type, r_to, context, v_from)
            if rel_id in self._store.state.relationships:
                return self._store.state.relationships[rel_id]

            rel = Relationship(
                id=rel_id,
                from_=r_from,
                type=edge_type,
                to=r_to,
                schema_version=1,
                source=stored_source,
                created_at=stored_source.at,
                context=context,
                attrs=attrs,
                valid_from=v_from,
                valid_to=valid_to,
            )
            evt = self._store.append("relate", rel, stored_source.at)
            self._store.log_audit(
                "relate",
                stored_source.by or "system",
                f"{r_from} -[{edge_type}]-> {r_to}",
                event_id=evt.id,
                run_id=stored_source.run_id,
                at=stored_source.at,
            )
            return self._store.state.relationships[rel_id]

    def assert_fact(
        self,
        about: str,
        predicate: str,
        value: Any,
        source: Provenance,
        confidence: float = 0.5,
        context: Optional[str] = None,
    ) -> Fact:
        with self._lock:
            stored_source = self._normalize_provenance(source)
            r_about = self._store.resolve_alias(about)
            fid = fact_id(r_about, predicate, value, stored_source.at, stored_source.kind, stored_source.url)
            if fid in self._store.state.facts:
                return self._store.state.facts[fid]

            fact = Fact(
                id=fid,
                about=r_about,
                predicate=predicate,
                value=value,
                confidence=confidence,
                source=stored_source,
                asserted_at=stored_source.at,
                context=context,
            )
            evt = self._store.append("assertFact", fact, stored_source.at)

            # Ensure subject entity exists and update attribute
            if r_about not in self._store.state.entities:
                inferred_type = r_about.split(":")[0] if ":" in r_about else "entity"
                self._fold_entity_upsert(
                    r_about,
                    inferred_type,
                    {predicate: value},
                    stored_source,
                    name=r_about,
                    state="emerging",
                )
            else:
                ent = self._store.state.entities[r_about]
                ent.attrs[predicate] = value
                ent.updated_at = stored_source.at

            self._store.log_audit(
                "assertFact",
                stored_source.by or "system",
                f"{r_about}.{predicate} = {canonical_json(value)}",
                event_id=evt.id,
                run_id=stored_source.run_id,
                at=stored_source.at,
            )
            return self._store.state.facts[fid]

    def ingest(self, observations: List[IngestObservation]) -> IngestReport:
        with self._lock:
            obs_ids = [o.external_id for o in observations]
            run_id = f"run_{sha256_hex(canonical_json(obs_ids))[:12]}"
            before = self.state_shape_canonical()
            events_before = len(self._store.events)

            fresh: List[IngestObservation] = []
            skipped: List[IngestObservation] = []

            for obs in observations:
                chk = sha256_hex(
                    canonical_json(
                        {
                            "external_id": obs.external_id,
                            "title": obs.title,
                            "text": obs.text or "",
                            "at": obs.at,
                        }
                    )
                )
                existing = self._store.observations.get(obs.external_id)
                if existing and existing.checksum == chk:
                    skipped.append(obs)
                else:
                    fresh.append(obs)

            for obs in fresh:
                source = self._normalize_provenance(obs.source)
                entity = self._fold_entity_upsert(
                    external_id(obs.type or "item", "ingest", obs.external_id),
                    obs.type or "item",
                    {"title": obs.title, "text": obs.text},
                    source,
                    name=obs.title,
                    state="active",
                )

                entity_ids: List[str] = []
                for mention in obs.entities:
                    mid = entity_id_from_name(mention.type, mention.name)
                    self._fold_entity_upsert(
                        mid,
                        mention.type,
                        {},
                        source,
                        name=mention.name,
                        state="active",
                    )
                    resolved_mid = self._store.resolve_alias(mid)
                    entity_ids.append(resolved_mid)

                    rid = relationship_id(entity.id, "mentions", resolved_mid)
                    if rid not in self._store.state.relationships:
                        rel = Relationship(
                            id=rid,
                            from_=entity.id,
                            type="mentions",
                            to=resolved_mid,
                            schema_version=1,
                            source=source,
                            created_at=source.at,
                            valid_from=source.at,
                        )
                        self._store.append("relate", rel, source.at)

                chk = sha256_hex(
                    canonical_json(
                        {
                            "external_id": obs.external_id,
                            "title": obs.title,
                            "text": obs.text or "",
                            "at": obs.at,
                        }
                    )
                )
                record = ObservationRecord(
                    id=f"obs_{sha256_hex(obs.external_id)[:12]}",
                    external_id=obs.external_id,
                    entity_id=entity.id,
                    checksum=chk,
                    at=obs.at,
                    entity_ids=sorted(entity_ids),
                )
                self._store.observations[obs.external_id] = record

            after = self.state_shape_canonical()
            events_appended = len(self._store.events) - events_before
            changed = before != after

            report = IngestReport(
                run_id=run_id,
                processed=len(fresh),
                skipped_unchanged=len(skipped),
                clusters=1 if fresh else 0,
                events_appended=events_appended,
                changed=changed,
            )
            self._store.log_audit(
                "ingest",
                "pipeline",
                f"run {run_id}: {len(fresh)} processed, {len(skipped)} skipped",
                run_id=run_id,
                at=observations[0].at if observations else None,
            )
            return report

    def view(self, name: str) -> Any:
        if name == "graph":
            return GraphView(self._store)
        raise ValueError(f"World Model: unknown view '{name}'")

    def audit(self) -> List[AuditEntry]:
        return list(self._store.audit)

    def events(self) -> List[WorldEvent]:
        return list(self._store.events)

    def merge_queue(self) -> List[MergeProposal]:
        return list(self._store.merge_queue)

    def state_shape(self) -> Dict[str, Any]:
        return {
            "entities": sorted(
                [
                    {
                        "id": e.id,
                        "type": e.type,
                        "name": e.name,
                        "attrs": e.attrs,
                        "aliases": sorted(e.aliases),
                    }
                    for e in self._store.state.entities.values()
                ],
                key=lambda x: x["id"],
            ),
            "contexts": sorted(
                [
                    {
                        "id": c.id,
                        "type": c.type,
                        "attrs": c.attrs,
                        "parent": c.parent,
                    }
                    for c in self._store.state.contexts.values()
                ],
                key=lambda x: x["id"],
            ),
            "relationships": sorted(
                [
                    {
                        "id": r.id,
                        "from": r.from_,
                        "type": r.type,
                        "to": r.to,
                        "validFrom": r.valid_from,
                        "validTo": r.valid_to,
                        "supersededBy": r.superseded_by,
                    }
                    for r in self._store.state.relationships.values()
                ],
                key=lambda x: x["id"],
            ),
            "facts": sorted(
                [
                    {
                        "id": f.id,
                        "about": f.about,
                        "predicate": f.predicate,
                        "value": f.value,
                    }
                    for f in self._store.state.facts.values()
                ],
                key=lambda x: x["id"],
            ),
            "observations": sorted(
                [
                    {
                        "externalId": o.external_id,
                        "entityId": o.entity_id,
                        "checksum": o.checksum,
                    }
                    for o in self._store.observations.values()
                ],
                key=lambda x: x["externalId"],
            ),
        }

    def state_shape_canonical(self) -> str:
        return canonical_json(self.state_shape())
