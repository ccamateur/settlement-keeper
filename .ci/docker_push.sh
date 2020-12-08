#!/bin/bash
docker build -t reflexer/settlement-keeper .
echo "$DOCKER_PASSWORD" | docker login -u "$DOCKER_USERNAME" --password-stdin
docker tag reflexer/settlement-keeper reflexer/settlement-keeper:$COMMIT
docker push reflexer/settlement-keeper
docker push reflexer/settlement-keeper:$COMMIT
