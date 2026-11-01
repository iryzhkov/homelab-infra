"""Shared protected operator lifecycle for the CLI and the MCP socket worker.

One function opens the existing root journal, a fresh authenticated admin
session and the Operator for one enabled protected config; another calls one
typed public Operator method by its CLI operation name. Both callers use the
same Journal, OperatorAdapter, credential and Operator code. Nothing here adds
state, a queue, a subprocess or a second journal.
"""
from contextlib import contextmanager

from emby_operator_adapter import OperatorAdapter, load_credentials
from emby_operator_recommendations import LIST_OPERATIONS, Journal, Operator, require

LEGACY_FIELDS = {'preview': {'movies'}, 'apply': {'key', 'movies', 'baseline_digest'},
                 'display-title-preview': {'title'},
                 'set-display-title': {'key', 'title', 'baseline_digest'},
                 'remove': {'key', 'baseline_digest'}, 'reconcile': set(),
                 'migrate-preview': set(), 'downgrade-preview': set(),
                 'migrate': {'key', 'intent_digest'}, 'downgrade': {'key', 'intent_digest'},
                 'init-preview': set(), 'init': {'key', 'intent_digest'}}
OPERATIONS = (*LEGACY_FIELDS, 'show', 'list-preview', *LIST_OPERATIONS)
SCOPE = ('server', 'actor', 'recipient', 'library', 'recipe')


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


@contextmanager
def operator_session(config, *, request_guard=None):
    """Yield the Operator for one validated, enabled protected config.

    request_guard is the optional trusted adapter callback that the socket
    worker uses for its deadline and cancellation; the CLI passes none. The
    adapter and journal are closed on every exit path.
    """
    require(config['enabled'], 'Operator disabled')
    journal = Journal(config['journal_directory'], root_safe=True)
    api = None
    try:
        if request_guard is None:
            api = OperatorAdapter(config, journal)
        else:
            api = OperatorAdapter(config, journal, request_guard=request_guard)
        credentials = load_credentials(config['credential_file'])
        try:
            api.authenticate(credentials)
        finally:
            credentials.clear()
        yield Operator(api, journal, **{k: config[k] for k in SCOPE})
    finally:
        if api is not None:
            api.close()
        journal.close()


def invoke(operator, operation, request):
    """Call one typed public Operator method with exactly its allowed fields."""
    require(operation in OPERATIONS and type(request) is dict and
            set(request) in request_fields(operation, request),
            'Invalid operator request fields')
    return getattr(operator, operation.replace('-', '_'))(**request)
