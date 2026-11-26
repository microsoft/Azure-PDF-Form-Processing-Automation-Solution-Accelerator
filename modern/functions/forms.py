import hashlib
import math
from urllib.parse import unquote


def document(event, account_id):
    prefix = '/blobServices/default/containers/raw/blobs/'
    subject = event.get('subject', '')
    data = event.get('data', {})
    if (event.get('eventType') != 'Microsoft.Storage.BlobCreated'
            or event.get('topic', '').lower() != account_id.lower()
            or not subject.startswith(prefix)
            or data.get('api') not in {'PutBlob', 'PutBlockList', 'FlushWithClose'}):
        raise ValueError('Expected a completed upload in the configured raw container')
    name = unquote(subject[len(prefix):], errors='strict')
    etag = data.get('eTag', '').strip('"')
    if not name.lower().endswith('.pdf') or not etag:
        raise ValueError('Expected a versioned PDF')
    identity = '\0'.join([account_id.lower(), 'raw', name, etag]).encode()
    return hashlib.sha256(identity).hexdigest(), name, etag


def evaluate(contents, policy):
    if not policy or not all(isinstance(limit, (int, float)) and 0 < limit <= 1
                             for limit in policy.values()):
        raise ValueError('Required field thresholds must be in (0, 1]')
    issues = []
    if not contents:
        issues.append({'reason': 'no_documents'})
    for segment, content in enumerate(contents, 1):
        fields = content.get('fields') or {}
        for key, threshold in policy.items():
            field = fields.get(key) or {}
            confidence = field.get('confidence')
            values = [value for name, value in field.items() if name.startswith('value')]
            if not any(value is not None and value != '' and value != [] and value != {} for value in values):
                reason = 'missing_value'
            elif (type(confidence) not in (int, float) or not math.isfinite(confidence)
                  or not threshold <= confidence <= 1):
                reason = 'confidence'
            else:
                continue
            issues.append({'segment': segment, 'field': key, 'reason': reason})
    return ('needs_review' if issues else 'accepted'), issues