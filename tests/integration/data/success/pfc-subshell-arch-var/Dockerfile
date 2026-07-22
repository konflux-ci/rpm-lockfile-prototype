FROM registry.access.redhat.com/ubi9/ubi-minimal:latest
RUN EXTRA=$(if [ "$(uname -m)" != "aarch64" ]; then echo -n test-not-aarch64; fi) && \
    microdnf install -y test-common $EXTRA
