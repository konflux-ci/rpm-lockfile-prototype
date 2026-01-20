ARG BASE_IMAGE=registry.fedoraproject.org/fedora:latest

FROM ${BASE_IMAGE}
RUN dnf install -y python3 python3-pip python3-dnf skopeo rpm git-core
WORKDIR /app
ARG GIT_REF=heads/main
RUN python3 -m pip install https://github.com/konflux-ci/rpm-lockfile-prototype/archive/refs/${GIT_REF}.tar.gz
WORKDIR /work
ENTRYPOINT ["/usr/local/bin/rpm-lockfile-prototype"]
