FROM registry.access.redhat.com/ubi9/ubi:latest
COPY packages-x86_64.txt packages-aarch64.txt /tmp/
RUN xargs dnf install -y < /tmp/packages-$(arch).txt
