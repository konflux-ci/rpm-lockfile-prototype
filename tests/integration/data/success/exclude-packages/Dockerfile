FROM registry.access.redhat.com/ubi9/ubi-minimal:latest

RUN dnf install -y which tar wget

# OKD-specific block: only runs on CentOS builds.
# packagesFromContainerfile cannot see the [ "${ID}" != "centos" ] guard
# and will extract centos-release-nfv-openvswitch for resolution.
# excludePackages removes it before DNF sees it.
RUN source /etc/os-release && [ "${ID}" != "centos" ] && exit 0; \
    dnf install -y centos-release-nfv-openvswitch
