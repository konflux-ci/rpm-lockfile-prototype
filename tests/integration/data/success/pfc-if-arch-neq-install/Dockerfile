FROM registry.access.redhat.com/ubi9/ubi-minimal:latest
RUN microdnf install -y test-common && \
    if [ $(arch) != s390x ]; then \
        microdnf install -y test-not-s390x; \
    fi
