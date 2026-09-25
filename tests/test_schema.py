import unittest

from multiagent_workflow.schema import ManagerPlan, ReviewResult


class SchemaTests(unittest.TestCase):
    def test_manager_plan_requires_dependencies_to_point_backward(self):
        with self.assertRaises(ValueError):
            ManagerPlan.from_dict(
                {
                    "summary": "plan",
                    "tasks": [
                        {
                            "id": "T01",
                            "title": "first",
                            "instruction": "do first",
                            "acceptance_criteria": ["done"],
                            "dependencies": ["T02"],
                            "suggested_files": [],
                            "suggested_tests": [],
                        }
                    ],
                }
            )

    def test_review_result(self):
        review = ReviewResult.from_dict(
            {
                "decision": "PASS",
                "summary": "ok",
                "problems": [],
                "required_changes": [],
            }
        )
        self.assertEqual(review.decision, "PASS")


if __name__ == "__main__":
    unittest.main()
