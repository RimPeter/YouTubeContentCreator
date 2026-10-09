from types import SimpleNamespace
from django.test import SimpleTestCase

from analysis.services.topic_groups import group_segment_rows


class TopicGroupTests(SimpleTestCase):
    def row(self, pk, text, labels=None):
        return {"segment": SimpleNamespace(pk=pk, order=pk, source_text=text,
                                           topic_labels=labels or ["deterministic-fallback"]),
                "selection": None}

    def test_shared_vocabulary_groups_non_adjacent_sections_and_survives_sorting(self):
        rows = [self.row(1, "solar energy panels electricity"),
                self.row(2, "bread baking flour dough"),
                self.row(3, "solar panels generate electricity"),
                self.row(4, "bread dough flour baking")]
        groups = group_segment_rows(rows)
        membership = lambda groups: {frozenset(row["segment"].pk for row in group["rows"]) for group in groups}
        self.assertEqual(membership(groups), {frozenset([1, 3]), frozenset([2, 4])})
        self.assertEqual(membership(group_segment_rows(list(reversed(rows)))), membership(groups))

    def test_empty_and_unrelated_text_are_not_forced_into_a_topic(self):
        self.assertEqual(group_segment_rows([]), [])
        rows = [self.row(1, ""), self.row(2, "bread flour"), self.row(3, "solar electricity")]
        self.assertEqual(len(group_segment_rows(rows)), 3)

    def test_shared_labels_ignore_case_and_whitespace(self):
        rows = [self.row(1, "", ["Specific detail", " Energy "]),
                self.row(2, "", ["Another detail", "energy"])]
        groups = group_segment_rows(rows)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["title"], "Energy")
        self.assertFalse(groups[0]["suggested"])

    def test_completion_counts_selections_and_exclusions_but_not_removed_sections(self):
        rows = [self.row(i, "", ["Shared topic"]) for i in range(1, 5)]
        rows[0]["selection"] = object()
        rows[0]["review"] = SimpleNamespace(decision="excluded")
        rows[1]["review"] = SimpleNamespace(decision="excluded")
        rows[2]["review"] = SimpleNamespace(decision="removed")
        group = group_segment_rows(rows)[0]
        self.assertEqual(group["selected_count"], 1)
        self.assertEqual(group["excluded_count"], 1)
        self.assertEqual(group["completed_count"], 2)
        self.assertEqual(group["completion_percent"], 50)
        rows[0]["selection"] = None
        rows[0]["review"] = SimpleNamespace(decision="removed")
        self.assertEqual(group_segment_rows(rows)[0]["completion_percent"], 25)
        for row in rows:
            row["review"] = SimpleNamespace(decision="excluded")
        self.assertEqual(group_segment_rows(rows)[0]["completion_percent"], 100)
