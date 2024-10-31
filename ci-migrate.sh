#!/bin/bash

cd /workspace

# Add /workspace as a safe git directory
git config --global --add safe.directory /workspace

# Turn off git info in zsh prompt (causes slowdowns)
git config oh-my-zsh.hide-info 1

# Install requirements.txt dependencies
wget -O rpcapi.yaml https://raw.githubusercontent.com/ietf-tools/datatracker/feat/rpc-api/rpcapi.yaml
npx --yes @openapitools/openapi-generator-cli generate  # config in openapitools.json
pip3 --disable-pip-version-check --no-cache-dir install --user --no-warn-script-location -r requirements.txt

# Run nginx
echo "Starting nginx..."
sudo nginx

# Wait for DB container
echo "Waiting for DB container to come online..."
/usr/local/bin/wait-for db:5432 -- echo "PostgreSQL ready"

# Run migration
echo "Running xfer_from_rfced migration..."
./manage.py xfer_from_rfced
