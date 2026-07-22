FROM registry.access.redhat.com/ubi9/ubi-minimal:latest
RUN microdnf install -y test-common && \
    if [ $(arch) = x86_64 ]; then microdnf install -y test-x86-only; fi && \
    if [ $(arch) = aarch64 ]; then microdnf install -y test-aarch64-only; fi
