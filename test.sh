#!/bin/bash

CONFIG="testchain-value-fixed-discount-uniswap-multisig-basic"
while getopts :c: option
do
case "${option}"
in
c) CONFIG=${OPTARG};;
esac
done

# Pull the docker image
docker pull reflexer/testchain-pyflex:${CONFIG}
pushd ./lib/pyflex
# Stop any existing containers
docker-compose -f config/${CONFIG}.yml down

# Start the docker image and wait for parity to initialize
docker-compose -f config/${CONFIG}.yml up -d
sleep 2
popd

PYTHONPATH=$PYTHONPATH:./lib/pyflex:./lib/auction-keeper:./lib/pygasprice-client py.test -s \
--cov=src --cov-report=term --cov-append tests/test_settlement_keeper.py $@
TEST_RESULT=$?

echo Stopping container
pushd ./lib/pyflex
docker-compose -f config/${CONFIG}.yml down
popd

exit $TEST_RESULT
