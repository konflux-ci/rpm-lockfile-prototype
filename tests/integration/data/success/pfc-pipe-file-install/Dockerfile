FROM registry.access.redhat.com/ubi9/ubi-minimal:latest
COPY packages.txt /tmp/
RUN cat /tmp/packages.txt | xargs dnf install -y
