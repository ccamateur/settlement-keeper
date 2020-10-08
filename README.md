# settlement-keeper

[![Build Status](https://travis-ci.org/reflexer-labs/settlement-keeper.svg?branch=master)](https://travis-ci.org/reflexer-labs/settlement-keeper)
[![codecov](https://codecov.io/gh/reflexer-labs/settlement-keeper/branch/master/graph/badge.svg)](https://codecov.io/gh/reflexer-labs/settlement-keeper)

The `settlement-keeper` is used to help facilitate [Emergency Shutdown](https://docs.reflexer.finance/system-contracts/shutdown-module) of the [Generalized Ethereum Bond Protocol](https://github.com/reflexer-labs/geb). Emergency shutdown is an involved, deterministic process, requiring interaction from all user types: SAFE owners, system coin holders, Redemption keepers, PROT governors, and other GEB Stakeholders. A high level overview is as follows:
1. System Shutdown - The Emergency Security Module [(ESM)](https://github.com/reflexer-labs/esm) calls `GlobalSettlement.shutdownSystem()` function, which freezes the USD price for each collateral type as well as many parts of the system.
2. Processing Period - Next, SAFE owners interact with GlobalSettlement to settle their SAFE and withdraw excess collateral. Auctions are left to conclude or are terminated prematurely before system coin redemption.
3. System Coin Redemption  - After the processing period duration `GlobalSettlement.shutdownCooldown` has elapsed, SAFE settlement and all system coin generating processes (auctions) are assumed to have concluded. At this point, system coin holders can begin to claim a proportional amount of each collateral type at a fixed rate.

To prevent a race-condition for system coin holders during Step 3, it's imperative that any SAFES having a collateralization ratio of less than 100% at Step 1 must be processed during Step 2. The owner of an underwater SAFE would not receive excess collateral, so they lack an incentive to `processSAFE` their position in the `GlobalSettlement` contract. Thus, it is the responsibility of a GEB Stakeholder (PROT holders, large system coin holders, etc) to ensure the system facilitates a system coin redemption phase without a time variable. The `settlement-keeper` is a tool to help stakeholders carry out this responsibility.

### Review
The following section assumes familiarity with Emergency Shutdown. Good places to start is the Shutdown Module in the [GEB Docs](https://docs.reflexer.finance/system-contracts/shutdown-module). Functions mentioned are from the implementation contained by the `GlobalSettlement` contract, which is [located here](https://github.com/reflexer-labs/geb/blob/master/src/GlobalSettlement.sol).

## Architecture

`settlement-keeper` directly interacts with the `GlobalSettlement`, `DebtAuctionHouse` and `PreSettlementSurplusAuctionHouse` contracts.

The central goal of the `settlement-keeper` is to process all under-collateralized `SAFES`. This accounting step is performed within `GlobalSettlement.processSAFE()`, and since it is surrounded by other required/important steps in the Emergency Shutdown, a first iteration of this keeper will help to call most of the other public function calls within the `GlobalSettlement` contract.

The keeper checks if the system has been shutdown before attempting to `processSAFE` all underwater SAFEs and `fastTrackAuction` all collateral auctions. After the processing period has been facilitated and the `GlobalSettlement.shutdownCooldown` waittime has been reached, it will transition the system into the system coin redemption phase of Emergency Shutdown by calling `GlobalSettlement.setOutstandingCoinSupply()` and `GlobalSettlement.calculateCashPrice()`. This first iteration of this keeper is naive, as it assumes it's the only keeper and attempts to account for all SAFEs, collateral types, and auctions. Because of this, it's important that the keeper's address has enough ETH to cover the gas costs involved with sending numerous transactions. Any transaction that attempts to call a function that's already been invoked by another Keeper/user would simply fail.


## Operation

Once the keys to an ethereum address are supplied at startup, the keeper works out of the box. It can either run continuously on a local/virtual machine or be run when the operator becomes aware of Emergency Shutdown. A sample startup script is shown below. When new collateral types are added to the protocol, the operator should pull the latest version of the keeper, which would include contracts associated with the aforementioned collateral types.

After the `settlement-keeper` facilitates the processing period, it can be turned off until `GlobalSettlement.shutdownCooldown` is nearly reached. Then, at that point, the operator would pass in the `--previous-settlement` argument during keeper start in order to bypass the feature that supports the processing period. Continuous operation removes the need for this flag.

The keeper's ethereum address should have enough ETH to cover gas costs and is a function of the protocol's state at the time of shutdown (i.e. more SAFEs to `processSAFE` means more required ETH to cover gas costs). The following equation approximates how much ETH is required:
```
min_ETH = average_gasPrice * [ ( DebtAuctionHouse.terminate_auction_prematurely()_gas * #_of_Debt_Auctions     ) +
                               ( PreSettlementSurplusAuctionHouse.terminate_auction_prematurely()_gas * #_of_Surplus_Auctions     ) +
                               ( GlobalSettlement.freezeCollateralType(CollateralType)_gas  * #_of_Collateral_Types  ) +
                               ( GlobalSettlement.fastTrackAuction()_gas     * #_of_Collateral_Auctions     ) +
                               ( GlobalSettlement.processSAFE()_gas     * #_of_Underwater_SAFEs ) +
                               ( AccountingEngine.settleDebt()_gas                              ) +
                               ( GlobalSettlement.setOutstandingCoinSupply()_gas                              ) +
                               ( GlobalSettlement.calculateCashPrice(CollateralType)_gas  * #_of_Collateral_Types  ) ]
```

Here's an example from a recent Kovan test of the `settlement-keeper`; note that the gasCost arguments in this example are conservative upper bounds, computed from `web3.eth.estimateGas()`, which calls the [eth_estimateGas JSON-RPC method](https://github.com/ethereum/wiki/wiki/JSON-RPC#eth_estimategas).
```
min_ETH = 15 GWei * [ ( 196605 * 1  ) +
                      ( 154892 * 1  ) +
                      ( 187083 * 3  ) +
                      ( 389191 * 3  ) +
                      ( 223399 * 30 ) +
                      ( 166397      ) +
                      ( 157094      ) +
                      ( 159159 * 3  ) ]
min_ETH = 15 GWei * 9583257
min_ETH ~= 0.1437 ETH
```




### Installation
#### Prerequisites
- [Python v3.6.6](https://www.python.org/downloads/release/python-366/)
- [virtualenv](https://virtualenv.pypa.io/en/latest/)
    - This project requires *virtualenv* to be installed if you want to use Reflexer's python tools. This helps with making sure that you are running the right version of python and checks that all of the pip packages that are installed in the **install.sh** are in the right place and have the right versions.

In order to clone the project and install required third-party packages please execute:
```
git clone https://github.com/reflexer-labs/settlement-keeper.git
cd settlement-keeper
git submodule update --init --recursive
./install.sh
```

For some known Ubuntu and macOS issues see the [pyflex](https://github.com/reflexer-labs/pyflex) README.


### Sample Startup Script

Make a run-settlement-keeper.sh to easily spin up the settlement-keeper.

```
#!/bin/bash
/full/path/to/settlement-keeper/bin/settlement-keeper \
	--rpc-host 'sample.ParityNode.com' \
	--network 'kovan' \
	--eth-from '0xABCAddress' \
	--eth-key 'key_file=/full/path/to/keystoreFile.json,pass_file=/full/path/to/passphrase/file.txt' \
	--vat-deployment-block 14374534
```


## Testing

Prerequisites:
* Download [docker and docker-compose](https://www.docker.com/get-started)

This project uses [pytest](https://docs.pytest.org/en/latest/) for unit testing.  Testing of GEB is
performed on a Dockerized local testchain included in `tests\config`.

In order to be able to run tests, please install development dependencies first by executing:
```
pip3 install -r requirements-dev.txt
```

You can then run all tests with:
```
./test.sh
```

## License

See [COPYING](https://github.com/reflexer-labs/auction-keeper/blob/master/COPYING) file.

### Disclaimer

YOU (MEANING ANY INDIVIDUAL OR ENTITY ACCESSING, USING OR BOTH THE SOFTWARE INCLUDED IN THIS GITHUB REPOSITORY) EXPRESSLY UNDERSTAND AND AGREE THAT YOUR USE OF THE SOFTWARE IS AT YOUR SOLE RISK.
THE SOFTWARE IN THIS GITHUB REPOSITORY IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
YOU RELEASE AUTHORS OR COPYRIGHT HOLDERS FROM ALL LIABILITY FOR YOU HAVING ACQUIRED OR NOT ACQUIRED CONTENT IN THIS GITHUB REPOSITORY. THE AUTHORS OR COPYRIGHT HOLDERS MAKE NO REPRESENTATIONS CONCERNING ANY CONTENT CONTAINED IN OR ACCESSED THROUGH THE SERVICE, AND THE AUTHORS OR COPYRIGHT HOLDERS WILL NOT BE RESPONSIBLE OR LIABLE FOR THE ACCURACY, COPYRIGHT COMPLIANCE, LEGALITY OR DECENCY OF MATERIAL CONTAINED IN OR ACCESSED THROUGH THIS GITHUB REPOSITORY.
