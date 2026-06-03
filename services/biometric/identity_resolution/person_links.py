"""
Untyped person-link graph.
==========================
Requirement: "it does not matter to find the relationship between the client and
the patient. just link the 2 or more persons so that the next time the client
comes the system considers as many possibilities of identification as the number
of the relationships + the client himself to load the profile."

So we keep a simple undirected link graph over person references:

    person_ref := "patient:<uuid>" | "customer:<uuid>"

Links are stored once (canonically ordered a<b) and traversed breadth-first so a
recognised customer fans out to EVERY connected patient profile — self + all
linked persons — which the pharmacist can then browse. Relationship type is
optional metadata, never required.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def patient_ref(pid) -> str:
    return f"patient:{pid}"


def customer_ref(cid) -> str:
    return f"customer:{cid}"


def _ordered(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


class PersonLinkGraph:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def link(
        self,
        pharmacy_id: UUID,
        ref_a: str,
        ref_b: str,
        relationship: str | None = None,
        confidence: float = 1.0,
        source: str = "insurance",
    ) -> None:
        """Create an (idempotent) undirected link between two persons."""
        if ref_a == ref_b:
            return
        a, b = _ordered(ref_a, ref_b)
        await self.db.execute(
            text(
                """
                INSERT INTO person_links
                    (pharmacy_id, person_a_ref, person_b_ref, relationship, confidence, source)
                VALUES (:pid, :a, :b, :rel, :conf, :src)
                ON CONFLICT (pharmacy_id, person_a_ref, person_b_ref) DO UPDATE
                    SET confidence = GREATEST(person_links.confidence, EXCLUDED.confidence),
                        relationship = COALESCE(EXCLUDED.relationship, person_links.relationship)
                """
            ),
            {"pid": str(pharmacy_id), "a": a, "b": b,
             "rel": relationship, "conf": confidence, "src": source},
        )

    async def neighbors(self, ref: str) -> list[dict]:
        """Direct links of a single person ref."""
        res = await self.db.execute(
            text(
                """
                SELECT person_a_ref, person_b_ref, relationship, confidence, source
                FROM person_links
                WHERE person_a_ref = :ref OR person_b_ref = :ref
                """
            ),
            {"ref": ref},
        )
        out = []
        for row in res.mappings().all():
            other = row["person_b_ref"] if row["person_a_ref"] == ref else row["person_a_ref"]
            out.append({
                "ref": other,
                "relationship": row["relationship"],
                "confidence": float(row["confidence"]),
                "source": row["source"],
            })
        return out

    async def connected_component(self, ref: str, max_depth: int = 3) -> list[str]:
        """
        BFS over the link graph from `ref`. Returns every reachable person ref
        (including `ref` itself) up to `max_depth` hops — the full candidate set.
        """
        seen = {ref}
        frontier = [ref]
        depth = 0
        while frontier and depth < max_depth:
            res = await self.db.execute(
                text(
                    """
                    SELECT person_a_ref, person_b_ref FROM person_links
                    WHERE person_a_ref = ANY(:refs) OR person_b_ref = ANY(:refs)
                    """
                ),
                {"refs": frontier},
            )
            next_frontier = []
            for row in res.mappings().all():
                for cand in (row["person_a_ref"], row["person_b_ref"]):
                    if cand not in seen:
                        seen.add(cand)
                        next_frontier.append(cand)
            frontier = next_frontier
            depth += 1
        return list(seen)

    @staticmethod
    def patient_ids_from_refs(refs: list[str]) -> list[str]:
        """Extract patient UUIDs from a list of person refs."""
        return [r.split(":", 1)[1] for r in refs if r.startswith("patient:")]
