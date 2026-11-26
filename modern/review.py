import argparse
from datetime import datetime, timezone
import json

from azure.core import MatchConditions
from azure.cosmos import CosmosClient
from azure.identity import AzureCliCredential


def decide(container, key, etag, decision, note, reviewer):
    record = container.read_item(key, partition_key=key)
    if record['status'] != 'needs_review' or record['_etag'] != etag:
        raise ValueError('This record changed or is no longer waiting for review. Inspect it again.')
    record.update(status={'approve': 'review_approved', 'reject': 'review_rejected'}[decision],
                  review={'reviewer': reviewer, 'note': note, 'time': datetime.now(timezone.utc).isoformat()})
    return container.replace_item(key, record, etag=etag, match_condition=MatchConditions.IfNotModified)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Inspect a form or record a decision using a reviewer identity.')
    parser.add_argument('endpoint')
    parser.add_argument('id')
    parser.add_argument('--decision', choices=['approve', 'reject'])
    parser.add_argument('--etag')
    parser.add_argument('--note')
    parser.add_argument('--reviewer', help='Reviewer attribution for the record; audit access separately via Azure diagnostics.')
    args = parser.parse_args()
    with CosmosClient(args.endpoint, AzureCliCredential()) as client:
        records = client.get_database_client('forms').get_container_client('records')
        if args.decision:
            if not all([args.etag, args.note, args.reviewer]):
                parser.error('A decision requires --etag, --note and --reviewer.')
            print(decide(records, args.id, args.etag, args.decision, args.note, args.reviewer)['status'])
        else:
            print(json.dumps(records.read_item(args.id, partition_key=args.id), indent=2))