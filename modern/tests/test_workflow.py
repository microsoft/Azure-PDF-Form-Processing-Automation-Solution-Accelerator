import json
from pathlib import Path
import unittest


class WorkflowTest(unittest.TestCase):
    def test_mail_is_allowed_and_stored_before_acknowledgment(self):
        source = Path(__file__).resolve().parents[1] / 'workflows/intake/workflow.json'
        flow = json.loads(source.read_text())['definition']['actions']['For_each_message']['actions']
        check = flow['Allow_sender']
        self.assertIn('TRUSTED_SENDERS', check['expression'])
        self.assertEqual(flow['Mark_handled']['runAfter']['Allow_sender'], ['Succeeded'])
        files = check['actions']['For_each_file']['actions']['PDF']['actions']
        self.assertEqual(files['Create_raw']['inputs']['headers']['If-None-Match'], '*')
        self.assertEqual(files['Confirm_stored']['inputs']['schema']['enum'], [201, 412])
        self.assertEqual(check['actions']['All_attachments_seen']['inputs']['schema']['enum'], [True])

    def test_delivery_and_poll_contracts(self):
        modern = Path(__file__).resolve().parents[1]
        workflow = json.loads((modern / 'workflows/process/workflow.json').read_text())
        handshake = workflow['definition']['actions']['Handshake']
        self.assertIn('validationResponse', handshake['actions']['Confirm']['inputs']['body'])
        actions = handshake['else']['actions']
        self.assertEqual(actions['Submit']['operationOptions'], 'DisableAsyncPattern')
        poll = actions['Poll']
        self.assertLessEqual(poll['limit']['count'], 60)
        self.assertEqual(poll['operationOptions'], 'FailWhenLimitsReached')
        self.assertEqual(set(actions['Polling_failed']['runAfter']['Poll']), {'Failed', 'TimedOut'})
        for action in (actions['Submit'], poll['actions']['Status'], actions['Check_result']['actions']['Validate']):
            self.assertEqual(action['inputs']['authentication']['type'], 'ManagedServiceIdentity')
            self.assertIn("appsetting('FORMS_API')", action['inputs']['uri'])
        self.assertEqual(workflow['kind'], 'Stateful')