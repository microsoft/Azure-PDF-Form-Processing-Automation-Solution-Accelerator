import unittest

from forms import document, evaluate


class FormsTests(unittest.TestCase):
    def test_only_completed_raw_versions(self):
        event = {'topic': '/storage/account', 'eventType': 'Microsoft.Storage.BlobCreated',
                 'subject': '/blobServices/default/containers/raw/blobs/test%20form.pdf',
                 'data': {'eTag': 'abc', 'api': 'PutBlockList'}}
        identity, name, _ = document(event, '/storage/account')
        self.assertEqual(name, 'test form.pdf')
        self.assertEqual(identity, document(dict(event, id='retry'), '/storage/account')[0])
        event['data']['eTag'] = 'def'
        self.assertNotEqual(identity, document(event, '/storage/account')[0])
        for update in [{'topic': 'other'}, {'subject': '/processed/test.pdf'},
                       {'data': {'api': 'CreateFile', 'eTag': 'abc'}}]:
            with self.assertRaises(ValueError):
                document(dict(event, **update), '/storage/account')

    def test_every_segment_must_pass(self):
        policy = {'Equipment': 0.9}
        good = {'fields': {'Equipment': {'valueBoolean': False, 'confidence': 0.9}}}
        self.assertEqual(evaluate([good], policy), ('accepted', []))
        for field in [{}, {'valueBoolean': True}, {'confidence': 1},
                      {'valueBoolean': True, 'confidence': 0.89}]:
            status, issues = evaluate([good, {'fields': {'Equipment': field}}], policy)
            self.assertEqual((status, issues[0]['segment']), ('needs_review', 2))
        self.assertEqual(evaluate([], policy)[0], 'needs_review')
        with self.assertRaises(ValueError):
            evaluate([good], {})