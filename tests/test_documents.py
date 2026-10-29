from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vibecoder import ConflictError, ValidationError
from vibecoder.documents import DocumentCategory, DocumentResolver, InstructionLoader


class DocumentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.resolver = DocumentResolver(self.workspace)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_context_prefers_vibecoder_then_falls_back_to_doc(self) -> None:
        self.resolver.write("doc/architecture.md", "public")
        found = self.resolver.resolve("architecture.md", DocumentCategory.CONTEXT)
        self.assertEqual(found.content, "public")
        self.resolver.write(".vibecoder/architecture.md", "internal")
        found = self.resolver.resolve("architecture.md", DocumentCategory.CONTEXT)
        self.assertEqual(found.content, "internal")

    def test_categories_are_not_interchangeable_and_absence_is_explicit(self) -> None:
        self.resolver.write("doc/installation.md", "install")
        internal = self.resolver.resolve("installation.md", DocumentCategory.INTERNAL)
        public = self.resolver.resolve("installation.md", DocumentCategory.PUBLIC)
        missing = self.resolver.resolve("missing.md", DocumentCategory.CONTEXT)
        self.assertIsNone(internal.path)
        self.assertEqual(public.content, "install")
        self.assertIsNone(missing.content)

    def test_write_requires_explicit_safe_destination_and_never_falls_back(self) -> None:
        with self.assertRaises(ValidationError):
            self.resolver.write("README.md", "not allowed")
        self.resolver.write("doc/design.md", "human")
        with self.assertRaises(ConflictError):
            self.resolver.write("doc/design.md", "overwrite")
        self.assertFalse((self.workspace / ".vibecoder/design.md").exists())

    def test_paths_and_symlinks_cannot_escape(self) -> None:
        with self.assertRaises(ValidationError):
            self.resolver.resolve("../secret", DocumentCategory.CONTEXT)
        outside = self.workspace.parent / "outside-docs"
        outside.mkdir(exist_ok=True)
        (self.workspace / ".vibecoder").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValidationError):
            self.resolver.write(".vibecoder/project.md", "escape")

    def test_instruction_hierarchy_hashes_and_no_root_duplication(self) -> None:
        self.resolver.write("AGENTS.md", "# Global\n\n- Rule: language = python\n")
        self.resolver.write(
            ".vibecoder/agents/main/AGENTS.md",
            "# Main\n\nAsk before architecture changes.\n",
        )
        context = InstructionLoader(self.workspace).load("main", "Current task")
        self.assertNotIn("# Global", context.text)
        self.assertIn("# Main", context.text)
        self.assertIn("Current task", context.text)
        self.assertEqual(set(context.versions), {
            "AGENTS.md", ".vibecoder/agents/main/AGENTS.md"
        })

    def test_instruction_specific_is_optional_and_agents_are_isolated(self) -> None:
        self.resolver.write("AGENTS.md", "# Global")
        self.resolver.write(".vibecoder/agents/main/AGENTS.md", "main only")
        other = InstructionLoader(self.workspace).load("reviewer", "task")
        self.assertNotIn("main only", other.text)
        self.assertEqual(set(other.versions), {"AGENTS.md"})

    def test_explicit_rule_conflict_blocks_context(self) -> None:
        self.resolver.write("AGENTS.md", "- Rule: network = denied")
        self.resolver.write(
            ".vibecoder/agents/main/AGENTS.md", "- Rule: network = allowed"
        )
        with self.assertRaises(ConflictError):
            InstructionLoader(self.workspace).load("main", "task")


if __name__ == "__main__":
    unittest.main()
