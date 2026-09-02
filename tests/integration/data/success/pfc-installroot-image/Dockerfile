FROM registry.access.redhat.com/ubi9/ubi-minimal:latest
RUN microdnf --installroot=/mnt/rootfs install -y test-installroot-only \
    && microdnf --installroot=/mnt/rootfs clean all
