#!/usr/bin/env bash

set -xeo pipefail

function cleanup() {
    trap - EXIT SIGINT SIGTERM
    if [ -f "${scratch_file}" ] ; then
        rm -f "${scratch_file}"
    fi
}

trap cleanup EXIT SIGINT SIGTERM

scratch_file=$(mktemp)

envsubst < requirements-dev.in > ${scratch_file}
SHA_SUM=$(sha1sum ${scratch_file} install-requires.txt | head -c 40)
REQUIREMENTS_OUT_FILE=$(echo requirements-dev-python${PYTHON_VERSION}-${SHA_SUM}.txt)

if [ ! -f "${REQUIREMENTS_OUT_FILE}" ] ; then
		time pip-compile requirements-dev.in -o "${REQUIREMENTS_OUT_FILE}"
fi

	time pip-sync "${REQUIREMENTS_OUT_FILE}"
