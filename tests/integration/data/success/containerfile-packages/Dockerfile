FROM registry.access.redhat.com/ubi9/ubi-minimal:latest

ARG EXTRA_PKG=test-from-arg

RUN microdnf install -y test-base && microdnf clean all

RUN microdnf install -y $EXTRA_PKG

RUN microdnf upgrade -y findutils

RUN microdnf reinstall -y shadow-utils
