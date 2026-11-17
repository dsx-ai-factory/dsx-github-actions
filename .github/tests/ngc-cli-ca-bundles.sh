#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Install the NGC CLI with each runner installer and check that none of its CA
# bundles trusts a distrusted root (see check_ca_bundles.py).

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
installers=(
  .github/actions/resource-push-ngc/scripts/install-ngc-cli.sh
  .github/actions/helm-shared/scripts/install-ngc.sh
)
status=0
for installer in "${installers[@]}"; do
  echo "== $installer"
  home=$(mktemp -d)
  # A fresh HOME and a PATH without any installed ngc force a real download and patch.
  env -i "HOME=$home" "PATH=/usr/bin:/bin" bash "$repo_root/$installer"
  bundles="$home/.local/ngc-cli/_internal"
  python3 "$repo_root/.github/scripts/check_ca_bundles.py" \
    "$bundles/certifi/cacert.pem" \
    "$bundles/grpc/_cython/_credentials/roots.pem" \
    "$bundles/botocore/cacert.pem" || status=1
  rm -rf "$home"
done
exit "$status"
