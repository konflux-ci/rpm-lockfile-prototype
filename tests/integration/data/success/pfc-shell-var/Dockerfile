FROM registry.access.redhat.com/ubi9/ubi-minimal:latest
RUN EXTRA_PKGS="test-from-shell-var" && \
    microdnf install -y test-base $EXTRA_PKGS
