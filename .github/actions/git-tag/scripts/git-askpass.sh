#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set +x
set -euo pipefail
case "${1:-}" in
    *Username*) printf '%s\n' 'x-access-token' ;;
    *Password*) printf '%s\n' "${INPUT_GITHUB_TOKEN:?GitHub token required}" ;;
    *) exit 1 ;;
esac
