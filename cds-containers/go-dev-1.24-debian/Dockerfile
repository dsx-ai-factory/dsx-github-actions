# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

FROM golang:1.27.2@sha256:820153096fda4de6ac1b7194d12e416b7dd8f4e9a33d17e0345b111b945162d8

RUN test "$(GOTOOLCHAIN=local go env GOVERSION)" = "go1.26.10"

# Set locale for proper terminal support
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# Install development tools and dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    wget \
    vim \
    procps \
    net-tools \
    ca-certificates \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' ca-certificates)" ge "20250419+deb13u1" \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Air for hot reloading
RUN go install github.com/air-verse/air@v1.62.0

# Install Delve for debugging
RUN go install github.com/go-delve/delve/cmd/dlv@v1.27.0

# Install other useful development tools
# Keep the v1 CLI/config with a compatible Go 1.26 export-data reader.
COPY go-tools/golangci-lint/ /tmp/golangci-lint/
RUN cd /tmp/golangci-lint \
    && go install -mod=readonly github.com/golangci/golangci-lint/cmd/golangci-lint \
    && rm -rf /tmp/golangci-lint
RUN go install golang.org/x/tools/cmd/goimports@v0.37.0
RUN go install github.com/swaggo/swag/cmd/swag@v1.16.4

# Enable CGO for development (allows for better debugging and some dev tools)
ENV CGO_ENABLED=1

WORKDIR /go/src/app
