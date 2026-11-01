#!/usr/bin/env python3
"""Explicit recommendation-only operator entry. No raw API or MCP writer."""
import argparse
import json
import sys

from emby_operator_adapter import (OperatorAdapter, load_config, load_credentials,
                                   private_read, strict_json)
from emby_operator_recommendations import LIST_OPERATIONS, Journal, Operator, Refused, require

LEGACY_FIELDS = {'preview': {'movies'}, 'apply': {'key', 'movies', 'baseline_digest'},
                 'display-title-preview': {'title'},
                 'set-display-title': {'key', 'title', 'baseline_digest'},
                 'remove': {'key', 'baseline_digest'}, 'reconcile': set(),
                 'migrate-preview': set(), 'downgrade-preview': set(),
                 'migrate': {'key', 'intent_digest'}, 'downgrade': {'key', 'intent_digest'},
                 'init-preview': set(), 'init': {'key', 'intent_digest'}}
OPERATIONS = (*LEGACY_FIELDS, 'show', 'list-preview', *LIST_OPERATIONS)


def request_fields(operation, request):
    """Exact allowed field sets; list-preview binds its named operation's arguments."""
    if operation in LEGACY_FIELDS:
        return [LEGACY_FIELDS[operation]]
    if operation == 'show':
        return [set(), {'list'}]
    if operation in LIST_OPERATIONS:
        return [{'key', 'list', 'intent_digest', *LIST_OPERATIONS[operation]}]
    target = request.get('operation')
    if operation == 'list-preview' and isinstance(target, str) and target in LIST_OPERATIONS:
        return [{'operation', 'list', *LIST_OPERATIONS[target]}]
    return []


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, help='Protected root-owned configuration path')
    parser.add_argument('operation', choices=OPERATIONS)
    parser.add_argument('--request-file', help='Protected JSON request, never credentials in argv')
    args = parser.parse_args(argv)
    api = None
    journal = None
    try:
        config = load_config(args.config)
        require(config['enabled'], 'Operator disabled')
        request = strict_json(private_read(args.request_file)) if args.request_file else {}
        require(type(request) is dict, 'Invalid operator request')
        require(set(request) in request_fields(args.operation, request),
                'Invalid operator request fields')
        journal = Journal(config['journal_directory'], root_safe=True)
        api = OperatorAdapter(config, journal)
        credentials = load_credentials(config['credential_file'])
        try:
            api.authenticate(credentials)
        finally:
            credentials.clear()
        operator = Operator(api, journal, **{
            k: config[k] for k in ('server', 'actor', 'recipient', 'library', 'recipe')})
        # Output contains private scope/Movie IDs; operator stdout is not a public receipt.
        result = getattr(operator, args.operation.replace('-', '_'))(**request)
        print(json.dumps(result, allow_nan=False))
        return 0
    except Refused as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception:
        # Do not print tracebacks, paths, upstream responses or credential-bearing data.
        print('Operator failed; protected manual inspection required', file=sys.stderr)
        return 2
    finally:
        if api is not None:
            api.close()
        if journal is not None:
            journal.close()


if __name__ == '__main__':
    sys.exit(main())
