# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
FROM --platform=$BUILDPLATFORM alpine:3.22.1 AS proxy-check
RUN test "$HTTP_PROXY" = "http://proxy.example:3128" \
  && test "$http_proxy" = "$HTTP_PROXY" \
  && test "$HTTPS_PROXY" = "http://proxy.example:3129" \
  && test "$https_proxy" = "$HTTPS_PROXY" \
  && test "$NO_PROXY" = "localhost,Exact.example" \
  && test "$no_proxy" = "$NO_PROXY" \
  && printf 'proxy preserved\n' > /proxy-proof

FROM nvcr.io/nvidia/distroless/go:v4.1.4
COPY --from=proxy-check /proxy-proof /proxy-proof
