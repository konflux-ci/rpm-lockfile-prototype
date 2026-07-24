FROM registry.access.redhat.com/ubi9/ubi-minimal:latest

RUN microdnf install -y test-first-run

RUN microdnf install -y test-second-run

RUN microdnf install -y test-third-run && microdnf clean all
