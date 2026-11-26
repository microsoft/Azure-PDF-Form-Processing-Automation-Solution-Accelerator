import argparse
import json
from pathlib import Path
import re

from azure.ai.contentunderstanding import ContentUnderstandingClient
from azure.identity import AzureCliCredential


SOURCE = Path(__file__).resolve().parents[1] / 'Deployment/Data/samples/train/contoso_set_1/fields.json'


def definitions(prefix, model):
    fields = {}
    for field in json.loads(SOURCE.read_text(encoding='utf-8-sig'))['fields']:
        label = field['fieldKey']
        name = ''.join(re.findall('[A-Za-z0-9]+', label))
        if not name or name in fields:
            raise ValueError(f'Ambiguous field label: {label}')
        kind = {'selectionMark': 'boolean', 'string': 'string', 'date': 'date'}[field['fieldType']]
        fields[name] = {'type': kind, 'method': 'extract', 'description': label}
    base = {'baseAnalyzerId': 'prebuilt-document', 'models': {'completion': model}}
    extractor = dict(base, config={'estimateFieldSourceAndConfidence': True}, fieldSchema={'fields': fields})
    classifier = dict(base, config={'enableSegment': True, 'omitContent': True, 'contentCategories': {
        'SafetyForm': {'description': 'A Contoso Safety 360 observation form with findings, checkboxes and follow-up actions.', 'analyzerId': prefix + '-extract'},
        'other': {'description': 'Pages that are not safety observation forms.', 'analyzerId': 'prebuilt-document'}
    }})
    return [(prefix + '-extract', extractor), (prefix, classifier)]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Preview or create the two safety analyzers. Never replace an existing analyzer.')
    parser.add_argument('--id', default='contoso-safety')
    parser.add_argument('--model', default='gpt-4.1')
    parser.add_argument('--apply-endpoint', help='Explicitly create analyzers at this Foundry endpoint using your Azure CLI identity.')
    args = parser.parse_args()
    definitions_to_create = definitions(args.id, args.model)
    if not args.apply_endpoint:
        print(json.dumps(dict(definitions_to_create), indent=2))
    else:
        with ContentUnderstandingClient(args.apply_endpoint, AzureCliCredential(), api_version='2025-11-01') as client:
            for name, schema in definitions_to_create:
                client.begin_create_analyzer(name, schema, allow_replace=False).result()
                print('Created', name)