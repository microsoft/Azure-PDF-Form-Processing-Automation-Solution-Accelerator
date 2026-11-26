import json
import os
import re
from functools import lru_cache
from urllib.parse import urlsplit
from uuid import UUID

import azure.functions as func
from azure.ai.contentunderstanding import ContentUnderstandingClient
from azure.core import MatchConditions
from azure.core.exceptions import ResourceExistsError
from azure.core.rest import HttpRequest
from azure.cosmos import CosmosClient, exceptions
from azure.identity import ManagedIdentityCredential
from azure.storage.blob import BlobServiceClient

from forms import document, evaluate

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)
MAX_BYTES = 16 * 1024 * 1024
API_VERSION = '2025-11-01'


@lru_cache(maxsize=1)
def clients():
    credential = ManagedIdentityCredential()
    blobs = BlobServiceClient(os.environ['BLOB_ENDPOINT'], credential)
    analyzer = ContentUnderstandingClient(os.environ['CU_ENDPOINT'], credential, api_version=API_VERSION)
    records = CosmosClient(os.environ['COSMOS_ENDPOINT'], credential).get_database_client('forms').get_container_client('records')
    return blobs, analyzer, records


def reply(body, status=200):
    return func.HttpResponse(json.dumps(body), mimetype='application/json', status_code=status)


def view(record):
    return {'id': record['id'], 'operationId': record['operationId'], 'status': record['status'],
            'statusPath': '/api/forms/' + record['id']}


def read_record(key):
    if not re.fullmatch('[a-f0-9]{64}', key):
        raise ValueError('Invalid document identifier')
    return clients()[2].read_item(key, partition_key=key)


@app.route(route='forms', methods=['POST'])
def submit(req: func.HttpRequest) -> func.HttpResponse:
    try:
        key, name, etag = document(req.get_json(), os.environ['SOURCE_ACCOUNT_ID'])
    except (AttributeError, TypeError, ValueError):
        return reply({'error': 'Invalid upload event'}, 400)
    blobs, analyzer, records = clients()
    try:
        return reply(view(read_record(key)), 202)
    except exceptions.CosmosResourceNotFoundError:
        pass
    blob = blobs.get_blob_client('raw', name)
    props = blob.get_blob_properties()
    if props.etag.strip('"') != etag:
        return reply({'error': 'Upload was superseded'}, 409)
    if not 0 < props.size <= MAX_BYTES:
        return reply({'error': 'PDF exceeds the starter size limit or is empty'}, 413)
    pdf = blob.download_blob(etag=props.etag, match_condition=MatchConditions.IfNotModified).readall()
    if not pdf.startswith(b'%PDF-'):
        return reply({'error': 'Expected PDF content'}, 415)
    location = []
    analyzer.begin_analyze_binary(os.environ['ANALYZER_ID'], pdf, content_type='application/pdf',
                                  processing_location='geography', polling=False,
                                  raw_response_hook=lambda response: location.append(response.http_response.headers['Operation-Location']))
    operation = str(UUID(urlsplit(location[-1]).path.rsplit('/', 1)[-1]))
    record = {'id': key, 'name': name, 'etag': etag, 'operationId': operation, 'status': 'submitted'}
    try:
        records.create_item(record)
    except exceptions.CosmosResourceExistsError:
        record = read_record(key)
    return reply(view(record), 202)


def analysis(record):
    operation = str(UUID(record['operationId']))
    endpoint = os.environ['CU_ENDPOINT'].rstrip('/')
    response = clients()[1].send_request(HttpRequest('GET', f'{endpoint}/contentunderstanding/analyzerResults/{operation}?api-version={API_VERSION}'))
    response.raise_for_status()
    return response.json()


@app.route(route='forms/{key}', methods=['GET'])
def status(req: func.HttpRequest) -> func.HttpResponse:
    record = read_record(req.route_params['key'])
    if record['status'] != 'submitted':
        return reply(view(record))
    return reply(dict(view(record), status=analysis(record)['status'].lower()))


@app.route(route='forms/{key}/validate', methods=['POST'])
def validate(req: func.HttpRequest) -> func.HttpResponse:
    record = read_record(req.route_params['key'])
    if record['status'] != 'submitted':
        return reply(view(record))
    result = analysis(record)
    if result['status'].lower() != 'succeeded':
        return reply({'error': 'Analysis is not complete'}, 409)
    contents = result['result'].get('contents', [])
    state, issues = evaluate(contents, json.loads(os.environ['FIELD_THRESHOLDS']))
    blobs, _, records = clients()
    path = record['id'] + '.json'
    try:
        blobs.get_blob_client('processed', path).upload_blob(json.dumps(result['result']), overwrite=False)
    except ResourceExistsError:
        pass
    projections = [{key: next((value for attr, value in field.items() if attr.startswith('value')), None)
                    for key, field in (segment.get('fields') or {}).items() if isinstance(field, dict)} for segment in contents]
    updated = dict(record, status=state, resultPath=path, issues=issues[:100], issueCount=len(issues))
    if len(json.dumps(projections).encode()) < 512 * 1024:
        updated['documents'] = projections
    try:
        records.replace_item(record['id'], updated, etag=record['_etag'], match_condition=MatchConditions.IfNotModified)
    except exceptions.CosmosAccessConditionFailedError:
        updated = read_record(record['id'])
    return reply(view(updated))