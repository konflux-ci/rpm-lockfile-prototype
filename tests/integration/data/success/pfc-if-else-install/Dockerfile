FROM registry.access.redhat.com/ubi9/ubi-minimal:latest
RUN if [ $(arch) = x86_64 ]; then \
        microdnf install -y test-x86-branch; \
    else \
        microdnf install -y test-else-branch; \
    fi
