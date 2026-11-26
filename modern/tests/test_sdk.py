import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import unittest
from unittest.mock import MagicMock, patch

from azure.ai.contentunderstanding import ContentUnderstandingClient
from azure.core.credentials import AzureKeyCredential
from azure.cosmos import exceptions
import azure.functions as func

import function_app as api


class SdkTest(unittest.TestCase):
    def test_submit_performs_one_binary_post_and_status_one_get(self):
        requests_seen = []
        identifier = '731b54ae-6e91-4a14-81ce-dc076e474f82'
        class Backend(BaseHTTPRequestHandler):
            def do_POST(self):
                requests_seen.append(('POST', self.path, self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(202)
                self.send_header('Operation-Location', endpoint + '/contentunderstanding/analyzerResults/' + identifier + '?api-version=2025-11-01')
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'id': identifier, 'status': 'NotStarted'}).encode())

            def do_GET(self):
                requests_seen.append(('GET', self.path, None))
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"status":"Running"}')

            def log_message(self, *args):
                pass

        with ThreadingHTTPServer(('127.0.0.1', 0), Backend) as server:
            endpoint = 'http://127.0.0.1:' + str(server.server_port)
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                pdf = (Path(__file__).resolve().parents[2] / 'Deployment/Data/samples/test/contoso_set_1/ContosoSafety360-Sample-1.pdf').read_bytes()
                blobs, records = MagicMock(), MagicMock()
                blob = blobs.get_blob_client.return_value
                blob.get_blob_properties.return_value.size = len(pdf)
                blob.get_blob_properties.return_value.etag = 'v1'
                blob.download_blob.return_value.readall.return_value = pdf
                records.read_item.side_effect = exceptions.CosmosResourceNotFoundError()
                event = {'topic': '/store', 'eventType': 'Microsoft.Storage.BlobCreated', 'subject': '/blobServices/default/containers/raw/blobs/test.pdf', 'data': {'eTag': 'v1', 'api': 'PutBlob'}}
                with ContentUnderstandingClient(endpoint, AzureKeyCredential('local-test-only')) as client, patch.object(api, 'clients', return_value=(blobs, client, records)), patch.dict(api.os.environ, {'CU_ENDPOINT': endpoint, 'SOURCE_ACCOUNT_ID': '/store', 'ANALYZER_ID': 'safety'}):
                    result = api.submit(func.HttpRequest('POST', '/api/forms', body=json.dumps(event).encode()))
                    self.assertEqual(json.loads(result.get_body())['operationId'], identifier)
                    self.assertEqual([item[0] for item in requests_seen], ['POST'])
                    self.assertIn(':analyzeBinary?', requests_seen[0][1])
                    self.assertIn('processingLocation=geography', requests_seen[0][1])
                    self.assertEqual(requests_seen[0][2], pdf)
                    self.assertEqual(api.analysis({'operationId': identifier})['status'], 'Running')
                    self.assertEqual([item[0] for item in requests_seen], ['POST', 'GET'])
            finally:
                server.shutdown()
                worker.join()