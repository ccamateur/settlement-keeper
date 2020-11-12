#!/bin/bash
docker build -t reflexer/settlement-keeper .
echo "$DOCKER_PASSWORD" | docker login -u "$DOCKER_USERNAME" --password-stdin
docker push reflexer/settlement-keeper
