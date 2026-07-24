#!/bin/bash
set -ex
microdnf install -y test-script-common
if [ $(arch) = x86_64 ]; then
    microdnf install -y test-script-x86
fi
