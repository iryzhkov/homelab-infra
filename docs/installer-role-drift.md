# Copied installer role drift check

Run `python3 -B scripts/check-installer-role-drift.py` from a checkout with
Python 3 and Git. The existing Check workflow runs it alongside its focused
unittest script, using a disposable scratch HOME and disk TMPDIR.

The check fetches `iryzhkov/chaldea-fleet-config` at exact source commit
`0438db0dd7ef99299a28519cc2b1de67fbf1ff70`. This pin is local-only until ROOT publishes
that exact commit; ROOT must confirm public reachability before I CI or use.
The check compares every file under
`roles/chaldea_access_trust/` and `roles/chaldea_host_artifact/` with this
repository. Missing files, extra files, unexpected bytes, symbolic links,
invalid pins and fetch failures fail the check. No content is executed from
the reference repository. No role fixtures are mirrored here.

The sole approved difference is
`roles/chaldea_access_trust/handlers/main.yml`: infra appends exactly
`  when: not ansible_check_mode` after `    daemon_reload: true` to keep the
handler from reloading systemd during a check-mode run. The script requires
the exact five-line fleet handler and the exact six-line infra handler.
It does not strip, normalize or ignore lines, including this one.

This checks against the pin, not the latest upstream main. Updating the pin
requires review of both roles and any explicit difference, followed by the
focused tests and real pinned-reference check. The pin is a CI reference only;
it does not import or distribute roles, change deployment source or runtime
configuration, schedule work, create credentials or converge a host.
