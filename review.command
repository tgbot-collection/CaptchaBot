#!/bin/sh
cd "$(dirname "$0")" || exit 1

if [ -x venv/bin/python ]; then
    exec venv/bin/python -m spamdet.web_review "$@"
fi

exec python3 -m spamdet.web_review "$@"
