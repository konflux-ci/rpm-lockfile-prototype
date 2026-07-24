FROM registry.access.redhat.com/ubi9/ubi-minimal:latest

ARG EXTRA_PKG=test-from-arg

RUN microdnf install -y test-base $EXTRA_PKG
