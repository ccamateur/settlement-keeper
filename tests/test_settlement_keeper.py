# This file is part of Maker Keeper Framework.
#
# Copyright (C) 2019 KentonPrescott
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


import pytest

from datetime import datetime, timedelta, timezone
import time
from typing import List
import logging

from web3 import Web3

from src.settlement_keeper import SettlementKeeper

from pyflex import Address
from pyflex.approval import directly, approve_safe_modification_directly
from pyflex.auctions import PreSettlementSurplusAuctionHouse, DebtAuctionHouse
from pyflex.auctions import EnglishCollateralAuctionHouse, FixedDiscountCollateralAuctionHouse
from pyflex.deployment import GfDeployment
from pyflex.gf import Collateral, CollateralType, SAFE
from pyflex.numeric import Wad, Ray, Rad
from pyflex.shutdown import ESM, GlobalSettlement

from tests.test_auctions import create_debt, check_active_auctions, max_delta_debt
from tests.test_gf import mint_prot, wrap_eth, wrap_modify_safe_collateralization, set_collateral_price, get_collateral_price
from tests.helpers import time_travel_by


def open_safe(geb: GfDeployment, collateral: Collateral, address: Address, debtMultiplier: int = 1):
    assert isinstance(geb, GfDeployment)
    assert isinstance(collateral, Collateral)
    assert isinstance(address, Address)

    collateral.approve(address)
    wrap_eth(geb, address, Wad.from_number(20))
    assert collateral.adapter.join(address, Wad.from_number(20)).transact(from_address=address)
    wrap_modify_safe_collateralization(geb, collateral, address, Wad.from_number(20), Wad.from_number(20 * debtMultiplier))

    assert geb.safe_engine.global_debt() >= Rad(Wad.from_number(20 * debtMultiplier))
    assert geb.safe_engine.coin_balance(address) >= Rad.from_number(20 * debtMultiplier)


def wipe_debt(geb: GfDeployment, collateral: Collateral, address: Address):
    safe = geb.safe_engine.safe(collateral.collateral_type, address)
    assert Rad(safe.generated_debt) >= geb.safe_engine.coin_balance(address)
    delta_collateral = Ray(geb.safe_engine.coin_balance(address)) / geb.safe_engine.collateral_type(collateral.collateral_type.name).accumulated_rate
    wrap_modify_safe_collateralization(geb, collateral, address, Wad(0), Wad(delta_collateral) * -1) #because there is residual state on the testchain
    assert geb.safe_engine.coin_balance(address) <= Rad(Wad(1)) # pesky dust amount in Dai amount

def open_underwater_safe(geb: GfDeployment, collateral: Collateral, address: Address):
    open_safe(geb, collateral, address, 50)
    #previous_eth_price = geb.safe_engine.collateral_type(collateral.collateral_type.name).safety_price * geb.oracle_relayer.safety_c_ratio(collateral.collateral_type)
    previous_eth_price = get_collateral_price(collateral)
    print(f"Previous ETH Price {previous_eth_price} USD")
    #set_collateral_price(geb, collateral, Wad.from_number(49))
    set_collateral_price(geb, collateral, previous_eth_price / Wad.from_number(5))

    safe = geb.safe_engine.safe(collateral.collateral_type, address)
    collateral_type = geb.safe_engine.collateral_type(collateral.collateral_type.name)
    safety_c_ratio = geb.oracle_relayer.safety_c_ratio(collateral_type)
    assert (safe.generated_debt * collateral_type.accumulated_rate) > (safe.locked_collateral * collateral_type.safety_price * safety_c_ratio)

    return previous_eth_price

def create_surplus(geb: GfDeployment, surplus_auction_house: PreSettlementSurplusAuctionHouse, deployment_address: Address):
    assert isinstance(geb, GfDeployment)
    assert isinstance(surplus_auction_house, PreSettlementSurplusAuctionHouse)
    assert isinstance(deployment_address, Address)

    surplus = geb.safe_engine.coin_balance(geb.accounting_engine.address)

    if surplus < geb.accounting_engine.surplus_buffer() + geb.accounting_engine.surplus_auction_amount_to_sell():
        # Create a SAFE with surplus
        print('Creating a SAFE with surplus')
        collateral = geb.collaterals['ETH-B']
        assert surplus_auction_house.auctions_started() == 0
        wrap_eth(geb, deployment_address, Wad.from_number(10))
        collateral.approve(deployment_address)
        assert collateral.adapter.join(deployment_address, Wad.from_number(10)).transact(
            from_address=deployment_address)
        wrap_modify_safe_collateralization(geb, collateral, deployment_address, delta_collateral=Wad.from_number(10),
                                           delta_debt=Wad.from_number(300))
        assert geb.tax_collector.tax_single(collateral.collateral_type).transact(from_address=deployment_address)
        surplus = geb.safe_engine.coin_balance(geb.accounting_engine.address)
        assert surplus > geb.accounting_engine.surplus_buffer() + geb.accounting_engine.surplus_auction_amount_to_sell()
    else:
        print(f'Surplus of {surplus} already exists; skipping SAFE creation')


def create_surplus_auction(geb: GfDeployment, deployment_address: Address, our_address: Address):
    assert isinstance(geb, GfDeployment)
    assert isinstance(deployment_address, Address)
    assert isinstance(our_address, Address)

    surplus_auction_house = geb.surplus_auction_house
    print(f"Before Surplus: {geb.safe_engine.coin_balance(geb.accounting_engine.address)}")
    create_surplus(geb, surplus_auction_house, deployment_address)
    print(f"After Surplus: {geb.safe_engine.coin_balance(geb.accounting_engine.address)}")

    # start surplus auction
    surplus = geb.safe_engine.coin_balance(geb.accounting_engine.address)
    assert surplus > geb.safe_engine.debt_balance(geb.accounting_engine.address) + \
                     geb.accounting_engine.surplus_auction_amount_to_sell() + \
                     geb.accounting_engine.surplus_buffer()
    assert (geb.safe_engine.debt_balance(geb.accounting_engine.address) - \
            geb.accounting_engine.debt_queue()) - geb.accounting_engine.total_on_auction_debt() == Rad(0)
    assert geb.accounting_engine.auction_surplus().transact()
    auction_id = surplus_auction_house.auctions_started()
    assert auction_id == 1
    assert len(surplus_auction_house.active_auctions()) == 1

    mint_prot(geb.prot, our_address, Wad.from_number(10))
    surplus_auction_house.approve(geb.prot.address, directly(from_address=our_address))
    bid_amount = Wad.from_number(0.001)
    assert geb.prot.balance_of(our_address) > bid_amount
    assert surplus_auction_house.increase_bid_size(surplus_auction_house.auctions_started(),
                                                   geb.accounting_engine.surplus_auction_amount_to_sell(),
                                                   bid_amount).transact(from_address=our_address)


def create_debt_auction(geb: GfDeployment, deployment_address: Address, our_address: Address):
    assert isinstance(geb, GfDeployment)
    assert isinstance(deployment_address, Address)
    assert isinstance(our_address, Address)

    debt_auction_house = geb.debt_auction_house
    print(f"Before Debt: {geb.safe_engine.debt_balance(geb.accounting_engine.address)}")
    if geb.accounting_engine.unqueued_unauctioned_debt() <= geb.accounting_engine.debt_auction_bid_size():
        create_debt(geb.web3, geb, our_address, deployment_address, geb.collaterals['ETH-A'])
    print(f"After Debt: {geb.safe_engine.debt_balance(geb.accounting_engine.address)}")

    # start debt auction
    auction_id = debt_auction_house.auctions_started()
    assert auction_id == 0
    assert len(debt_auction_house.active_auctions()) == 0
    assert geb.safe_engine.coin_balance(geb.accounting_engine.address) == Rad(0)
    assert geb.accounting_engine.auction_debt().transact()
    auction_id = debt_auction_house.auctions_started()
    assert auction_id == 1
    assert len(debt_auction_house.active_auctions()) == 1
    check_active_auctions(debt_auction_house)
    current_bid = debt_auction_house.bids(auction_id)

    amount_to_sell = Wad.from_number(0.000005)
    # current_bid.bid_amount = 0.001
    # current_bid.amount_to_sell = 0.0001
    debt_auction_house.approve(geb.safe_engine.address, approval_function=approve_safe_modification_directly(from_address=our_address))
    assert geb.safe_engine.safe_rights(our_address, debt_auction_house.address)

    collateral = geb.collaterals['ETH-A']
    wrap_eth(geb, our_address, Wad.from_number(1))
    collateral.approve(our_address)
    assert collateral.adapter.join(our_address, Wad.from_number(1)).transact(from_address=our_address)
    #web3.eth.defaultAccount = our_address.address
    wrap_modify_safe_collateralization(geb, collateral, our_address, delta_collateral=Wad.from_number(1), delta_debt=Wad.from_number(10))





    assert geb.safe_engine.coin_balance(our_address) >= current_bid.bid_amount
    decrease_sold_amount(debt_auction_house, auction_id, our_address, amount_to_sell, current_bid.bid_amount)
    current_bid = debt_auction_house.bids(auction_id)
    assert current_bid.high_bidder == our_address

def decrease_sold_amount(debt_auction_house: DebtAuctionHouse, id: int, address: Address, amount_to_sell: Wad, bid_amount: Rad):
    assert (isinstance(debt_auction_house, DebtAuctionHouse))
    assert (isinstance(id, int))
    assert (isinstance(amount_to_sell, Wad))
    assert (isinstance(bid_amount, Rad))

    assert debt_auction_house.contract_enabled() == 1

    current_bid = debt_auction_house.bids(id)
    assert current_bid.high_bidder != Address("0x0000000000000000000000000000000000000000")
    assert current_bid.bid_expiry > datetime.now().timestamp() or current_bid.bid_expiry == 0
    assert current_bid.auction_deadline > datetime.now().timestamp()

    assert bid_amount == current_bid.bid_amount
    assert Wad(0) < amount_to_sell < current_bid.amount_to_sell
    assert debt_auction_house.bid_decrease() * amount_to_sell <= current_bid.amount_to_sell

    assert debt_auction_house.decrease_sold_amount(id, amount_to_sell, bid_amount).transact(from_address=address)


def create_collateral_auction(geb: GfDeployment, deployment_address: Address, our_address: Address):
    assert isinstance(geb, GfDeployment)
    assert isinstance(our_address, Address)
    assert isinstance(deployment_address, Address)

    # Create a SAFE
    collateral = geb.collaterals['ETH-A']
    collateral_type = collateral.collateral_type
    wrap_eth(geb, deployment_address, Wad.from_number(1))
    collateral.approve(deployment_address)
    assert collateral.adapter.join(deployment_address, Wad.from_number(1)).transact(
        from_address=deployment_address)
    wrap_modify_safe_collateralization(geb, collateral, deployment_address, delta_collateral=Wad.from_number(1), delta_debt=Wad(0))
    delta_debt = max_delta_debt(geb, collateral, deployment_address) - Wad(1)
    wrap_modify_safe_collateralization(geb, collateral, deployment_address, delta_collateral=Wad(0), delta_debt=delta_debt)

    # Undercollateralize and bite the SAFE
    to_price = Wad(geb.web3.toInt(collateral.osm.read())) / Wad.from_number(2)
    set_collateral_price(geb, collateral, to_price)
    safe = geb.safe_engine.safe(collateral.collateral_type, deployment_address)
    collateral_type = geb.safe_engine.collateral_type(collateral_type.name)
    safe = Ray(safe.generated_debt) * geb.safe_engine.collateral_type(collateral_type.name).accumulated_rate <= Ray(safe.locked_collateral) * collateral_type.safety_price
    assert not safe
    assert geb.liquidation_engine.can_liquidate(collateral.collateral_type, SAFE(deployment_address))
    assert geb.liquidation_engine.liquidate_safe(collateral.collateral_type, SAFE(deployment_address)).transact()
    auction_id = collateral.collateral_auction_house.auctions_started()

    # Generate some system coin, bid on the collateral auction without covering all the debt
    wrap_eth(geb, our_address, Wad.from_number(10))
    collateral.approve(our_address)
    assert collateral.adapter.join(our_address, Wad.from_number(10)).transact(from_address=our_address)
    geb.web3.eth.defaultAccount = our_address.address
    wrap_modify_safe_collateralization(geb, collateral, our_address, delta_collateral=Wad.from_number(10), delta_debt=Wad.from_number(50))
    collateral.collateral_auction_house.approve(geb.safe_engine.address, approval_function=approve_safe_modification_directly())
    current_bid = collateral.collateral_auction_house.bids(auction_id)
    safe = geb.safe_engine.safe(collateral.collateral_type, our_address)
    assert Rad(safe.generated_debt) > current_bid.amount_to_raise
    bid_amount = Rad.from_number(6)
    if isinstance(collateral.collateral_auction_house, EnglishCollateralAuctionHouse):
        increase_bid_size(collateral.collateral_auction_house, auction_id, our_address, current_bid.amount_to_sell, bid_amount)
    elif isinstance(collateral.collateral_auction_house, FixedDiscountCollateralAuctionHouse):
        assert collateral.collateral_auction_house.get_collateral_bought(auction_id, Wad(bid_amount)).transact(from_address=our_address)
        assert collateral.collateral_auction_house.buy_collateral(auction_id, Wad(bid_amount)).transact(from_address=our_address)


def increase_bid_size(collateral_auction_house: EnglishCollateralAuctionHouse, id: int, address: Address, amount_to_sell: Wad, bid_amount: Rad):
        assert (isinstance(collateral_auction_house, EnglishCollateralAuctionHouse))
        assert (isinstance(id, int))
        assert (isinstance(amount_to_sell, Wad))
        assert (isinstance(bid_amount, Rad))

        current_bid = collateral_auction_house.bids(id)
        assert current_bid.high_bidder != Address("0x0000000000000000000000000000000000000000")
        assert current_bid.bid_expiry > datetime.now().timestamp() or current_bid.bid_expiry == 0
        assert current_bid.auction_deadline > datetime.now().timestamp()

        assert amount_to_sell == current_bid.amount_to_sell
        assert bid_amount <= current_bid.amount_to_raise
        assert bid_amount > current_bid.bid_amount
        assert (bid_amount >= Rad(collateral_auction_house.bid_increase()) * current_bid.bid_amount) or (bid_amount == current_bid.amount_to_raise)

        assert collateral_auction_house.increase_bid_size(id, amount_to_sell, bid_amount).transact(from_address=address)


def prepare_esm(geb: GfDeployment, deployment_address: Address):
    assert geb.esm is not None
    assert isinstance(geb.esm, ESM)
    assert isinstance(geb.esm.address, Address)
    assert geb.esm.trigger_threshold() > Wad(0)
    assert not geb.esm.settled()

    assert geb.prot.approve(geb.esm.address).transact(from_address=deployment_address)

    # Mint enough prot to call esm.shutdown
    mint_prot(geb.prot, deployment_address, geb.esm.trigger_threshold())
    assert geb.prot.balance_of(deployment_address) >= geb.esm.trigger_threshold()

    assert not geb.esm.settled()

def fire_esm(geb: GfDeployment, deployment_address: Address):
    assert geb.global_settlement.contract_enabled()
    assert geb.esm.shutdown().transact(from_address=deployment_address)
    assert geb.esm.settled()
    assert not geb.global_settlement.contract_enabled()

def print_out(testName: str):
    print("")
    print(f"{testName}")
    print("")

pytest.global_safes = []
pytest.global_auctions = {}

class TestSettlementKeeper:

    def test_check_deployment(self, geb: GfDeployment, keeper: SettlementKeeper):
        print_out("test_check_deployment")
        keeper.check_deployment()

    def test_get_underwater_safes(self, geb: GfDeployment, keeper: SettlementKeeper, guy_address: Address, our_address: Address):
        print_out("test_get_underwater_safes")

        previous_eth_price = open_underwater_safe(geb, geb.collaterals['ETH-A'], guy_address)
        open_safe(geb, geb.collaterals['ETH-C'], our_address)

        collateral_types = keeper.get_collateral_types()

        safes = keeper.get_underwater_safes(collateral_types)
        assert type(safes) is list
        assert all(isinstance(x, SAFE) for x in safes)
        assert len(safes) == 1
        assert safes[0].address.address == guy_address.address

        ## We've multiplied by a small Ray amount to counteract
        ## the residual dust (or lack thereof) in this step that causes
        ## create_debt_auction fail
        set_collateral_price(geb, geb.collaterals['ETH-A'], Wad(previous_eth_price * Ray.from_number(1.0001)))

        pytest.global_safes = safes

    def test_get_collateral_types(self, geb: GfDeployment, keeper: SettlementKeeper):
        print_out("test_get_collateral_types")

        collateral_types = keeper.get_collateral_types()
        assert type(collateral_types) is list
        assert all(isinstance(x, CollateralType) for x in collateral_types)
        deployment_collateral_types = [geb.safe_engine.collateral_type(key) for key in geb.collaterals.keys()]

        empty_deployment_collateral_types = list(filter(lambda l: geb.safe_engine.collateral_type(l.name).safe_debt == Wad(0), deployment_collateral_types))

        assert all(elem not in empty_deployment_collateral_types for elem in collateral_types)

    def test_active_auctions(self, geb: GfDeployment, keeper: SettlementKeeper, our_address: Address, other_address: Address, deployment_address: Address):
        print_out("test_active_auctions")
        print(f"debt balance: {geb.safe_engine.debt_balance(geb.accounting_engine.address)}")
        print(f"system coin: {geb.safe_engine.coin_balance(geb.accounting_engine.address)}")

        create_surplus_auction(geb, deployment_address, our_address)
        create_debt_auction(geb, deployment_address, other_address)

        # this collateral auction sets the collateral back to a price that makes the guy's vault underwater again.
        # 49 to make it underwater, and create_collateral_auction sets it to 33
        create_collateral_auction(geb, deployment_address, our_address)

        auctions = keeper.all_active_auctions()
        assert "collateral_auctions" in auctions
        assert "debt_auctions" in auctions
        assert "surplus_auctions" in auctions

        nobody = Address("0x0000000000000000000000000000000000000000")

        # All auctions active before terminations
        for collateral_type in auctions["collateral_auctions"].keys():
            # pyflex create_debt() doesn't bid/settle on collateral auction.
            # so one extra auction is present
            #assert len(auctions["collateral_auctions"][collateral_type]) == 1
            for auction in auctions["collateral_auctions"][collateral_type]:
                if isinstance(geb.collaterals[collateral_type].collateral_auction_house, EnglishCollateralAuctionHouse):
                    assert auction.id > 0
                    assert auction.bid_amount < auction.amount_to_raise
                    assert auction.high_bidder != nobody
                    #assert auction.high_bidder == our_address
                elif isinstance(geb.collaterals[collateral_type].collateral_auction_house, FixedDiscountCollateralAuctionHouse):
                    assert auction.amount_to_sell != Wad(0) and auction.amount_to_raise != Rad(0)

        assert len(auctions["surplus_auctions"]) == 1
        for auction in auctions["surplus_auctions"]:
            assert auction.id > 0
            assert auction.high_bidder != nobody
            assert auction.high_bidder == our_address

        assert len(auctions["debt_auctions"]) == 1
        for auction in auctions["debt_auctions"]:
            assert auction.id > 0
            assert auction.high_bidder != nobody
            assert auction.high_bidder == other_address

        pytest.global_auctions = auctions

    def test_check_settlement(self, geb: GfDeployment, keeper: SettlementKeeper, our_address: Address, other_address: Address):
        print_out("test_check_settlement")
        keeper.check_settlement()
        assert keeper.settlement_facilitated == False
        assert geb.global_settlement.contract_enabled() == 1
        prepare_esm(geb, our_address)
        fire_esm(geb, our_address)
        assert keeper.confirmations == 0
        for i in range(0,12):
            time_travel_by(geb.web3, 1)
            keeper.check_settlement()
        assert keeper.confirmations == 12

        keeper.check_settlement() # Facilitate processing period
        assert keeper.settlement_facilitated == True

        shutdown_time = geb.global_settlement.shutdown_time()
        shutdown_cooldown = geb.global_settlement.shutdown_cooldown()
        shutdown_time_in_unix = shutdown_time.replace(tzinfo=timezone.utc).timestamp()
        blockNumber = geb.web3.eth.blockNumber
        now = geb.web3.eth.getBlock(blockNumber).timestamp
        set_outstanding_coin_supply_time = shutdown_time_in_unix + shutdown_cooldown
        assert now >= set_outstanding_coin_supply_time

        time_travel_by(geb.web3, 1)

        keeper.check_settlement() # Facilitate cooldown (setting outstanding coin supply)
        assert keeper.settlement_facilitated == True

    def test_settlement_keeper(self, geb: GfDeployment, keeper: SettlementKeeper, our_address: Address, other_address: Address):
        print_out("test_settlement_keeper")
        collateral_types = keeper.get_collateral_types()
        safes = pytest.global_safes
        auctions = pytest.global_auctions

        for collateral_type in collateral_types:
            # Check if freeze_collateral_type(collateral_type) called on all collateral_types
            assert geb.global_settlement.final_coin_per_collateral_price(collateral_type) > Ray(0)

            # Check if calculate_cash_price(collateral_type) called on all collateral_types
            assert geb.global_settlement.collateral_cash_price(collateral_type) > Ray(0)

        # All underwater safes present before ES have been processed
        for i in safes:
            safe = geb.safe_engine.safe(i.collateral_type, i.address)
            assert safe.generated_debt == Wad(0)

        # All auctions active before settlement have been terminated prematurely
        for collateral_type in auctions["collateral_auctions"].keys():
            for auction in auctions["collateral_auctions"][collateral_type]:
                assert geb.collaterals[collateral_type].collateral_auction_house.bids(auction.id).amount_to_sell == Wad(0)

        for auction in auctions["surplus_auctions"]:
            assert geb.surplus_auction_house.bids(auction.id).amount_to_sell == Rad(0)

        for auction in auctions["debt_auctions"]:
            assert geb.debt_auction_house.bids(auction.id).amount_to_sell == Wad(0)

        # setOutstandingCoinSupply() has been called
        assert geb.global_settlement.outstanding_coin_supply() != Rad(0)
