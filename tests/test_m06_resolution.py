import unittest
from dataclasses import dataclass

from vibecoder.errors import NotFoundError
from vibecoder.resolution import AmbiguousResourceError, ResourceResolver


@dataclass
class Item:
    id: str
    name: str
    natural: str | None = None
    project: str = "p"


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.resolver = ResourceResolver(id_of=lambda x: x.id, name_of=lambda x: x.name,
                                         natural_id_of=lambda x: x.natural)

    def test_id_has_precedence_over_name_collision(self):
        by_id = Item("same", "first")
        by_name = Item("other", "same")
        self.assertIs(self.resolver.resolve("same", [by_name, by_id]), by_id)

    def test_natural_and_unique_casefolded_name(self):
        item = Item("id", "BreakOut", "owner/breakout")
        self.assertIs(self.resolver.resolve("owner/breakout", [item]), item)
        self.assertIs(self.resolver.resolve(" breakout ", [item]), item)

    def test_ambiguous_lists_candidates_and_partial_uuid_is_not_id(self):
        items = [Item("12345678-full-a", "main"), Item("12345678-full-b", "main")]
        with self.assertRaises(AmbiguousResourceError) as caught:
            self.resolver.resolve("main", items)
        self.assertEqual(len(caught.exception.candidates), 2)
        with self.assertRaises(NotFoundError):
            self.resolver.resolve("12345678", items)

    def test_agent_scope_is_explicit_input(self):
        items = [Item("a", "main", project="p1"), Item("b", "main", project="p2")]
        with self.assertRaises(AmbiguousResourceError):
            self.resolver.resolve("main", items)
        self.assertEqual(self.resolver.resolve("main", [x for x in items if x.project == "p1"]).id, "a")

