#!/bin/bash
# Wrapper script to run the PrizePicks scraper from cron.
# Activates the venv and runs the ingest module.

cd /Users/aayushdave/props || exit 1
source .venv/bin/activate
python -m props.ingest.prizepicks >> /Users/aayushdave/props/cron.log 2>&1
