#!/bin/bash

declare -a TESTCHAINS=("rai-testchain-value-fixed-discount-uniswap-multisig-basic" "rai-testchain-value-fixed-discount-uniswap-multisig-safe")

declare -a TESTCHAINS=("testchain-value-fixed-discount-uniswap-vote-quorum")

run_test () {
  export TESTCHAIN=$1

  # Pull the docker image
  docker pull reflexer/${TESTCHAIN}

  pushd ./lib/pyflex

  # Remove existing container if tests not gracefully stopped
  docker-compose -f config/${TESTCHAIN}.yml down


  # Start parity and wait to initialize
  echo Starting parity
  docker-compose -f config/${TESTCHAIN}.yml up -d parity
  sleep 2
  popd

  export PYTHONPATH=$PYTHONPATH:./lib/pyflex:./lib/auction-keeper:./lib/pygasprice-client
  py.test -s --cov=src --cov-report=term --cov-append tests/test_settlement_keeper.py
  TEST_RESULT=$?

  echo Stopping container
  pushd ./lib/pyflex
  docker-compose -f config/${TESTCHAIN}.yml down
  popd

  return $TEST_RESULT

}


# If passing a single config or test file, just run tests on one testchain
while getopts :c:f: option
do
case "${option}"
in
c) TESTCHAIN=${OPTARG};;
esac
done

if [ ! -z ${TESTCHAIN} ];then
  echo "Testing on testchain ${TESTCHAIN}"
  run_test $TESTCHAIN
  exit $?
fi

COMBINED_RESULT=0
for config in "${TESTCHAINS[@]}"
do
  run_test $config
  COMBINED_RESULT=$(($COMBINED_RESULT + $?))
done


exit $COMBINED_RESULT
