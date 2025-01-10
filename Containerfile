FROM registry.fedoraproject.org/fedora:41
RUN dnf install -y python3 python3-pip python3-dnf skopeo rpm
WORKDIR /app
COPY . .
RUN python3 -m pip install .
ENTRYPOINT ["/usr/local/bin/rpm-lockfile-prototype"]
