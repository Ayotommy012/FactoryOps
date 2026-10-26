"""Meterless World Model conformance verification suite.

Direct implementation of the 8 canonical Meterless invariants:
- sid-001: diacritics and NBSP normalize identically
- sid-002: distinct names stay distinct (no over-normalization)
- band-001: merge bands at the boundaries (0.85 auto, 0.84 review, 0.82 review, 0.81 new)
- prov-001: writes without provenance are rejected
- idem-001: ingest idempotency: same batch twice produces empty diff (changed=False)
- idem-002: mutated batch changes the canonical shape (changed=True)
- bitemp-001: bitemporal edges: superseding edge closes old one; point-in-time queries respect validity
- audit-001: every mutation writes an audit entry in strictly increasing order
"""

from __future__ import annotations

import sys
from typing import Callable, List, Tuple

from app.world_model.engine import WorldModel
from app.world_model.resolve import decide
from app.world_model.stable_ids import entity_id_from_name, normalize_name
from app.world_model.types import IngestEntityMention, IngestObservation, Provenance

CaseResult = Tuple[str, str, str, str]  # id, description, status (PASS/FAIL), detail


def run_conformance_checks() -> List[CaseResult]:
    results: List[CaseResult] = []

    def record(id_: str, desc: str, fn: Callable[[], None]) -> None:
        try:
            fn()
            results.append((id_, desc, "PASS", ""))
        except Exception as err:
            results.append((id_, desc, "FAIL", str(err)))

    # sid-001
    def check_sid_001() -> None:
        # Non-breaking space \u00a0 and accented characters
        accented = "José\u00a0\u00a0García"
        plain = "Jose Garcia"
        assert normalize_name(accented) == normalize_name(plain), (
            f"accented + NBSP name '{accented}' must normalize to '{plain}', got '{normalize_name(accented)}'"
        )
        assert entity_id_from_name("person", "José García") == entity_id_from_name(
            "person", "jose garcia"
        ), "same normalized name must produce the same id"

    record(
        "sid-001",
        "stable ids: diacritics and NBSP normalize identically (NFKD + strip marks)",
        check_sid_001,
    )

    # sid-002
    def check_sid_002() -> None:
        assert entity_id_from_name("org", "Acme Corp") != entity_id_from_name(
            "org", "Acme Corporation"
        ), "different names must produce different ids"
        assert entity_id_from_name("person", "x") != entity_id_from_name(
            "org", "x"
        ), "type participates in the key"

    record(
        "sid-002",
        "stable ids: distinct names stay distinct (no over-normalization)",
        check_sid_002,
    )

    # band-001
    def check_band_001() -> None:
        assert decide(0.85) == "auto-merge", "0.85 exactly is auto-merge (>= reading)"
        assert decide(0.84) == "review", "0.84 queues for review"
        assert decide(0.82) == "review", "0.82 exactly queues"
        assert decide(0.81) == "new", "0.81 is a new entity"

    record(
        "band-001",
        "merge bands at the boundaries: 0.85 auto, 0.84 review, 0.82 review, 0.81 new",
        check_band_001,
    )

    # prov-001
    def check_prov_001() -> None:
        world = WorldModel(namespace="conf")
        rejected = False
        try:
            world.upsert_entity(type_="person", name="No Provenance", source=None)  # type: ignore
        except Exception:
            rejected = True
        assert rejected, "no provenance, no write"

    record("prov-001", "writes without provenance are rejected", check_prov_001)

    # Sample observations for idempotency checks
    sample_obs = [
        IngestObservation(
            external_id="a1",
            title="Fed holds rates",
            at="2026-05-17T14:00:00.000Z",
            source=Provenance(kind="rss", at="2026-05-17T14:00:00.000Z"),
            entities=[IngestEntityMention(type="person", name="Jerome Powell")],
        ),
        IngestObservation(
            external_id="a2",
            title="Powell signals patience",
            at="2026-05-17T16:30:00.000Z",
            source=Provenance(kind="rss", at="2026-05-17T16:30:00.000Z"),
            entities=[IngestEntityMention(type="person", name="Jerome Powell")],
        ),
    ]

    # idem-001
    def check_idem_001() -> None:
        world = WorldModel(namespace="conf")
        world.ingest(sample_obs)
        shape1 = world.state_shape_canonical()
        second = world.ingest(sample_obs)
        assert second.changed is False, "second run must report changed=false"
        assert (
            world.state_shape_canonical() == shape1
        ), "canonical state must be identical after re-ingest"

    record(
        "idem-001",
        "ingest idempotency: same batch twice produces an empty diff",
        check_idem_001,
    )

    # idem-002
    def check_idem_002() -> None:
        world = WorldModel(namespace="conf")
        world.ingest(sample_obs)
        shape1 = world.state_shape_canonical()
        mutated = [
            IngestObservation(
                external_id=sample_obs[0].external_id,
                title=sample_obs[0].title + " (edited)",
                at=sample_obs[0].at,
                source=sample_obs[0].source,
                entities=sample_obs[0].entities,
            ),
            sample_obs[1],
        ]
        run = world.ingest(mutated)
        assert run.changed is True, "an edited observation must report changed=true"
        assert (
            world.state_shape_canonical() != shape1
        ), "canonical state must differ after a real edit"

    record(
        "idem-002",
        "the idempotency check can fail: a mutated batch changes the shape",
        check_idem_002,
    )

    # bitemp-001
    def check_bitemp_001() -> None:
        world = WorldModel(namespace="conf")

        def at_src(s: str) -> Provenance:
            return Provenance(kind="manual", at=s)

        world.upsert_entity(
            type_="player",
            external_key={"system": "g", "id": "p1"},
            attrs={},
            source=at_src("2087-01-01T00:00:00.000Z"),
        )
        world.upsert_entity(
            type_="team",
            external_key={"system": "g", "id": "a"},
            attrs={},
            source=at_src("2087-01-01T00:00:00.000Z"),
        )
        world.upsert_entity(
            type_="team",
            external_key={"system": "g", "id": "b"},
            attrs={},
            source=at_src("2088-03-01T00:00:00.000Z"),
        )

        world.relate(
            from_="player:g:p1",
            type="plays-for",
            to="team:g:a",
            valid_from="2087-01-01T00:00:00.000Z",
            source=at_src("2087-01-01T00:00:00.000Z"),
        )
        world.relate(
            from_="player:g:p1",
            type="plays-for",
            to="team:g:b",
            valid_from="2088-03-01T00:00:00.000Z",
            source=at_src("2088-03-01T00:00:00.000Z"),
        )

        graph = world.view("graph")
        now = graph.neighbors_of("player:g:p1", via="plays-for")
        assert len(now) == 1 and now[0].id == "team:g:b", (
            f"open edge should point at team b; got {[e.id for e in now]}"
        )

        then = graph.neighbors_of(
            "player:g:p1", via="plays-for", at="2087-06-01T00:00:00.000Z"
        )
        assert len(then) == 1 and then[0].id == "team:g:a", (
            f"2087 query should return team a; got {[e.id for e in then]}"
        )

    record(
        "bitemp-001",
        "bitemporal edges: a superseding edge closes the old one; point-in-time queries respect validity",
        check_bitemp_001,
    )

    # audit-001
    def check_audit_001() -> None:
        world = WorldModel(namespace="conf")
        world.upsert_entity(
            type_="x",
            name="Audit One",
            source=Provenance(kind="manual", at="2026-01-01T00:00:00.000Z"),
        )
        world.upsert_entity(
            type_="x",
            name="Audit Two",
            source=Provenance(kind="manual", at="2026-01-02T00:00:00.000Z"),
        )
        audit = world.audit()
        assert len(audit) >= 2, f"expected >= 2 audit entries, got {len(audit)}"
        for i in range(1, len(audit)):
            assert (
                audit[i].seq > audit[i - 1].seq
            ), "audit seq must be strictly increasing"

    record(
        "audit-001",
        "every mutation writes an audit entry in order",
        check_audit_001,
    )

    return results


def main() -> None:
    results = run_conformance_checks()
    passed = [r for r in results if r[2] == "PASS"]
    print("\nWorld Model conformance: Python native implementation\n")
    for id_, desc, status, detail in results:
        msg = f"  {status} {id_} {desc}"
        if detail:
            msg += f"\n       {detail}"
        print(msg)
    print(f"\n  {len(passed)}/{len(results)} checks passed\n")
    if len(passed) != len(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
