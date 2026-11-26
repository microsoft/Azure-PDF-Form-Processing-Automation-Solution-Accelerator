import json
import os
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

import azure.functions as func
from azure.cosmos import exceptions

import function_app as api


class PipelineTest(unittest.TestCase):
    def test_sample_pdf_through_both_routes(self):
        sample = Path(__file__).resolve().parents[2] / 'Deployment/Data/samples/test/contoso_set_1/ContosoSafety360-Sample-1.pdf'
        pdf = sample.read_bytes()
        operation = 'fac92dd6-1895-4639-a89c-7259ccfe7b26'
        for confidence, expected in [(0.96, 'accepted'), (0.32, 'needs_review')]:
            with self.subTest(expected=expected):
                blobs, analyzer, records = MagicMock(), MagicMock(), MagicMock()
                stored = {}
                def read(key, partition_key):
                    if key not in stored:
                        raise exceptions.CosmosResourceNotFoundError()
                    return stored[key].copy()
                records.read_item.side_effect = read
                records.create_item.side_effect = lambda record: stored.update({record['id']: dict(record, _etag='v1')})
                records.replace_item.side_effect = lambda key, record, **kwargs: stored.update({key: record})
                raw, processed = MagicMock(), MagicMock()
                blobs.get_blob_client.side_effect = lambda container, name: {'raw': raw, 'processed': processed}[container]
                raw.get_blob_properties.return_value.size = len(pdf)
                raw.get_blob_properties.return_value.etag = '"etag"'
                raw.download_blob.return_value.readall.return_value = pdf
                def submit_binary(analyzer_id, payload, **kwargs):
                    self.assertEqual(pdf, payload)
                    self.assertIs(kwargs['polling'], False)
                    response = MagicMock()
                    response.http_response.headers = {'Operation-Location': 'https://example.test/operations/' + operation}
                    kwargs['raw_response_hook'](response)
                analyzer.begin_analyze_binary.side_effect = submit_binary
                field = {'valueString': 'Test finding', 'confidence': confidence}
                analyzer.send_request.return_value.json.return_value = {'status': 'Succeeded', 'result': {'contents': [{'fields': {'Findings': field}}]}}
                config = {'SOURCE_ACCOUNT_ID': '/account', 'CU_ENDPOINT': 'https://example.test', 'ANALYZER_ID': 'safety', 'FIELD_THRESHOLDS': '{"Findings":0.9}'}
                with patch.dict(os.environ, config), patch.object(api, 'clients', return_value=(blobs, analyzer, records)):
                    event = {'topic': '/account', 'subject': '/blobServices/default/containers/raw/blobs/test.pdf',
                             'eventType': 'Microsoft.Storage.BlobCreated', 'data': {'eTag': 'etag', 'api': 'FlushWithClose'}}
                    request = func.HttpRequest('POST', '/api/forms', body=json.dumps(event).encode())
                    started = api.submit(request)
                    self.assertEqual(started.status_code, 202)
                    key = json.loads(started.get_body())['id']
                    follow = func.HttpRequest('GET', '/api/forms/' + key, body=b'', route_params={'key': key})
                    self.assertEqual(json.loads(api.status(follow).get_body())['status'], 'succeeded')
                    self.assertEqual(json.loads(api.validate(follow).get_body())['status'], expected)
                    self.assertEqual(stored[key]['documents'][0]['Findings'], 'Test finding')
                    self.assertEqual(json.loads(api.submit(request).get_body())['status'], expected)
                    analyzer.begin_analyze_binary.assert_called_once()
                    processed.upload_blob.assert_called_once()