from django.conf import settings

from ethereum.utils import check_checksum

from gnosis.eth import EthereumClient
from gnosis.eth.constants import NULL_ADDRESS

from . import TransactionServiceProvider


class CirclesService:

    def __init__(self, ethereum_client: EthereumClient, gas_price: int = 1):
        """
        :param ethereum_client:
        :param gas_price: Gas price when paid in Circles token
        """
        self.ethereum_client = ethereum_client
        self.gas_price = gas_price

    def estimate_gas_price(self) -> int:
        """
        Returns the estimate gas price for transactions with Circles token
        :return: Gas price
        """
        return self.gas_price

    def estimate_signup_gas(self, safe_address: str, gas_token: str = NULL_ADDRESS) -> int:
        """
        Estimates gas costs of Circles token deployment method
        :param gas_token:
        """
        value = 0
        operation = 0
        # Tx data from Circles Token contract signup method
        data = ("0x519c6377000000000000000000000000000000000000000000000"
                "0000000000000000020000000000000000000000000000000000000"
                "0000000000000000000000000007436972636c65730000000000000"
                "0000000000000000000000000000000000000")
        transaction_estimation = TransactionServiceProvider().estimate_tx(
            safe_address,
            settings.CIRCLES_HUB_ADDRESS,
            value,
            data,
            operation,
            gas_token
        )
        return (
            transaction_estimation.safe_tx_gas + transaction_estimation.base_gas
        ) * transaction_estimation.gas_price

    def pack_address(self, token_address: str) -> str:
        """
        Prepares Circles token address
        :return: packed string
        """
        assert check_checksum(token_address)
        return "000000000000000000000000" + token_address[2:]

    def is_circles_token(self, token_address: str) -> bool:
        """
        Checks if given Token is known by Circles Hub
        :return: true if Circles Token otherwise false
        """
        call_args = {
            'to': settings.CIRCLES_HUB_ADDRESS,
            # Calling tokenToUser mapping of Hub contract;
            'data': '0xa18b506b' + self.pack_address(token_address)
        }
        return self.ethereum_client.w3.eth.call(call_args) != NULL_ADDRESS

    def is_token_deployed(self, safe_address: str) -> bool:
        """
        Checks if Safe address has a deployed Token connected to it
        :return: true if Circles Token exists otherwise false
        """
        call_args = {
            'to': settings.CIRCLES_HUB_ADDRESS,
            # Calling userToToken mapping of Hub contract;
            # @TODO: Insert correct data
            'data': '0x' + self.pack_address(safe_address)
        }
        return self.ethereum_client.w3.eth.call(call_args) != NULL_ADDRESS
