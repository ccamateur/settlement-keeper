#!/usr/bin/env bash

set -e

function message() {
    echo
    echo -----------------------------------
    echo "$@"
    echo -----------------------------------
    echo
}

ENVIRONMENT=$1
REGION=$2

if [ "$ENVIRONMENT" == "prod" ]; then
  TAG=latest
elif [ "$ENVIRONMENT" == "test" ]; then
  TAG=test
else
   message UNKNOWN ENVIRONMENT
fi

if [ -z "$ENVIRONMENT" ]; then
  echo 'You must specifiy an environment (bash build-deploy.sh <ENVIRONMENT>).'
  echo 'Allowed values are "staging" or "prod"'
  exit 1
fi

# build image
message BUILDING IMAGE
docker build -t "reflexer/settlement-keeper:${TAG}" .

# docker login
echo "$DOCKER_PASSWORD" | docker login --username "$DOCKER_USER" --password-stdin

# docker push
docker push "reflexer/settlement-keeper:${TAG}"

# service deploy
if [ "$ENVIRONMENT" == "prod" ]; then
  message DEPLOYING MAINNET
  aws ecs update-service --cluster settlement-keeper-mainnet-cluster --service settlement-keeper-mainnet-service --force-new-deployment --endpoint https://ecs.$REGION.amazonaws.com --region $REGION

  message DEPLOYING KOVAN
  aws ecs update-service --cluster settlement-keeper-kovan-cluster --service settlement-keeper-kovan-service --force-new-deployment --endpoint https://ecs.$REGION.amazonaws.com --region $REGION

elif [ "$ENVIRONMENT" == "test" ]; then
  message DEPLOYING TEST
  aws ecs update-service --cluster settlement-keeper-test-cluster --service settlement-keeper-test-service --force-new-deployment --endpoint https://ecs.$REGION.amazonaws.com --region $REGION

else
   message UNKNOWN ENVIRONMENT
fi
