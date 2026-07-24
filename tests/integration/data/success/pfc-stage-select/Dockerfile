FROM registry.access.redhat.com/ubi9/ubi-minimal:latest AS builder
RUN microdnf install -y test-builder-only

FROM registry.access.redhat.com/ubi9/ubi-minimal:latest AS runtime
RUN microdnf install -y test-runtime-only
