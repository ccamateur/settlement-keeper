#  Dockerized Settlement-Keeper

# Build and Run the settlement-keeper locally

## Prerequisite:
- docker installed: https://docs.docker.com/install/
- Git

## Installation
Clone the project and install required third-party packages:
```
git clone git@github.com:reflexer-labs/settlement-keeper.git
cd settlement-keeper
git submodule update --init --recursive
```

## Configure, Build and Run:

## Configure
### Configure Envrionment variables
The settlement-keeper requires the following environment variables in `env/envvars.sh` file.
Make a copy of the envvarstemplate.sh file, name it envvars.sh, and enter the required environment variables.

```
# DNS for ETH Parity Node, ex: myparity.node.com (default: `localhost')
SERVER_ETH_RPC_HOST=

# Ethereum blockchain to connect to, ex: (mainnet | kovan)
BLOCKCHAIN_NETWORK=

# Account used to pay for gas
ETH_FROM_ADDRESS=

# URL of Vulcanize instance to use
VULCANIZE_URL=

# ETH Gas Station API key
ETH_GASSTATION_API_KEY=

# For ease of use, do not change the location of ETH account keys, note that account files should always be placed in the secrets directory of the settlement-keeper, and files named as indicated.
ETH_ACCOUNT_KEY='key_file=/opt/keeper/settlement-keeper/secrets/keystore.json,pass_file=/opt/keeper/settlement-keeper/secrets/password.txt'
```

### Configure ETH account keys

Place unlocked keystore and password file for the account address under *secrets* directory. The names of the keystore should be *keystore.json*, and password file should be *password.txt*. If you name your secrets files something other than indicated, you will need to update the *ETH_ACCOUNT_KEY=* value, in envvars.sh, to reflect the change.

## Build
### Build the docker image locally
From within the `settlement-keeper` directory, run the following command:
```
docker build --tag settlement-keeper .
```

## Run
### Run the settlement-keeper
Running the settlement-keeper requires you to pass the environment file to the container, and map a volume to the secrets directory to allow the settlement-keeper to access your keystore files.
From within the `settlement-keeper` directory, run the following command:
```
docker run \
    --env-file env/envvars.sh \
    --volume "$(pwd)"/secrets:/opt/keeper/settlement-keeper/secrets \
    settlement-keeper:latest
```

To run the container in the background, use the `-d` option.
```
docker run -d \
    --env-file env/envvars.sh \
    --volume "$(pwd)"/secrets:/opt/keeper/settlement-keeper/secrets \
    settlement-keeper:latest
```
