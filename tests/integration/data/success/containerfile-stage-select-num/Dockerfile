FROM registry.invalid/does-not-exist@sha256:0000000000000000000000000000000000000000000000000000000000000000 AS builder
RUN dnf install -y test-builder-only

FROM registry.access.redhat.com/ubi9/ubi-minimal:latest AS runtime
RUN microdnf install -y test-runtime-only
