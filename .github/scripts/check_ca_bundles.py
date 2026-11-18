#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Fail if a PEM CA bundle trusts a distrusted root certificate."""

import hashlib
import re
import ssl
import sys

# SHA-256 fingerprints of the DER certificates, so stripped labels cannot hide them.
DISTRUSTED = {
    # CVE-2026-111943 (eMudhra)
    "d7a7a0fb5d7e2731d771e9484ebcdef71d5f0c3e0a2948782bc83ee0ea699ef4": "AAA Certificate Services",
    "86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b": "emSign ECC Root CA - G3",
}
# Rejects an emptied or truncated bundle; the bundles checked hold about 100 roots or more.
MIN_CERTIFICATES = 50
PEM = re.compile(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", re.S)


def check(path):
    with open(path, encoding="ascii", errors="ignore") as bundle:
        certificates = PEM.findall(bundle.read())
    digests = {hashlib.sha256(ssl.PEM_cert_to_DER_cert(c)).hexdigest() for c in certificates}
    found = sorted(DISTRUSTED[d] for d in digests & DISTRUSTED.keys())
    ok = len(certificates) >= MIN_CERTIFICATES and not found
    print(f"{'PASS' if ok else 'FAIL'} {path}: {len(certificates)} certificates, "
          f"distrusted: {', '.join(found) or 'none'}")
    return ok


def main(paths):
    if not paths:
        print("usage: check_ca_bundles.py BUNDLE...", file=sys.stderr)
        return 2
    results = [check(path) for path in paths]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
