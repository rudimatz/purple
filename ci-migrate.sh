#!/bin/bash

cd /workspace

# Add /workspace as a safe git directory
git config --global --add safe.directory /workspace

# Build Datatracker RPC API client
wget -O rpcapi.yaml https://raw.githubusercontent.com/ietf-tools/datatracker/feat/rpc-api/rpcapi.yaml
npx --yes @openapitools/openapi-generator-cli generate --generator-key datatracker # config in openapitools.json

# Install requirements.txt dependencies
pip3 --disable-pip-version-check --no-cache-dir install --user --no-warn-script-location -r requirements.txt

# Run nginx
echo "Starting nginx..."
sudo nginx

# Wait for DB container
echo "Waiting for DB container to come online..."
/usr/local/bin/wait-for db:5432 -- echo "PostgreSQL ready"
echo "Waiting for RFCED DB Container to come online..."
/usr/local/bin/wait-for rfced:3306 -- echo "MariaDB ready"

export DATATRACKER_RPC_API_BASE=http://datatracker:8001/api/rpc
export DATATRACKER_API_V1_BASE=http://datatracker:8001/api/v1

# Run Django migrations
echo "Running Django migrations..."
./manage.py migrate --no-input

# Run rfced data migration
echo "Running xfer_from_rfced migration..."
./manage.py xfer_from_rfced
