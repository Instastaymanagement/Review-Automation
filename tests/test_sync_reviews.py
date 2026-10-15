import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import sync_reviews as s  # noqa: E402

COLS = {t: f"col_{t}" for t in s.REQUIRED_COLUMNS}
COLS["Property Link"] = "col_link"


def review(overall=5, categories=None, **extra):
    categories = categories or {}
    detailed = [{"type": t, "rating": categories.get(t, 5), "comment": None}
                for t in ("value", "cleanliness", "communication", "location", "checkin", "accuracy")]
    detailed += [{"type": "facilities", "rating": 0, "comment": None}]
    r = {
        "id": "rev-1",
        "platform": "airbnb",
        "public": {"rating": overall, "review": "Great stay.", "response": None},
        "private": {"feedback": None, "detailed_ratings": detailed},
        "reviewed_at": "2026-10-01T20:55:13+00:00",
        "responded_at": None,
        "guest": {"first_name": "Sue", "last_name": "Reynolds"},
    }
    r.update(extra)
    return r


class FlagTests(unittest.TestCase):
    def test_perfect_review_not_flagged(self):
        self.assertFalse(s.needs_flag(review()))

    def test_low_overall_flagged(self):
        self.assertTrue(s.needs_flag(review(overall=4)))

    def test_low_category_flagged_even_with_5_overall(self):
        self.assertTrue(s.needs_flag(review(categories={"cleanliness": 4})))

    def test_unrated_categories_ignored(self):
        # facilities/staff/services come back as 0 when not rated
        self.assertEqual(set(s.category_ratings(review())), set(s.CATEGORY_COLUMNS.values()))


class ColumnValueTests(unittest.TestCase):
    def test_full_mapping(self):
        r = review(overall=3, categories={"cleanliness": 1}, responded_at="2026-10-02T00:00:00+00:00")
        r["private"]["feedback"] = "Hair in shower"
        r["private"]["detailed_ratings"][1]["comment"] = "dirty blankets"
        v = s.build_column_values(r, "22 Euston", "13292667359", COLS)
        self.assertEqual(v["col_Overall Rating"], "3")
        self.assertEqual(v["col_Cleanliness"], "1")
        self.assertEqual(v["col_Value"], "5")
        self.assertEqual(v["col_Review Date"], {"date": "2026-10-01"})
        self.assertEqual(v["col_Flagged for Action"], {"checked": "true"})
        self.assertEqual(v["col_Host Responded"], {"checked": "true"})
        self.assertEqual(v["col_link"], {"item_ids": [13292667359]})
        self.assertEqual(v["col_Review ID"], "rev-1")
        text = v["col_Review Text"]["text"]
        self.assertIn("Great stay.", text)
        self.assertIn("Private feedback: Hair in shower", text)
        self.assertIn("Cleanliness (1★): dirty blankets", text)

    def test_no_link_when_property_unmapped(self):
        v = s.build_column_values(review(), "New place", None, COLS)
        self.assertNotIn("col_link", v)
        self.assertNotIn("col_Flagged for Action", v)

    def test_long_text_truncated(self):
        r = review()
        r["public"]["review"] = "x" * 5000
        self.assertEqual(len(s.review_text(r)), s.LONG_TEXT_LIMIT)

    def test_reviewer_name(self):
        self.assertEqual(s.reviewer_name(review()), "Sue Reynolds")
        self.assertEqual(s.reviewer_name(review(guest=None)), "Unknown guest")


if __name__ == "__main__":
    unittest.main()
