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

FROM golang:1.27.1@sha256:3680233e3204827fbdc66088528ae6d4b3d034f51d03a99d454f6de034888244

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
    && rm -rf /var/lib/apt/lists/*

# Install Air for hot reloading
RUN go install github.com/air-verse/air@v1.62.0

# Install Delve for debugging
RUN go install github.com/go-delve/delve/cmd/dlv@v1.25.2

# Install other useful development tools
RUN go install github.com/golangci/golangci-lint/cmd/golangci-lint@v1.64.8
RUN go install golang.org/x/tools/cmd/goimports@v0.37.0
RUN go install github.com/swaggo/swag/cmd/swag@v1.16.4

# Enable CGO for development (allows for better debugging and some dev tools)
ENV CGO_ENABLED=1

WORKDIR /go/src/app
