#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -eu

check_ca_certificates() {
    test -s /etc/ssl/certs/ca-certificates.crt
    if command -v apk >/dev/null 2>&1; then
        installed="$(apk info --installed --verbose 'ca-certificates>=20261020-r0')"
        test -n "$installed"
    else
        case "$1" in
            go) minimum="20250419+deb13u1" ;;
            tools) minimum="20260816ubuntu0.22.04.1" ;;
        esac
        installed="$(dpkg-query -W -f='${Version}' ca-certificates)"
        dpkg --compare-versions "$installed" ge "$minimum"
    fi
    printf 'Verified ca-certificates %s\n' "$installed"
}

case "${1:-}" in
    go)
        # Ignore auto-downloads: test the toolchain that is actually in the image.
        export GOTOOLCHAIN=local
        go version
        test "$(go env GOVERSION)" = "go1.26.10"
        check_ca_certificates go
        test "$(go env CGO_ENABLED)" = "1"
        air -v
        dlv version
        golangci-lint --version
        swag --version
        for tool in air dlv golangci-lint goimports swag; do
            go version -m "$(command -v "$tool")" | sed -n '1p' | grep 'go1\.26\.10$'
        done

        workdir="$(mktemp -d)"
        trap 'rm -rf "$workdir"' EXIT HUP INT TERM
        cd "$workdir"
        cat > hello.go <<'GO'
package main
func main() {
    fmt.Println("Hello from the CDS Go container")
}
GO
        goimports -w hello.go
        go build -o hello hello.go
        test "$(./hello)" = "Hello from the CDS Go container"
        go build '-gcflags=all=-N -l' -o debughello hello.go
        printf 'continue\nexit\n' > dlv.init
        dlv exec --allow-non-terminal-interactive --init dlv.init ./debughello
        printf 'module cds-container-smoke\n\ngo 1.26.10\n' > go.mod
        cat > .golangci.yml <<'YAML'
linters:
  disable-all: true
  enable: [govet, errcheck, ineffassign, staticcheck, unused]
YAML
        golangci-lint run --no-config --disable-all --enable=govet hello.go
        golangci-lint run --config .golangci.yml hello.go
        cat > bad.go <<'GO'
package main
import "fmt"
func main() { fmt.Printf("%d", "not an integer") }
GO
        if golangci-lint run --no-config --disable-all --enable=govet bad.go; then
            echo "golangci-lint did not report the invalid Printf call" >&2
            exit 1
        fi
        rm bad.go
        cat > cgo.go <<'GO'
package main
/* int answer(void) { return 42; } */
import "C"
func main() {
    if C.answer() != 42 { panic("CGO failed") }
}
GO
        go build -o cgo cgo.go
        ./cgo
        ;;
    tools)
        node <<'NODE'
const assert = require('node:assert/strict');
const [major, minor, patch] = process.versions.node.split('.').map(Number);
assert(major === 24 && (minor > 22 || (minor === 22 && patch >= 1)),
  `Node 24 >= 24.22.1 required, found ${process.versions.node}`);
assert.notEqual(process.env.NODE_TLS_REJECT_UNAUTHORIZED, '0');
assert(require('node:tls').rootCertificates.length > 0);
console.log(`Verified Node ${process.versions.node}`);
NODE
        check_ca_certificates tools
        bazel --version
        bazel6 --version
        bazel9 --version
        test "$(readlink -f "$(command -v bazel)")" = "$(readlink -f "$(command -v bazel9)")"
        kubectl version --client
        kubelogin --version
        helm version --short
        terraform version
        terragrunt --version
        ngc version
        docker --version
        docker buildx version
        docker compose version
        regctl version
        yq --version
        jq --version
        npm --version
        python3 --version
        pip3 --version
        python3 <<'PYTHON'
import certifi
import pip
import pip._vendor.certifi as pip_certifi

assert pip.__version__ == "26.2.2", pip.__version__
assert certifi.__version__ == "2026.10.19", certifi.__version__
assert pip_certifi.__version__ == "2026.10.19", pip_certifi.__version__
print("Verified system pip and both certifi bundles")
PYTHON
        git --version
        git lfs version
        make --version
        uv --version
        test "$(uv --version | cut -d ' ' -f2)" = "0.13.5"
        uvx --version
        ;;
    *)
        echo "Usage: security-runtime.sh {go|tools}" >&2
        exit 2
        ;;
esac
