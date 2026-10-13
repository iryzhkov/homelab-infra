#!/usr/bin/env python3
"""Exporter-only compatibility for the existing Proxmox cluster CA.

Python 3.13 / urllib3 enables strict X.509 extension checks, while this
cluster's existing CA omits keyUsage. Clear only that policy flag; CA trust,
certificate signatures, validity periods and hostname verification remain.
This affects only this exporter process, whose network policy allows loopback.
"""
import ssl

import urllib3.connection
import urllib3.util.ssl_

_original_context = urllib3.util.ssl_.create_urllib3_context


def pve_ssl_context(*args, **kwargs):
    context = _original_context(*args, **kwargs)
    context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return context


def configure_tls():
    urllib3.util.ssl_.create_urllib3_context = pve_ssl_context
    urllib3.connection.create_urllib3_context = pve_ssl_context


if __name__ == "__main__":
    configure_tls()
    from pve_exporter.cli import main
    main()
