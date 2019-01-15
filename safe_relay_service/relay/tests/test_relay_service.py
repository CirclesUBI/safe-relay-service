import logging

from hexbytes import HexBytes

from gnosis.eth.constants import NULL_ADDRESS
from gnosis.eth.contracts import get_safe_contract
from gnosis.eth.tests.utils import deploy_example_erc20
from gnosis.eth.utils import get_eth_address_with_key
from gnosis.safe.safe_service import (GasPriceTooLow, InvalidMasterCopyAddress,
                                      InvalidRefundReceiver,
                                      NotEnoughFundsForMultisigTx)
from gnosis.safe.tests.factories import deploy_safe, generate_safe
from gnosis.safe.tests.test_safe_service import GAS_PRICE, TestSafeService

from safe_relay_service.gas_station.gas_station import GasStationMock
from safe_relay_service.tokens.tests.factories import TokenFactory

from ..relay_service import (InvalidGasToken, RefundMustBeEnabled,
                             RelayService, RelayServiceProvider)

logger = logging.getLogger(__name__)


class TestRelayService(TestSafeService):

    def test_relay_provider_singleton(self):
        relay_service1 = RelayServiceProvider()
        relay_service2 = RelayServiceProvider()
        self.assertEqual(relay_service1, relay_service2)

    def test_estimate_safe_creation(self):
        gas_station = GasStationMock()
        gas_price = gas_station.get_gas_prices().fast
        relay_service = RelayService(self.safe_service, gas_station)

        number_owners = 4
        payment_token = None
        safe_creation_estimate = relay_service.estimate_safe_creation(number_owners, payment_token)
        self.assertGreater(safe_creation_estimate.gas, 0)
        self.assertEqual(safe_creation_estimate.gas_price, gas_price)
        self.assertGreater(safe_creation_estimate.payment, 0)
        estimated_payment = safe_creation_estimate.payment

        number_owners = 8
        payment_token = None
        safe_creation_estimate = relay_service.estimate_safe_creation(number_owners, payment_token)
        self.assertGreater(safe_creation_estimate.gas, 0)
        self.assertEqual(safe_creation_estimate.gas_price, gas_price)
        self.assertGreater(safe_creation_estimate.payment, estimated_payment)

        payment_token = get_eth_address_with_key()[0]
        with self.assertRaisesMessage(InvalidGasToken, payment_token):
            relay_service.estimate_safe_creation(number_owners, payment_token)

        erc20 = deploy_example_erc20(self.w3, 1000, self.w3.eth.accounts[0])
        number_owners = 4
        payment_token = erc20.address
        payment_token_db = TokenFactory(address=payment_token, fixed_eth_conversion=0.1)
        safe_creation_estimate = relay_service.estimate_safe_creation(number_owners, payment_token)
        self.assertGreater(safe_creation_estimate.gas, 0)
        self.assertEqual(safe_creation_estimate.gas_price, gas_price)
        self.assertGreater(safe_creation_estimate.payment, estimated_payment)

    def test_send_multisig_tx(self):
        gas_station = GasStationMock()
        relay_service = RelayService(self.safe_service, gas_station)
        # Create Safe
        w3 = self.w3
        funder = w3.eth.accounts[0]
        owners_with_keys = [get_eth_address_with_key(), get_eth_address_with_key()]
        # Signatures must be sorted!
        owners_with_keys.sort(key=lambda x: x[0].lower())
        owners = [x[0] for x in owners_with_keys]
        keys = [x[1] for x in owners_with_keys]
        threshold = len(owners_with_keys)

        safe_creation = generate_safe(relay_service, owners=owners, threshold=threshold)
        my_safe_address = deploy_safe(w3, safe_creation, funder)

        # The balance we will send to the safe
        safe_balance = w3.toWei(0.01, 'ether')

        # Send something to the owner[0], who will be sending the tx
        owner0_balance = safe_balance
        w3.eth.waitForTransactionReceipt(w3.eth.sendTransaction({
            'from': funder,
            'to': owners[0],
            'value': owner0_balance
        }))

        my_safe_contract = get_safe_contract(w3, my_safe_address)

        to = funder
        value = safe_balance // 2
        data = HexBytes('')
        operation = 0
        safe_tx_gas = 100000
        data_gas = 300000
        gas_price = gas_station.get_gas_prices().standard
        gas_token = NULL_ADDRESS
        refund_receiver = NULL_ADDRESS
        nonce = relay_service.retrieve_nonce(my_safe_address)
        safe_multisig_tx_hash = relay_service.get_hash_for_safe_tx(safe_address=my_safe_address,
                                                                   to=to,
                                                                   value=value,
                                                                   data=data,
                                                                   operation=operation,
                                                                   safe_tx_gas=safe_tx_gas,
                                                                   data_gas=data_gas,
                                                                   gas_price=gas_price,
                                                                   gas_token=gas_token,
                                                                   refund_receiver=refund_receiver,
                                                                   nonce=nonce)

        # Just to make sure we are not miscalculating tx_hash
        contract_multisig_tx_hash = my_safe_contract.functions.getTransactionHash(
            to,
            value,
            data,
            operation,
            safe_tx_gas,
            data_gas,
            gas_price,
            gas_token,
            refund_receiver,
            nonce).call()

        self.assertEqual(safe_multisig_tx_hash, contract_multisig_tx_hash)

        signatures = [w3.eth.account.signHash(safe_multisig_tx_hash, private_key) for private_key in keys]
        signature_pairs = [(s['v'], s['r'], s['s']) for s in signatures]
        signatures_packed = relay_service.signatures_to_bytes(signature_pairs)

        # {bytes32 r}{bytes32 s}{uint8 v} = 65 bytes
        self.assertEqual(len(signatures_packed), 65 * len(owners))

        # Recover key is now a private function
        # Make sure the contract retrieves the same owners
        # for i, owner in enumerate(owners):
        #    recovered_owner = my_safe_contract.functions.recoverKey(safe_multisig_tx_hash, signatures_packed, i).call()
        #    self.assertEqual(owner, recovered_owner)

        self.assertTrue(relay_service.check_hash(safe_multisig_tx_hash, signatures_packed, owners))

        # Check owners are the same
        contract_owners = my_safe_contract.functions.getOwners().call()
        self.assertEqual(set(contract_owners), set(owners))
        self.assertEqual(w3.eth.getBalance(owners[0]), owner0_balance)

        with self.assertRaises(NotEnoughFundsForMultisigTx):
            relay_service.send_multisig_tx(
                my_safe_address,
                to,
                value,
                data,
                operation,
                safe_tx_gas,
                data_gas,
                gas_price,
                gas_token,
                refund_receiver,
                signatures_packed,
                tx_sender_private_key=keys[0]
            )

        # Send something to the safe
        w3.eth.waitForTransactionReceipt(w3.eth.sendTransaction({
            'from': funder,
            'to': my_safe_address,
            'value': safe_balance
        }))

        bad_refund_receiver = get_eth_address_with_key()[0]
        with self.assertRaises(InvalidRefundReceiver):
            relay_service.send_multisig_tx(
                my_safe_address,
                to,
                value,
                data,
                operation,
                safe_tx_gas,
                data_gas,
                gas_price,
                gas_token,
                bad_refund_receiver,
                signatures_packed,
                tx_sender_private_key=keys[0]
            )

        invalid_gas_price = 0
        with self.assertRaises(RefundMustBeEnabled):
            relay_service.send_multisig_tx(
                my_safe_address,
                to,
                value,
                data,
                operation,
                safe_tx_gas,
                data_gas,
                invalid_gas_price,
                gas_token,
                refund_receiver,
                signatures_packed,
                tx_sender_private_key=keys[0]
            )

        with self.assertRaises(GasPriceTooLow):
            relay_service.send_multisig_tx(
                my_safe_address,
                to,
                value,
                data,
                operation,
                safe_tx_gas,
                data_gas,
                gas_station.get_gas_prices().standard - 1,
                gas_token,
                refund_receiver,
                signatures_packed,
                tx_sender_private_key=keys[0]
            )

        owner0_balance = w3.eth.getBalance(owners[0])

        sent_tx_hash, tx = relay_service.send_multisig_tx(
            my_safe_address,
            to,
            value,
            data,
            operation,
            safe_tx_gas,
            data_gas,
            gas_price,
            gas_token,
            refund_receiver,
            signatures_packed,
            tx_sender_private_key=keys[0]
        )

        tx_receipt = w3.eth.waitForTransactionReceipt(sent_tx_hash)
        self.assertTrue(tx_receipt['status'])
        owner0_new_balance = w3.eth.getBalance(owners[0])
        gas_used = tx_receipt['gasUsed']
        gas_cost = gas_used * GAS_PRICE
        estimated_payment = (data_gas + gas_used) * gas_price
        real_payment = owner0_new_balance - owner0_balance - gas_cost
        # Estimated payment will be bigger, because it uses all the tx gas. Real payment only uses gas left
        # in the point of calculation of the payment, so it will be slightly lower
        self.assertGreater(estimated_payment, real_payment)
        self.assertGreater(real_payment, 0)
        self.assertTrue(owner0_new_balance > owner0_balance - tx['gas'] * GAS_PRICE)
        self.assertEqual(relay_service.retrieve_nonce(my_safe_address), 1)
