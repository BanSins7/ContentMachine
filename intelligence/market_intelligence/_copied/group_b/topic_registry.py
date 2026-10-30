"""GB-MOD-20: immutable read-only SQL005 B8 taxonomy registry snapshot.

A registry digest proves consistency, not operator authorization. Categories,
topics and subtopics are SQL005-owned. No invented slug, implicit merge, new
DB node or cross-parent resolution. An external actor must approve changes.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
import unicodedata

from .contracts import canonical_hash
from .errors import GroupBError

VERSION = "group-b-topic-registry/v1"
_SLUG = re.compile(r"[a-z][a-z0-9_]{0,119}\Z")
LEVELS = ("category", "topic", "subtopic")


class TopicRegistryError(GroupBError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__("b8_registry_unavailable", "b8_registry: " + reason)


def _deny(code: str):
    raise TopicRegistryError(code)


def normalize_label(value: str) -> str:
    """Case/Unicode/whitespace only: never synonym guessing or slug creation."""
    if (type(value) is not str or not 1 <= len(value) <= 160 or
        any(unicodedata.category(c)[0] == "C" and not c.isspace()
            for c in value)):
        _deny("topic_label_invalid")
    text = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    if not text or len(text) > 160:
        _deny("topic_label_invalid")
    return text


@dataclass(frozen=True, slots=True)
class TopicNode:
    id: int
    slug: str
    name: str
    parent_id: int | None
    merged_into_id: int | None

    def wire(self) -> dict:
        return {"id": self.id, "slug": self.slug, "name": self.name,
                "parent_id": self.parent_id, "merged_into_id": self.merged_into_id}


@dataclass(frozen=True, slots=True)
class TaxonomySnapshot:
    categories: tuple[TopicNode, ...]
    topics: tuple[TopicNode, ...]
    subtopics: tuple[TopicNode, ...]
    snapshot_sha256: str

    def payload(self) -> dict:
        return {"schema_version": VERSION,
                "categories": [v.wire() for v in self.categories],
                "topics": [v.wire() for v in self.topics],
                "subtopics": [v.wire() for v in self.subtopics]}

    def verify_digest(self) -> bool:
        try:
            return canonical_hash(self.payload()) == self.snapshot_sha256
        except (ValueError, TypeError):
            return False

    def _nodes(self, level):
        if level == "category":
            return self.categories
        if level == "topic":
            return self.topics
        if level == "subtopic":
            return self.subtopics
        _deny("taxonomy_level_invalid")

    def resolve(self, level: str, label: str, parent_id: int | None = None):
        """Return (approved canonical ID, merged alias used) or (None, False).

        Missing labels yield None for explicit human-review proposal.
        Ambiguous identical names across distinct canonical targets or
        parent-changing merges are hard failures, not random first matches.
        """
        if not self.verify_digest():
            _deny("registry_snapshot_digest_mismatch")
        if level not in LEVELS:
            _deny("taxonomy_level_invalid")
        if level == "category" and parent_id is not None:
            _deny("category_parent_not_allowed")
        if level != "category" and (type(parent_id) is not int or parent_id <= 0):
            _deny("taxonomy_parent_required")
        wanted = normalize_label(label)
        rows = self._nodes(level)
        by_id = {x.id: x for x in rows}
        candidates = [x for x in rows if x.parent_id == parent_id and
                      wanted in (normalize_label(x.slug), normalize_label(x.name))]
        if not candidates:
            return None, False
        targets = []
        for candidate in candidates:
            visited = set()
            current = candidate
            while current.merged_into_id is not None:
                if current.id in visited:
                    _deny("registry_merge_cycle")
                visited.add(current.id)
                target = by_id.get(current.merged_into_id)
                if target is None or target.parent_id != current.parent_id:
                    _deny("registry_merge_cross_parent_or_missing")
                current = target
            targets.append((current.id, current.id != candidate.id))
        if len({target_id for target_id, _ in targets}) != 1:
            _deny("taxonomy_alias_ambiguous")
        return targets[0][0], any(alias for _, alias in targets)


def make_topic_registry(*, categories, topics, subtopics) -> TaxonomySnapshot:
    """Strict deterministic builder for trusted DB reads or synthetic tests.

    Creating this value DOES NOT authenticate its provenance. Host admission
    must reload and compare the same snapshot within the saving transaction.
    """
    groups = []
    for level, supplied in zip(LEVELS, (categories, topics, subtopics)):
        if type(supplied) not in (tuple, list) or len(supplied) > 10000:
            _deny("registry_node_count_invalid")
        nodes = []
        for node in supplied:
            if (not isinstance(node, TopicNode) or
                type(node.id) is not int or node.id <= 0 or
                type(node.slug) is not str or not _SLUG.fullmatch(node.slug) or
                type(node.name) is not str or not node.name.strip() or
                len(node.name) > 160 or
                type(node.merged_into_id) not in (int, type(None)) or
                (node.merged_into_id is not None and
                 (node.merged_into_id <= 0 or node.merged_into_id == node.id))):
                _deny("registry_node_shape_invalid")
            normalize_label(node.name)
            if level == "category":
                if node.parent_id is not None:
                    _deny("category_parent_not_null")
            elif type(node.parent_id) is not int or node.parent_id <= 0:
                _deny("registry_parent_invalid")
            nodes.append(node)
        nodes.sort(key=lambda v: v.id)
        if len({x.id for x in nodes}) != len(nodes):
            _deny("registry_duplicate_id")
        if len({(x.parent_id, x.slug) for x in nodes}) != len(nodes):
            _deny("registry_duplicate_slug")
        groups.append(tuple(nodes))
    categories_, topics_, subtopics_ = groups
    for level, nodes, parents in (
        ("topic", topics_, {x.id for x in categories_}),
        ("subtopic", subtopics_, {x.id for x in topics_}),
    ):
        if any(x.parent_id not in parents for x in nodes):
            _deny("registry_parent_missing")
    for nodes in groups:
        indexed = {x.id: x for x in nodes}
        for node in nodes:
            seen = set()
            current = node
            while current.merged_into_id is not None:
                if current.id in seen:
                    _deny("registry_merge_cycle")
                seen.add(current.id)
                successor = indexed.get(current.merged_into_id)
                if successor is None or successor.parent_id != current.parent_id:
                    _deny("registry_merge_cross_parent_or_missing")
                current = successor
    draft = TaxonomySnapshot(categories_, topics_, subtopics_, "")
    return TaxonomySnapshot(categories_, topics_, subtopics_,
                            canonical_hash(draft.payload()))


def read_topic_registry(conn) -> TaxonomySnapshot:
    """SQL005 read-only snapshot; caller owns authorization/transaction.

    Do not treat cached snapshots as a security grant. The read path does
    not inspect Search Planner, Group C or SRE taxonomies.
    """
    queries = (
        "SELECT id,slug,name,merged_into_id FROM content_b_categories ORDER BY id",
        "SELECT id,slug,name,category_id,merged_into_id FROM content_b_topics ORDER BY id",
        "SELECT id,slug,name,topic_id,merged_into_id FROM content_b_subtopics ORDER BY id",
    )
    result = []
    for level, query in enumerate(queries):
        rows = conn.execute(query).fetchall()
        if len(rows) > 10000:
            _deny("registry_node_count_invalid")
        prepared = []
        for row in rows:
            if isinstance(row, Mapping):
                prepared.append(TopicNode(
                    row["id"], row["slug"], row["name"],
                    None if level == 0 else row["category_id" if level == 1 else "topic_id"],
                    row["merged_into_id"]))
            else:
                prepared.append(TopicNode(
                    row[0], row[1], row[2], None if level == 0 else row[3],
                    row[3] if level == 0 else row[4]))
        result.append(prepared)
    return make_topic_registry(categories=result[0],topics=result[1],
                               subtopics=result[2])
