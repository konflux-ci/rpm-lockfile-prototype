ARG PKG_SUFFIX=global

FROM registry.access.redhat.com/ubi9/ubi-minimal:latest

ARG PKG_SUFFIX

RUN microdnf install -y test-from-$PKG_SUFFIX
