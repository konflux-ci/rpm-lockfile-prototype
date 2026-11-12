FROM registry.access.redhat.com/ubi9/ubi-minimal@sha256:062c52ff973065752b0965787649db2bcf551a6c727a00e95a3eb42cebadbdab

ARG EXTRA_PKG=test-from-arg

RUN microdnf install -y test-base && microdnf clean all

RUN microdnf install -y $EXTRA_PKG

RUN microdnf upgrade -y findutils

RUN microdnf reinstall -y shadow-utils
