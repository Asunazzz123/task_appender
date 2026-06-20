import unittest

from taskmgr.model import TaskError
from taskmgr.reminder_rules import format_reminders, parse_reminder_rule, validate_reminders


class ReminderRuleTests(unittest.TestCase):
    def test_parse_and_format_due_rules(self):
        rules = [parse_reminder_rule("1d@09:00"), parse_reminder_rule("0d@09:00")]

        self.assertEqual(
            rules,
            [
                {"days_before": 1, "time": "09:00"},
                {"days_before": 0, "time": "09:00"},
            ],
        )
        self.assertEqual(format_reminders(rules), "提前 1 天 09:00；当天 09:00")

    def test_parse_rejects_invalid_rule(self):
        with self.assertRaisesRegex(TaskError, "Nd@HH:MM"):
            parse_reminder_rule("tomorrow")

    def test_validate_rejects_duplicate_and_missing_due(self):
        duplicate = [
            {"days_before": 1, "time": "09:00"},
            {"days_before": 1, "time": "09:00"},
        ]

        self.assertIn(
            "duplicate",
            " ".join(validate_reminders("short", "2026-07-04", duplicate)),
        )
        self.assertIn(
            "requires due_at",
            " ".join(validate_reminders("short", None, duplicate[:1])),
        )

    def test_daily_task_cannot_add_due_rules(self):
        errors = validate_reminders(
            "daily", None, [{"days_before": 0, "time": "09:00"}]
        )

        self.assertIn("daily task reminders must be empty", errors)


if __name__ == "__main__":
    unittest.main()
