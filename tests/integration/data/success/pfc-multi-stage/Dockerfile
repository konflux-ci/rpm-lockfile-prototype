FROM registry.access.redhat.com/ubi9/ubi-minimal:latest AS builder
RUN microdnf install -y test-builder-only

FROM registry.access.redhat.com/ubi9/ubi-minimal:latest AS middleware
RUN microdnf install -y test-middleware-only

FROM registry.access.redhat.com/ubi9/ubi-minimal:latest AS final
RUN microdnf install -y test-final-only
