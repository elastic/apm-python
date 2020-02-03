#!/usr/bin/env bash

set -e

export PATH=${HOME}/.local/bin:${PATH}
python -m pip install --user -U pip --cache-dir "${PIP_CACHE}"
python -m pip install --user -r "tests/requirements/requirements-${WEBFRAMEWORK}.txt" --cache-dir "${PIP_CACHE}"

export PYTHON_VERSION=$(python -c "import platform; pv=platform.python_version_tuple(); print('pypy' + ('' if pv[0] == 2 else str(pv[0])) if platform.python_implementation() == 'PyPy' else '.'.join(map(str, platform.python_version_tuple()[:2])))")

# check if the full FRAMEWORK name is in scripts/envs
if [[ -e "./tests/scripts/envs/${WEBFRAMEWORK}.sh" ]]
then
    source ./tests/scripts/envs/${WEBFRAMEWORK}.sh
else
    # check if only the first part of the FRAMEWORK is in scripts/envs
    IFS='-'; frameworkParts=($WEBFRAMEWORK); unset IFS;
    if [[ -e "./tests/scripts/envs/${frameworkParts[0]}.sh" ]]
    then
        source ./tests/scripts/envs/${frameworkParts[0]}.sh
    fi
fi

make update-json-schema
make update-gherkin-features

if [[ -n $WAIT_FOR_HOST ]]
then
    echo "Waiting for $WAIT_FOR_HOST:$WAIT_FOR_PORT"
    while ! nc -z $WAIT_FOR_HOST $WAIT_FOR_PORT; do
        sleep 1
    done
    echo "$WAIT_FOR_HOST:$WAIT_FOR_PORT is up!"
fi

if [[ "$WITH_COVERAGE" == "true" ]]
then
    make coverage
else
    make test
fi
