FROM registry.access.redhat.com/ubi9/ubi-minimal:latest
RUN microdnf install -y test-common && \
    [ $(arch) != x86_64 ] || microdnf install -y test-x86-only && \
    [ $(arch) = aarch64 ] && microdnf install -y test-aarch64-only || true
