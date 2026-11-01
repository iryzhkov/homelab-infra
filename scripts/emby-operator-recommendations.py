#!/usr/bin/env python3
"""Explicit recommendation-only operator entry. No raw API or MCP writer."""
import argparse
import json
import sys

from emby_operator_adapter import load_config, private_read, strict_json
from emby_operator_entry import OPERATIONS, invoke, operator_session, request_fields
from emby_operator_recommendations import Refused, require


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, help='Protected root-owned configuration path')
    parser.add_argument('operation', choices=OPERATIONS)
    parser.add_argument('--request-file', help='Protected JSON request, never credentials in argv')
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        require(config['enabled'], 'Operator disabled')
        request = strict_json(private_read(args.request_file)) if args.request_file else {}
        require(type(request) is dict, 'Invalid operator request')
        require(set(request) in request_fields(args.operation, request),
                'Invalid operator request fields')
        with operator_session(config) as operator:
            # Output contains private scope/Movie IDs; operator stdout is not a public receipt.
            result = invoke(operator, args.operation, request)
            print(json.dumps(result, allow_nan=False))
        return 0
    except Refused as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception:
        # Do not print tracebacks, paths, upstream responses or credential-bearing data.
        print('Operator failed; protected manual inspection required', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
