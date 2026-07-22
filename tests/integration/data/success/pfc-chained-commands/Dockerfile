FROM registry.access.redhat.com/ubi9/ubi-minimal:latest
RUN microdnf install -y test-first && \
    microdnf install -y test-second && \
    microdnf clean all
