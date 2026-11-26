import json
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from setup_analyzer import SOURCE, definitions
from review import decide


class SetupReviewTests(unittest.TestCase):
    def test_upstream_fields_and_unknown_pages(self):
        analyzers = dict(definitions('safety', 'gpt-4.1'))
        fields = analyzers['safety-extract']['fieldSchema']['fields']
        self.assertEqual({field['description'] for field in fields.values()},
                         {field['fieldKey'] for field in json.loads(SOURCE.read_text())['fields']})
        self.assertEqual(fields['Equipment']['type'], 'boolean')
        categories = analyzers['safety']['config']['contentCategories']
        self.assertIn(categories['SafetyForm']['analyzerId'], analyzers)
        self.assertTrue(categories['other']['analyzerId'])

    def test_review_is_conditional(self):
        container = MagicMock()
        container.read_item.return_value = {'id': 'doc', 'status': 'needs_review', '_etag': 'v1'}
        with self.assertRaises(ValueError):
            decide(container, 'doc', 'stale', 'approve', 'Checked evidence', 'reviewer')
        container.replace_item.assert_not_called()
        decide(container, 'doc', 'v1', 'reject', 'Unreadable', 'reviewer')
        self.assertEqual(container.replace_item.call_args.args[1]['status'], 'review_rejected')
        self.assertEqual(container.replace_item.call_args.kwargs['etag'], 'v1')