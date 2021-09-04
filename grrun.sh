#!/bin/bash

MYDIR=$(readlink -f $(dirname "$0"))
MYNAME=$(basename "$0")

if [[ "$#" -lt 1 || "$1" == '-h' ]]
then
    echo "Usage: $MYNAME <executable [executable_args]>"
    exit 1
fi

if [[ -z $(which $1) ]]
then
    echo "Error: Cannot find file $1"
    exit 1
fi

cmd="$@"

set +e
LD_PRELOAD="$MYDIR/libguardrails.so.0.0" $cmd
status="$?"
set -e

if [[ $status -eq 0 ]]
then
    echo "'$cmd' returned success"
else
    echo "'$cmd' returned error status $status"
fi

