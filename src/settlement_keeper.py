# This file is part of the Maker Keeper Framework.
#
# Copyright (C) 2019 EdNoepel, KentonPrescott
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
import types
from os import path
from typing import List

from web3 import Web3, HTTPProvider

from pyflex import Address
from pyflex.gas import DefaultGasPrice, FixedGasPrice
from pyflex.auctions import FixedDiscountCollateralAuctionHouse, EnglishCollateralAuctionHouse, DebtAuctionHouse
from pyflex.auctions import PreSettlementSurplusAuctionHouse
from pyflex.keys import register_keys
from pyflex.lifecycle import Lifecycle
from pyflex.numeric import Wad, Rad, Ray
from pyflex.token import ERC20Token
from pyflex.deployment import GfDeployment
from pyflex.gf import CollateralType, SAFE

from auction_keeper.safe_history import SAFEHistory
from auction_keeper.gas import DynamicGasPrice

class SettlementKeeper:
    """Keeper to facilitate Emergency Shutdown"""

    logger = logging.getLogger('settlement-keeper')

    def __init__(self, args: list, **kwargs):
        """Pass in arguements assign necessary variables/objects and instantiate other Classes"""

        parser = argparse.ArgumentParser("settlement-keeper")

        parser.add_argument("--rpc-host", type=str, default="localhost",
                            help="JSON-RPC host (default: `localhost')")

        parser.add_argument("--rpc-port", type=int, default=8545,
                            help="JSON-RPC port (default: `8545')")

        parser.add_argument("--rpc-timeout", type=int, default=1200,
                            help="JSON-RPC timeout (in seconds, default: 10)")

        parser.add_argument("--network", type=str, required=True,
                            help="Network that you're running the Keeper on (options, 'mainnet', 'kovan', 'testnet')")

        parser.add_argument('--previous-settlement', dest='settlement_facilitated', action='store_true',
                            help='Include this argument if this keeper previously helped to facilitate the processing phase of ES')

        parser.add_argument("--eth-from", type=str, required=True,
                            help="Ethereum address from which to send transactions; checksummed (e.g. '0x12AebC')")

        parser.add_argument("--eth-key", type=str, nargs='*',
                            help="Ethereum private key(s) to use (e.g. 'key_file=/path/to/keystore.json,pass_file=/path/to/passphrase.txt')")

        parser.add_argument("--gf-deployment-file", type=str, required=False,
                            help="Json description of all the system addresses (e.g. /Full/Path/To/configFile.json)")

        parser.add_argument("--safe-engine-deployment-block", type=int, required=False, default=0,
                            help=" Block that the SAFEEngine from gf-deployment-file was deployed at (e.g. 8836668")

        parser.add_argument("--vulcanize-endpoint", type=str,
                            help="When specified, frob history will be queried from a VulcanizeDB lite node, "
                                 "reducing load on the Ethereum node for Vault query")

        parser.add_argument("--vulcanize-key", type=str,
                            help="API key for the Vulcanize endpoint")

        parser.add_argument("--max-errors", type=int, default=100,
                            help="Maximum number of allowed errors before the keeper terminates (default: 100)")

        parser.add_argument("--debug", dest='debug', action='store_true',
                            help="Enable debug output")

        parser.add_argument("--ethgasstation-api-key", type=str, default=None, help="ethgasstation API key")

        parser.add_argument("--gas-initial-multiplier", type=str, default=1.0, help="ethgasstation API key")
        parser.add_argument("--gas-reactive-multiplier", type=str, default=2.25, help="gas strategy tuning")
        parser.add_argument("--gas-maximum", type=str, default=5000, help="gas strategy tuning")



        parser.set_defaults(settlement_facilitated=False)
        self.arguments = parser.parse_args(args)

        self.web3 = kwargs['web3'] if 'web3' in kwargs else Web3(HTTPProvider(endpoint_uri=f"https://{self.arguments.rpc_host}:{self.arguments.rpc_port}",
                                                                              request_kwargs={"timeout": self.arguments.rpc_timeout}))
        self.web3.eth.defaultAccount = self.arguments.eth_from
        register_keys(self.web3, self.arguments.eth_key)
        self.our_address = Address(self.arguments.eth_from)

        if self.arguments.gf_deployment_file:
            self.geb = GfDeployment.from_json(web3=self.web3, conf=open(self.arguments.gf_deployment_file, "r").read())
        else:
            self.geb = GfDeployment.from_network(web3=self.web3, network=self.arguments.network)


        self.deployment_block = self.arguments.safe_engine_deployment_block

        self.max_errors = self.arguments.max_errors
        self.errors = 0

        self.settlement_facilitated = self.arguments.settlement_facilitated

        self.confirmations = 0

        # Create gas strategy
        if self.arguments.ethgasstation_api_key:
            self.gas_price = DynamicGasPrice(self.arguments, self.web3)
        else:
            self.gas_price = DefaultGasPrice()


        logging.basicConfig(format='%(asctime)-15s %(levelname)-8s %(message)s',
                            level=(logging.DEBUG if self.arguments.debug else logging.INFO))


    def main(self):
        """ Initialize the lifecycle and enter into the Keeper Lifecycle controller

        Each function supplied by the lifecycle will accept a callback function that will be executed.
        The lifecycle.on_block() function will enter into an infinite loop, but will gracefully shutdown
        if it recieves a SIGINT/SIGTERM signal.

        """
        with Lifecycle(self.web3) as lifecycle:
            self.lifecycle = lifecycle
            lifecycle.on_startup(self.check_deployment)
            lifecycle.on_block(self.process_block)


    def check_deployment(self):
        self.logger.info('')
        self.logger.info('Please confirm the deployment details')
        self.logger.info(f'Keeper Balance: {self.web3.eth.getBalance(self.our_address.address) / (10**18)} ETH')
        self.logger.info(f'SAFE Engine: {self.geb.safe_engine.address}')
        self.logger.info(f'Accounting Engine: {self.geb.accounting_engine.address}')
        self.logger.info(f'PreSettlementSurplusAuctionHouse: {self.geb.surplus_auction_house.address}')
        self.logger.info(f'Debt Auction House: {self.geb.debt_auction_house.address}')
        self.logger.info(f'Tax Collector: {self.geb.tax_collector.address}')
        self.logger.info(f'Global Settlement: {self.geb.global_settlement.address}')
        self.logger.info('')


    def process_block(self):
        """Callback called on each new block. If too many errors, terminate the keeper to minimize potential damage."""
        if self.errors >= self.max_errors:
            self.lifecycle.terminate()
        else:
            self.check_settlement()


    def check_settlement(self):
        """ After live is 0 for 12 block confirmations, facilitate the processing period, then set_outstanding_coin_supply """
        blockNumber = self.web3.eth.blockNumber
        self.logger.info(f'Checking settlment on block {blockNumber}')

        contract_enabled = self.geb.global_settlement.contract_enabled()

        # Ensure 12 blocks confirmations have passed before facilitating settlement
        if not contract_enabled and (self.confirmations == 12):
            self.logger.info('======== System has been settled ========')

            shutdown_time = self.geb.global_settlement.shutdown_time()
            shutdown_cooldown = self.geb.global_settlement.shutdown_cooldown()
            shutdown_time_in_unix = shutdown_time.replace(tzinfo=timezone.utc).timestamp()
            now = self.web3.eth.getBlock(blockNumber).timestamp
            set_outstanding_coin_supply_time = shutdown_time_in_unix + shutdown_cooldown 

            if not self.settlement_facilitated:
                self.settlement_facilitated = True
                self.facilitate_processing_period()

            # wait until processing time concludes
            elif (now >= set_outstanding_coin_supply_time):
                self.set_outstanding_coin_supply()

                if not (self.arguments.network == 'testnet'):
                    self.lifecycle.terminate()

            else:
                when_set_outstanding_coin_supply_time = datetime.utcfromtimestamp(set_outstanding_coin_supply_time)
                self.logger.info('')
                self.logger.info(f'settlement has been processed and outstanding coin supply will be set on '
                                 f'{when_set_outstanding_coin_supply_time.strftime("%m/%d/%Y, %H:%M:%S")} UTC')
                self.logger.info('')

        elif not contract_enabled and self.confirmations < 13:
            self.confirmations = self.confirmations + 1
            self.logger.info(f'======== System has been settled ( {self.confirmations} confirmations) ========')


    def facilitate_processing_period(self):
        """ Prematurely terminated all active surplus/debt auctions,
        freeze all collateral_types, fast track all collateral auctions, process all underwater safes  """

        self.logger.info('')
        self.logger.info('======== Facilitating Settlement ========')
        self.logger.info('')

        # check collateral_types
        collateral_types = self.get_collateral_types()
        print("Collateral_types")
        for x in collateral_types:
            print(x)

        # Get all auctions that can be prematurely terminated after shutdown
        auctions = self.all_active_auctions()

        # Prematurely terminate all surplus and debt auctions
        self.terminate_auctions_prematurely(auctions["surplus_auctions"], auctions["debt_auctions"])

        # Freeze all collateral_types
        for collateral_type in collateral_types:
            self.geb.global_settlement.freeze_collateral_type(collateral_type).transact(gas_price=self.gas_price)

        # Fast track all collateral auctions
        for key in auctions["collateral_auctions"].keys():
            collateral_type = self.geb.safe_engine.collateral_type(key)
            for bid in auctions["collateral_auctions"][key]:
                self.geb.global_settlement.fast_track_auction(collateral_type,bid.id).transact(gas_price=self.gas_price)

        safes = self.get_underwater_safes(collateral_types)

        # Process all underwater safes
        for i in safes:
            self.geb.global_settlement.process_safe(i.collateral_type, i.address).transact(gas_price=self.gas_price)


    def set_outstanding_coin_supply(self):
        """ Once GlobalSettlement.shutdownCooldown is reached, annihilate any lingering system coin in the Accounting Engine,
        set the outstanding coin supply, and set the collateral_cash_price for all collateral_types  """
        self.logger.info('')
        self.logger.info('======== Setting outstanding coin supply ========')
        self.logger.info('')

        collateral_types = self.get_collateral_types()

        # check if system coin is in AccountingEngine and annihilate it with settleDebt()
        system_coin = self.geb.safe_engine.coin_balance(self.geb.accounting_engine.address)
        if system_coin > Rad(0):
            self.geb.accounting_engine.settle_debt(system_coin).transact(gas_price=self.gas_price)

        # Fix outstanding supply of System coin
        self.geb.global_settlement.set_outstanding_coin_supply().transact(gas_price=self.gas_price)

        # Set fix (collateral/system_coin ratio) for all CollateralTypes
        for collateral_type in collateral_types:
            self.geb.global_settlement.calculate_cash_price(collateral_type).transact(gas_price=self.gas_price)


    def get_collateral_types(self) -> List[CollateralType]:
        """ Use CollateralTypes as saved in https://github.com/makerdao/pyflex/tree/master/config """

        collateral_types = [self.geb.collaterals[key].collateral_type for key in self.geb.collaterals.keys()]
        collateral_types_with_debt = list(filter(lambda l: self.geb.safe_engine.collateral_type(l.name).safe_debt > Wad(0), collateral_types))

        collateral_type_names = [i.name for i in collateral_types_with_debt]

        self.logger.info(f'CollateralTypes to check: {collateral_type_names}')

        return collateral_types_with_debt


    def get_underwater_safes(self, collateral_types: List) -> List[SAFE]:
        """ With all safes every frobbed, compile and return a list safes that are under-collateralized up to 100%  """

        underwater_safes = []

        for collateral_type in collateral_types:

            safe_history = SAFEHistory(self.web3,
                                     self.geb,
                                     collateral_type,
                                     self.deployment_block,
                                     self.arguments.vulcanize_endpoint,
                                     self.arguments.vulcanize_key)

            safes = safe_history.get_safes()

            self.logger.info(f'Collected {len(safes)} from {collateral_type}')
            print(f'Collected {len(safes)} from {collateral_type}')

            i = 0
            for safe in safes.values():
                safe.collateral_type = self.geb.safe_engine.collateral_type(safe.collateral_type.name)
                safety_ratio = self.geb.oracle_relayer.safety_c_ratio(safe.collateral_type)
                debt = Ray(safe.generated_debt) * safe.collateral_type.accumulated_rate
                collateral = Ray(safe.locked_collateral) * safe.collateral_type.safety_price * safety_ratio
                # Check if underwater ->  
                # safe.generated_debt * collateral_type.accumulated_rate > 
                # safe.locked_collateral * collateral_type.safety_price * oracle_relayer.safety_c_ratio[collateral_type]
                if debt > collateral:
                    underwater_safes.append(safe)
                i += 1;

                if i % 100 == 0:
                    self.logger.info(f'Processed {i} safes of {collateral_type.name}')

        return underwater_safes


    def all_active_auctions(self) -> dict:
        """ Aggregates active auctions that meet criteria to be called after Settlement """
        collateral_auctions = {}
        for collateral in self.geb.collaterals.values():
            # Each collateral has it's own collateral auction contract; add auctions from each.
            collateral_auctions[collateral.collateral_type.name] = self.settlement_active_auctions(collateral.collateral_auction_house)

        return {
            "collateral_auctions": collateral_auctions,
            "surplus_auctions": self.settlement_active_auctions(self.geb.surplus_auction_house),
            "debt_auctions": self.settlement_active_auctions(self.geb.debt_auction_house)
        }


    def settlement_active_auctions(self, parentObj) -> List:
        """ Returns auctions that meet the requiremenets to be called by
        GlobalSettlement.fastTrackAuction, Flap.yank, and DebtAuctionHouse.terminateAuctionPrematurely """
        active_auctions = []
        auction_count = parentObj.auctions_started()

        # collateral auctions
        if isinstance(parentObj, EnglishCollateralAuctionHouse):
            for index in range(1, auction_count + 1):
                bid = parentObj._bids(index)
                if bid.high_bidder != Address("0x0000000000000000000000000000000000000000"):
                    if bid.bid_amount < bid.amount_to_raise:
                        active_auctions.append(bid)
                index += 1

        # surplus and debt auctions
        else:
            for index in range(1, auction_count + 1):
                bid = parentObj._bids(index)
                if bid.high_bidder != Address("0x0000000000000000000000000000000000000000"):
                    active_auctions.append(bid)
                index += 1

        return active_auctions

    def terminate_auctions_prematurely(self, surplus_bids: List, debt_bids: List):
        """ Calls terminate_auction_prematurely on all PreSettlementSurplusAuctionHouse and DebtAuctionHouseand auctions ids that meet the shutdown criteria """
        for bid in surplus_bids:
            self.geb.surplus_auction_house.terminate_auction_prematurely(bid.id).transact(gas_price=self.gas_price)

        for bid in debt_bids:
            self.geb.debt_auction_house.terminate_auction_prematurely(bid.id).transact(gas_price=self.gas_price)


if __name__ == '__main__':
    SettlementKeeper(sys.argv[1:]).main()
