FROM registry.access.redhat.com/ubi9/ubi:latest
COPY deps.txt /opt/config/
RUN xargs dnf install -y < /var/tmp/deps.txt
