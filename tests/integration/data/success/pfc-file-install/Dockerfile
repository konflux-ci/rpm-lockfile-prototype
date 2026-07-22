FROM registry.access.redhat.com/ubi9/ubi-minimal:latest
COPY packages.txt /tmp/
RUN xargs microdnf install -y < /tmp/packages.txt
