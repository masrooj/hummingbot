import logging
import os
import subprocess
import tempfile
from decimal import ROUND_DOWN, Decimal
from typing import Any, Dict, List, Optional

from pydantic import Field

from hummingbot.client.config.config_data_types import BaseClientModel, ClientFieldData
from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.connector.derivative.position import Position
from hummingbot.connector.utils import split_hb_trading_pair
from hummingbot.core.data_type.common import OrderType, PositionAction, PriceType, TradeType
from hummingbot.core.data_type.limit_order import LimitOrder
from hummingbot.core.data_type.order_candidate import OrderCandidate
from hummingbot.core.event.events import (
    BuyOrderCompletedEvent,
    BuyOrderCreatedEvent,
    MarketOrderFailureEvent,
    OrderCancelledEvent,
    OrderFilledEvent,
    SellOrderCompletedEvent,
    SellOrderCreatedEvent,
)
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase


class ManualFutureLeverageConfig(BaseClientModel):
    script_file_name: str = Field(default_factory=lambda: os.path.basename(__file__))
    trading_pair: str = Field(
        "ETH-USDT",
        client_data=ClientFieldData(
            prompt_on_new=True, prompt=lambda mi: "Enter the exchange where the bot will trade:"
        ),
    )
    exchange: str = Field(
        "binance_perpetual_testnet",
        client_data=ClientFieldData(
            prompt_on_new=True, prompt=lambda mi: "Enter the exchange where the bot will trade:"
        ),
    )
    base_order_amount: Decimal = Field(
        0.025,
        client_data=ClientFieldData(
            prompt_on_new=True, prompt=lambda mi: "Enter the base order amount (denominated in base asset):"
        ),
    )
    leverage: Decimal = Field(
        1.0, client_data=ClientFieldData(prompt_on_new=True, prompt=lambda mi: "Enter the leverage :")
    )

    is_stop_loss_activated: bool = Field(
        False, client_data=ClientFieldData(prompt_on_new=True, prompt=lambda mi: "Enter the stop loss activated :")
    )
    stop_loss_percentage: Decimal = Field(
        0.00, client_data=ClientFieldData(prompt_on_new=True, prompt=lambda mi: "Enter the stop_loss_percentage :")
    )
    callback_rate: Decimal = Field(
        0.00, client_data=ClientFieldData(prompt_on_new=True, prompt=lambda mi: "Enter the callback rate :")
    )

    is_take_profit_activated: bool = Field(
        False, client_data=ClientFieldData(prompt_on_new=True, prompt=lambda mi: "Enter the is_take_profit_activated :")
    )
    is_take_profit_price_selected: bool = Field(
        False,
        client_data=ClientFieldData(prompt_on_new=True, prompt=lambda mi: "Enter the is_take_profit_price_selected :"),
    )
    take_profit_price: Decimal = Field(
        0.00, client_data=ClientFieldData(prompt_on_new=True, prompt=lambda mi: "Enter the take_profit_price :")
    )
    take_profit_percentage: Decimal = Field(
        0.00, client_data=ClientFieldData(prompt_on_new=True, prompt=lambda mi: "Enter the take_profit_percentage :")
    )

    price_type: str = Field(
        "last",
        client_data=ClientFieldData(prompt_on_new=True, prompt=lambda mi: "Enter the price type to use (mid or last):"),
    )

    api_key: str = Field(
        "api key", client_data=ClientFieldData(prompt_on_new=True, prompt=lambda mi: "Enter the api key :")
    )
    api_secret: str = Field(
        "api secret", client_data=ClientFieldData(prompt_on_new=True, prompt=lambda mi: "Enter the api secret :")
    )
    base_url: str = Field(
        "https://testnet.binancefuture.com",
        client_data=ClientFieldData(prompt_on_new=True, prompt=lambda mi: "Enter the binance api base url :"),
    )
    user_id: str = Field(
        "user id", client_data=ClientFieldData(prompt_on_new=True, prompt=lambda mi: "Enter the User Id :")
    )
    bot_id: str = Field(
        "bot id", client_data=ClientFieldData(prompt_on_new=True, prompt=lambda mi: "Enter the bot id :")
    )
    trade_type: str = Field(
        "trade_type", client_data=ClientFieldData(prompt_on_new=True, prompt=lambda mi: "Enter the trade type :")
    )
    session_id: str = Field(
        "session id", client_data=ClientFieldData(prompt_on_new=True, prompt=lambda mi: "Enter the session id :")
    )

    exit_price: Decimal = Field(
        0.00, client_data=ClientFieldData(prompt_on_new=True, prompt=lambda mi: "Enter the exit_price :")
    )
    stop_price: Decimal = Field(
        0.00, client_data=ClientFieldData(prompt_on_new=True, prompt=lambda mi: "Enter the stop_price :")
    )
    exit_type: str = Field(
        "exit type", client_data=ClientFieldData(prompt_on_new=True, prompt=lambda mi: "Enter the exit_type :")
    )
    commands_count: int = Field(
        0, client_data=ClientFieldData(prompt_on_new=True, prompt=lambda mi: "Enter the commands count :")
    )

    limit_order_percentage: Decimal = Field(
        0.001, client_data=ClientFieldData(prompt_on_new=True, prompt=lambda mi: "Enter the limit order percentage : ")
    )
    order_refresh_time: int = Field(
        10,
        client_data=ClientFieldData(
            prompt_on_new=True, prompt=lambda mi: "Enter the order refresh time (in seconds): "
        ),
    )
    limit_order_market_attempt: int = Field(
        1, client_data=ClientFieldData(prompt_on_new=True, prompt=lambda mi: "Enter the limit order market attempt: ")
    )


class ManualFutureLeverage(ScriptStrategyBase):

    price_source = PriceType.MidPrice
    markets = {"binance_perpetual_testnet": {"BTC-USDT"}}

    _create_timestamp = 0
    _partially_filled_create_timestamp = 0

    _base_order_price = None
    _base_order_id = None
    _base_order_filled_token = None
    _base_order_completed = False

    _stop_loss_price = None
    _stop_loss_order_id = None
    _take_profit_price = None
    _take_profit_order_id = None

    _base_asset_balance_in_connector = None
    _binance_client = None
    _is_binance_package_installed = None
    _is_requests_package_installed = None

    _latest_config_data = None
    _main_config_data = None

    _stop_loss_percentage_adjustment = 0.002
    _stop_loss_order_id_by_market = None
    _exit_order_id = None

    _is_cancelled = False

    _base_order_attempt = 1
    _exit_limit_order_attempt = 1
    _is_exit_limit_order_started = False
    _is_exit_market_order_started = False

    @classmethod
    def init_markets(cls, config: ManualFutureLeverageConfig):
        cls.markets = {config.exchange: {config.trading_pair}}
        cls.price_source = (
            PriceType.LastTrade
        )  # PriceType.MidPrice #PriceType.LastTrade if config.price_type == "last" else PriceType.MidPrice

    def __init__(self, connectors: Dict[str, ConnectorBase], config: ManualFutureLeverageConfig = None):
        super().__init__(connectors, config)
        self.config = config

    def loggingMessage(self, message):
        self.logger().info(message)
        self.notify_hb_app_with_timestamp(message)

    def on_tick(self):

        if self.config is None:
            self.loggingMessage(f"markets is  {self.markets}")
            self.loggingMessage(f"config is  {self.config}")

        if self.config.api_key is None:
            self.loggingMessage(f"api key is  {self.config.api_key}")
            self.loggingMessage(f"api secret is  {self.config.api_secret}")
            self.loggingMessage(f"base url is  {self.config.base_url}")

        # # BINANCE
        # if self._is_binance_package_installed is None:
        #     env_name = "hummingbot"
        #     package_name = "binance-futures-connector"
        #     self.install_package_in_conda_env(env_name, package_name)
        # # else :
        # #     self.loggingMessage(f"Package {self._is_binance_package_installed} already installed")

        # # REQUESTS
        # if self._is_requests_package_installed is None:
        #     env_name = "hummingbot"
        #     package_name = "requests"
        #     self.install_package_in_conda_env(env_name, package_name)
        # # else :
        # #     self.loggingMessage(f"Package {self.is_mongo_package_installed} already installed")

        # update the latest configuration values
        self.loggingMessage("****** config data fetching process started ***** ")
        # self.get_main_config_data_via_api()
        # self.get_latest_config_data_via_api()
        self.loggingMessage("****** config data fetching process started ***** ")

        if self._create_timestamp <= self.current_timestamp:
            self.timely_order_execution()
            self._create_timestamp = self.config.order_refresh_time + self.current_timestamp

        self.initial_order_execution()

    def install_package_in_conda_env(self, env_name, package_name):
        # Create a temporary shell script file
        script = f"""#!/bin/bash
                source activate {env_name}
                pip install {package_name}
                """

        # Create a temporary file and write the script to it
        temp_script = tempfile.NamedTemporaryFile(mode="w", delete=False)
        temp_script.write(script)
        temp_script.close()

        try:
            # Execute the temporary shell script
            subprocess.check_call(["bash", temp_script.name])
        except subprocess.CalledProcessError as e:
            self.loggingMessage(f"install package in conda env => An error occurred while running the script: {e}")

            if package_name == "binance-futures-connector":
                self._is_binance_package_installed = None

            if package_name == "requests":
                self._is_requests_package_installed = None

        else:
            self.loggingMessage(
                f"install package in conda env => Package '{package_name}' installed successfully in environment '{env_name}'."
            )

            if package_name == "binance-futures-connector":
                self._is_binance_package_installed = package_name

            if package_name == "requests":
                self._is_requests_package_installed = package_name

        # Clean up the temporary script file

        self.loggingMessage(f"install package in conda env => Package '{package_name}' setup successfully.")

        os.unlink(temp_script.name)

    def timely_order_execution(self):
        try:
            # if the base order filled position manually sell then need to cancel all existing orders
            if self._base_order_id is not None:
                active_position = self.get_active_position(self.config.trading_pair)
                self.loggingMessage(f"active_position: {active_position}")

                open_orders_in_exchange: List[LimitOrder] = self.get_active_orders(self.config.exchange)
                self.loggingMessage(f"open orders in exchange : {open_orders_in_exchange}")

                active_orders = [
                    trade for trade in open_orders_in_exchange if (trade.trading_pair == self.config.trading_pair)
                ]
                self.loggingMessage(f"active_orders : {active_orders}")

                if len(active_orders) > 0 and active_position is None:
                    # base order re initiation if not fill in specific time

                    if not self._base_order_completed:
                        # if the base order not filled within certain seconds then cancel the base order and create new one ( after base order place price moving upwards will keep base order not filling)
                        buy_sell_orders = [
                            trade
                            for trade in active_orders
                            if (trade.is_buy and self.config.trade_type == "BUY")
                            or (not trade.is_buy and self.config.trade_type == "SELL")
                        ]

                        self.loggingMessage(f"buy_sell_orders : {buy_sell_orders}")

                        if len(buy_sell_orders) > 0:
                            for buy_sell_order in buy_sell_orders:
                                if buy_sell_order.client_order_id == self._base_order_id:
                                    self.loggingMessage(f"canceling base order id : {buy_sell_order.client_order_id}")
                                    self.cancel_base_order()

                if (
                    self._base_order_id is not None
                    and active_position is not None
                    and self._base_order_completed
                    and abs(active_position.amount)
                    >= self.truncate_decimal(
                        (float(self._base_order_filled_token) * float(active_position.leverage)), 0
                    )
                ):

                    self.loggingMessage(f"limit ==> _exit_order_id {self._exit_order_id}")
                    self.loggingMessage(
                        f"order size ==> {self.truncate_decimal((float(self._base_order_filled_token) * float(active_position.leverage)), 0)}"
                    )

                    if self._exit_order_id is not None:
                        self.cancel_exit_order()

        except Exception as e:
            self.loggingMessage(f"Error in timely order execution : {e}")

    def initial_order_execution(self):
        try:

            # check current asset to check can make orders which means we have reasonable amount in bankroll
            quote_asset, base_asset = split_hb_trading_pair(trading_pair=self.config.trading_pair)
            base_asset_balance = self.get_asset_balance_base_asset(base_asset)
            if base_asset_balance is not None:
                self._base_asset_balance_in_connector = base_asset_balance
            # self.loggingMessage(f"Connectors base asset balance {self._base_asset_balance_in_connector} ")

            if self._base_order_id is not None:

                active_position = self.get_active_position(self.config.trading_pair)
                self.loggingMessage(f"active_position: {active_position}")

                open_orders_in_exchange: List[LimitOrder] = self.get_active_orders(self.config.exchange)
                self.loggingMessage(f"open orders in exchange : {open_orders_in_exchange}")

                active_orders = [
                    trade for trade in open_orders_in_exchange if (trade.trading_pair == self.config.trading_pair)
                ]
                self.loggingMessage(f"active_orders : {active_orders}")

                if len(active_orders) > 0 and active_position is None:

                    # cancelling tp/sl order if position is gone and remains any active orders (user manually close the position in binance)
                    tp_sl_orders = [
                        trade
                        for trade in active_orders
                        if (trade.is_buy and self.config.trade_type == "SELL")
                        or (not trade.is_buy and self.config.trade_type == "BUY")
                    ]

                    self.loggingMessage(f"tp_sl_orders : {tp_sl_orders}")

                    if len(tp_sl_orders) > 0:
                        for tp_sl_order in tp_sl_orders:
                            if tp_sl_order.client_order_id == self._stop_loss_order_id:
                                self.cancel_stop_loss_order()

                            elif tp_sl_order.client_order_id == self._take_profit_order_id:
                                self.cancel_take_profit_order()

                            elif tp_sl_order.client_order_id == self._exit_order_id:
                                self.cancel_exit_order()

                        # need to check after order cancelled need to reset everything or not

                elif (
                    len(active_orders) == 0
                    and active_position is not None
                    and self._base_order_completed
                    and abs(active_position.amount)
                    >= self.truncate_decimal(
                        (float(self._base_order_filled_token) * float(active_position.leverage)), 0
                    )
                    and not self._is_cancelled
                ):

                    self.loggingMessage("Sell order creation ==> Take profit")
                    self.loggingMessage(
                        f"order size ==> {self.truncate_decimal((float(self._base_order_filled_token) * float(active_position.leverage)), 0)}"
                    )

                    # after base order filled create tp and sl orders where tp created instantly

                    # if self.config.is_stop_loss_activated and self._stop_loss_order_id is None:
                    #     self.loggingMessage("Stop-loss order creation.")
                    #     self.create_stop_loss_order()

                    if self.config.is_take_profit_activated and self._take_profit_order_id is None:
                        self.loggingMessage("Take-profit order creation.")
                        self.create_take_profit_order()

                elif (
                    len(active_orders) > 0
                    and active_position is not None
                    and self._base_order_completed
                    and abs(active_position.amount)
                    >= self.truncate_decimal(
                        (float(self._base_order_filled_token) * float(active_position.leverage)), 0
                    )
                    and not self._is_cancelled
                ):

                    self.loggingMessage("Sell order creation ==> Stop loss")
                    self.loggingMessage(
                        f"order size ==> {self.truncate_decimal((float(self._base_order_filled_token) * float(active_position.leverage)), 0)}"
                    )

                    # after base order filled create tp and sl orders where sl created after stop price reached so check each seconds
                    if self.config.is_stop_loss_activated and self._stop_loss_order_id is None:

                        self.loggingMessage("Stop-loss order creation.")
                        self.create_stop_loss_order()

                    elif self._stop_loss_order_id is not None:
                        sl_orders = [
                            trade
                            for trade in active_orders
                            if (trade.is_buy and self.config.trade_type == "SELL")
                            or (not trade.is_buy and self.config.trade_type == "BUY")
                        ]

                        self.loggingMessage(f"sl_orders : {sl_orders}")

                        if len(sl_orders) > 0:
                            for sl_order in sl_orders:
                                if sl_order.client_order_id == self._stop_loss_order_id:

                                    current_price = self.get_current_price()

                                    if (
                                        sl_order.price > current_price
                                    ):  # current market price is lower than our stop loss

                                        if self.config.trade_type == TradeType.BUY.name:
                                            self._stop_loss_order_id_by_market = self.execute_order(
                                                self._stop_loss_price,
                                                self._base_order_filled_token,
                                                TradeType.SELL.name,
                                                OrderType.MARKET,
                                            )
                                        elif self.config.trade_type == TradeType.SELL.name:
                                            self._stop_loss_order_id_by_market = self.execute_order(
                                                self._stop_loss_price,
                                                self._base_order_filled_token,
                                                TradeType.BUY.name,
                                                OrderType.MARKET,
                                            )
                                        else:
                                            self.loggingMessage(
                                                f"cannot create stop loss order in market price trading type : {self.config.trade_type}"
                                            )

                self.loggingMessage(
                    f"Base order created at {self._base_order_price} USDT. Stop-loss set at {self._stop_loss_price} USDT and take-profit set at {self._take_profit_price} USDT."
                )

                # re create exit order
                if self._exit_order_id is None and self._is_exit_limit_order_started:
                    self.loggingMessage("exit order => limit")
                    if self._exit_limit_order_attempt <= self.config.limit_order_market_attempt:
                        self.loggingMessage(f"exit order ==> limit attempt  : {self._exit_limit_order_attempt}")
                        # base process
                        self.exit_order(False)
                        self._exit_limit_order_attempt += 1
                    else:
                        self.loggingMessage("exit order => market after limit")
                        self.exit_order(True)

                if self._exit_order_id is None and self._is_exit_market_order_started:
                    self.loggingMessage("exit order => market")
                    self.exit_order(True)

            elif self._base_order_id is None:
                if self._base_order_attempt <= self.config.limit_order_market_attempt:
                    self.loggingMessage(f"Base order limit attempt  : {self._base_order_attempt}")
                    # base process
                    self.base_order_start_process(False)
                    self._base_order_attempt += 1
                else:
                    self.loggingMessage("Base order market after limit")
                    self.base_order_start_process(True)

        except Exception as e:
            self.loggingMessage(f"Error in initial order execution : {e}")

    def base_order_start_process(self, is_market: bool):

        self.loggingMessage("****** base order start process started ***** ")
        current_price = self.get_current_price()

        order_id, base_price, base_order_token = self.base_order(
            current_price, self.config.base_order_amount, is_market
        )
        self._base_order_id = order_id
        self._base_order_price = base_price
        self._base_order_filled_token = base_order_token
        self.loggingMessage("****** base order start process end  ***** ")

    def base_order(self, current_price: Decimal, base_order_amount: Decimal, is_market: bool) -> str:
        try:
            # Calculate base price a few points under the current price
            self.loggingMessage(f"current price: {current_price} ")

            base_order_price = current_price  # variable initialization
            order_type = OrderType.LIMIT

            if self.config.trade_type == TradeType.BUY.name:
                base_order_price = self.calculate_limit_order_price(current_price, False)

            elif self.config.trade_type == TradeType.SELL.name:
                base_order_price = self.calculate_limit_order_price(
                    current_price, True
                )  # calculated amount if its limit and create based on tp placing

            if is_market:
                base_order_price = current_price  # base current price if its a market or  once base_order_price updated due to  self.config.trade_type then need to rest it its market
                order_type = OrderType.MARKET

            # if self.config.stop_price > Decimal(0):
            #     order_type = OrderType.STOP_MARKET

            self.loggingMessage(f"base order price: {base_order_price} for is market {is_market}")
            self.loggingMessage(f"base order amount: {base_order_amount} ")

            base_order_token = Decimal(base_order_amount) / Decimal(base_order_price)
            self.loggingMessage(f"base order token: {base_order_token} ")
            self.loggingMessage(f"self.config.trade_type: {self.config.trade_type} ")

            order_id = self.execute_order(base_order_price, base_order_token, self.config.trade_type, order_type, self.config.stop_price)
            self.loggingMessage(f"base order_id return : {order_id} ")

            return order_id, base_order_price, base_order_token

        except Exception as e:
            self.loggingMessage(f"Error in base_order : {e}")

    def order_completed(self, order_id: str):
        # if order_id == self._base_order_id:
        #     # Base order is filled, and has a position only  set the stop-loss and take-profit prices
        #     active_position =self.get_active_position(self.config.trading_pair)
        #     self.loggingMessage(f"active_position: {active_position}")

        #     if  active_position is not None :
        #         if self.config.is_stop_loss_activated :
        #             self.loggingMessage("Stop-loss order creation.")
        #             self.create_stop_loss_order()

        #         if self.config.is_take_profit_activated :
        #             self.loggingMessage("Take-profit order creation.")
        #             self.create_take_profit_order()

        #         self.loggingMessage(f"Base order filled at {self._base_order_price} USDT. Stop-loss set at {self._stop_loss_price} USDT and take-profit set at {self._take_profit_price} USDT.")

        # elif order_id == self._stop_loss_order_id:
        #     # Stop-loss order completed, cancel the take-profit order
        #     self.loggingMessage("Stop-loss order completed. Cancelling take-profit order.")
        #     self.cancel_take_profit_order()

        # elif order_id == self._take_profit_order_id:
        #     # Take-profit order completed, cancel the stop-loss order
        #     self.loggingMessage("Take-profit order completed. Cancelling stop-loss order.")
        #     self.cancel_stop_loss_order()

        if order_id == self._base_order_id:
            self._base_order_completed = True

        if order_id == self._stop_loss_order_id:
            # Stop-loss order completed, cancel the take-profit order
            self.loggingMessage("Stop-loss order completed. Cancelling take-profit order.")
            self.cancel_take_profit_order()

        elif order_id == self._take_profit_order_id:
            # Take-profit order completed, cancel the stop-loss order
            self.loggingMessage("Take-profit order completed. Cancelling stop-loss order.")
            self.cancel_stop_loss_order()
        # elif order_id=self._exit_order_id:
        # self._is_exit_limit_order_started = False
        # self._is_exit_market_order_started = False

    def on_order_cancelled(self, order_id: str):
        # Reset order IDs when an order is cancelled
        if order_id == self._stop_loss_order_id:
            self._stop_loss_order_id = None
            self.loggingMessage("Stop-loss order was cancelled.")

        elif order_id == self._take_profit_order_id:
            self._take_profit_order_id = None
            self.loggingMessage("Take-profit order was cancelled.")

        elif order_id == self._exit_order_id:
            self._exit_order_id = None
            self.loggingMessage("Exit order was cancelled.")

        elif order_id == self._base_order_id:
            self._base_order_id = None
            self.loggingMessage("Base order was cancelled.")

    def cancel_stop_loss_order(self):
        if self._stop_loss_order_id:
            self.cancel(self.config.exchange, self.config.trading_pair, self._stop_loss_order_id)
            # self._stop_loss_order_id = None

    def cancel_take_profit_order(self):
        if self._take_profit_order_id:
            self.cancel(self.config.exchange, self.config.trading_pair, self._take_profit_order_id)
            # self._take_profit_order_id = None

    def cancel_exit_order(self):
        if self._exit_order_id:
            self.cancel(self.config.exchange, self.config.trading_pair, self._exit_order_id)
            # self._exit_order_id = None

    def cancel_base_order(self):
        if self._base_order_id:
            self.cancel(self.config.exchange, self.config.trading_pair, self._base_order_id)

    def create_stop_loss_order(self):
        self._stop_loss_price = self.calculate_stop_loss_price(self._base_order_price, self.config.trade_type)
        current_price = self.get_current_price()  # last trade price
        # # this minimal_stop_price for check if the price i near to stop loss then place the order , once if put order after passing then loss will happen, to reduce that this part added
        minimal_stop_price = self._stop_loss_price * (1 + Decimal(self._stop_loss_percentage_adjustment))

        self.loggingMessage(f"create_stop_loss_order current_price {current_price}")
        self.loggingMessage(f"create_stop_loss_order stop_loss_price  {self._stop_loss_price}")
        self.loggingMessage(f"create_stop_loss_order minimal_stop_price  {minimal_stop_price}")

        # Place stop-loss and take-profit orders
        if self.config.trade_type == TradeType.SELL.name:
            if current_price >= minimal_stop_price:
                self._stop_loss_order_id = self.execute_order(
                    self._stop_loss_price, self._base_order_filled_token, TradeType.BUY.name, OrderType.LIMIT
                )
                self.loggingMessage(
                    f"stop_loss_order_id return  : {self._stop_loss_order_id} for {self.config.trade_type} "
                )
            else:
                self.loggingMessage(f"stop loss not created for {self.config.trade_type}")
        elif self.config.trade_type == TradeType.BUY.name:
            if current_price <= minimal_stop_price:
                self._stop_loss_order_id = self.execute_order(
                    self._stop_loss_price, self._base_order_filled_token, TradeType.SELL.name, OrderType.LIMIT
                )
                self.loggingMessage(
                    f"stop_loss_order_id return  : {self._stop_loss_order_id} for {self.config.trade_type} "
                )
            else:
                self.loggingMessage(f"stop loss not created for {self.config.trade_type}")

    def create_take_profit_order(self):
        self._take_profit_price = self.calculate_take_profit_price(self._base_order_price, self.config.trade_type)
        # current_price = self.get_current_price() # last trade price

        # Place stop-loss and take-profit orders
        if self.config.trade_type == TradeType.SELL.name:
            # if current_price <= self._take_profit_price:
            self._take_profit_order_id = self.execute_order(
                self._take_profit_price, self._base_order_filled_token, TradeType.BUY.name, OrderType.LIMIT
            )
            self.loggingMessage(
                f"take_profit_order_id return : {self._take_profit_order_id} for {self.config.trade_type}  "
            )
            # else :
            #     self.loggingMessage(f"take profit not created for {self.config.trade_type}")
        elif self.config.trade_type == TradeType.BUY.name:
            # if current_price >= self._take_profit_price:
            self._take_profit_order_id = self.execute_order(
                self._take_profit_price, self._base_order_filled_token, TradeType.SELL.name, OrderType.LIMIT
            )
            self.loggingMessage(
                f"take_profit_order_id return : {self._take_profit_order_id} for {self.config.trade_type}  "
            )
            # else :
            #     self.loggingMessage(f"take profit not created for {self.config.trade_type}")

    def execute_order(self, price: Decimal, order_amount: Decimal, trade_type: TradeType, order_type: OrderType, stop_price: Decimal) -> str:

        order: OrderCandidate = self.create_order(
            price=price, trade_type=trade_type, order_type=order_type, is_maker=True, order_amount=order_amount, stop_price=stop_price
        )

        return self.place_order(connector_name=self.config.exchange, order=order)

    def create_order(
        self, price: Decimal, trade_type: TradeType, order_type: OrderType, is_maker: bool, order_amount: Decimal, stop_price: Decimal
    ) -> OrderCandidate:

        leveraged_order_amount = Decimal(self.config.leverage) * Decimal(order_amount)
        self.loggingMessage(
            f"leveraged_order_amount ==> leverage {self.config.leverage} , order_amount {order_amount}, leveraged_order_amount {leveraged_order_amount}"
        )

        order = OrderCandidate(
            trading_pair=self.config.trading_pair,
            is_maker=is_maker,
            order_type=order_type,
            order_side=trade_type,
            amount=Decimal(leveraged_order_amount),
            price=price,
            stop_price=stop_price
        )

        return order

    def place_order(self, connector_name: str, order: OrderCandidate) -> str:
        try:
            if order.order_side == TradeType.SELL.name:  # TradeType.SELL
                return self.sell(
                    connector_name=connector_name,
                    trading_pair=order.trading_pair,
                    amount=order.amount,
                    order_type=order.order_type,
                    price=order.price,
                    position_action=PositionAction.OPEN,
                )
            elif order.order_side == TradeType.BUY.name:
                return self.buy(
                    connector_name=connector_name,
                    trading_pair=order.trading_pair,
                    amount=order.amount,
                    order_type=order.order_type,
                    price=order.price,
                    position_action=PositionAction.OPEN,  # A position action (for perpetual market only)
                )
        except Exception as e:
            self.log_with_clock(logging.ERROR, f"Failed to submit {order.order_side.name} order: {str(e)}")

    def connect_bot_manager_service(self, get_url):
        self.loggingMessage("Connecting to bot manager service ...")
        try:
            import requests

            service_host = os.getenv("SERVICE_HOST", "0.0.0.0")
            bot_manager_service_port = os.getenv("BOT_MANAGER_SERVICE_PORT", "5000")
            bot_manager_service_prefix_url = os.getenv("BOT_MANAGER_SERVICE_PREFIX_URL", "/api/bots")
            bot_manager_service_hummingbot_manual_leverage_url = os.getenv(
                "BOT_MANAGER_SERVICE_MANUAL_LEVERAGE_SESSION_URL", "/hummingbot-manual-leverage"
            )

            url = f"http://{service_host}:{bot_manager_service_port}{bot_manager_service_prefix_url}{bot_manager_service_hummingbot_manual_leverage_url}{get_url}"
            self.loggingMessage(f"Connecting to bot manager service url {url}")

            response = requests.get(url)

            data = response.json()

            return data

        except Exception as e:
            self.loggingMessage(f"connect_bot_manager_service error: {e}")

    def get_latest_config_data_via_api(self):

        try:
            if self.config.session_id is not None:

                url = f"/{self.config.session_id}"
                self.loggingMessage(f"get_latest_config_data_via_api url {url}")

                self._latest_config_data = self.connect_bot_manager_service(url)

                # self.connect_bot_manager_service()
                self.loggingMessage(f"self._latest_config_data {self._latest_config_data}")

                active_position = self.get_active_position(self.config.trading_pair)

                if self._latest_config_data is not None and self._latest_config_data["message"] == "OK":

                    data = self._latest_config_data["data"]
                    self.loggingMessage(f"data {data}")

                    self.loggingMessage(f"self.config {self.config}")

                    if data is not None:

                        # self.config.trading_pair = data['trading_pair']
                        self.config.exchange = data["exchange"]
                        self.config.base_order_amount = data["order_amount"]
                        self.config.is_stop_loss_activated = data["is_stop_loss_activated"]

                        if data["leverage"] is not None:
                            self.config.leverage = data["leverage"]

                        if data["stop_loss_percentage"] is not None:

                            # if Decimal(self.config.stop_loss_percentage) != Decimal(data['stop_loss_percentage']):
                            if (
                                abs(float(self.config.stop_loss_percentage) - float(data["stop_loss_percentage"]))
                                > 1e-9
                            ):  # Adjust tolerance as needed
                                self.loggingMessage("Stop-loss order cancel and create with latest.")

                                # when coming first time value different and no base order or stop loss created but we need to update the value to create first order thats why
                                self.config.stop_loss_percentage = data["stop_loss_percentage"]

                                if (
                                    self._base_order_id is not None
                                    and self._stop_loss_order_id is not None
                                    and active_position is not None
                                ):
                                    self.cancel_stop_loss_order()  # check if cancel failed what to do
                                    # self.create_stop_loss_order()

                        if data["callback_rate"] is not None:
                            self.config.callback_rate = data["callback_rate"]

                        self.config.is_take_profit_activated = data["is_take_profit_activated"]
                        self.config.is_take_profit_price_selected = data["is_take_profit_price_selected"]

                        if data["take_profit_price"] is not None:
                            # if Decimal(self.config.take_profit_price) != Decimal(data['take_profit_price']):
                            if (
                                abs(float(self.config.take_profit_price) - float(data["take_profit_price"])) > 1e-9
                            ):  # Adjust tolerance as needed
                                self.loggingMessage("Take-profit order cancel and create with latest take_profit_price")

                                # when coming first time value different and no base order or take profit created but we need to update the value to create first order thats why
                                self.config.take_profit_price = data["take_profit_price"]

                                if (
                                    self._base_order_id is not None
                                    and self._take_profit_order_id is not None
                                    and active_position is not None
                                ):
                                    self.cancel_take_profit_order()  # check if cancel failed what to do
                                    # self.create_take_profit_order()

                        if data["take_profit_percentage"] is not None:

                            # if Decimal(self.config.take_profit_percentage) != Decimal(data['take_profit_percentage']) :
                            if (
                                abs(float(self.config.take_profit_percentage) - float(data["take_profit_percentage"]))
                                > 1e-9
                            ):  # Adjust tolerance as needed
                                self.loggingMessage(
                                    "Take-profit order cancel and create with latest take_profit_percentage"
                                )

                                # when coming first time value different and no base order or take profit created but we need to update the value to create first order thats why
                                self.config.take_profit_percentage = data["take_profit_percentage"]

                                if (
                                    self._base_order_id is not None
                                    and self._take_profit_order_id is not None
                                    and active_position is not None
                                ):
                                    self.cancel_take_profit_order()  # check if cancel failed what to do
                                    # self.create_take_profit_order()

                        self.config.trade_type = data["order_side"]

                        binance_credential = data["binance_credential_id"]
                        if binance_credential is not None:
                            self.loggingMessage(f"binance_credential {binance_credential}")
                            self.config.api_key = binance_credential["key"]
                            self.config.api_secret = binance_credential["secret"]

                        if data["exchange"] == "binance_perpetual":
                            self.config.base_url = "https://fapi.binance.com"
                        elif data["exchange"] == "binance":
                            self.config.base_url = "https://api.binance.com"
                        elif data["exchange"] == "binance_perpetual_testnet":
                            self.config.base_url = "https://testnet.binancefuture.com"
                        else:
                            self.config.base_url = "https://testnet.binancefuture.com"

                        if (
                            data["commands"] is not None
                            and len(data["commands"]) > 0
                            and self.config.commands_count != len(data["commands"])
                        ):
                            self.loggingMessage(f"data['commands']  {data['commands']}")
                            self.loggingMessage(
                                f"self.config.commands_count {self.config.commands_count} and data['commands'] {data['commands']}"
                            )

                            self.config.commands_count = len(data["commands"])

                            last_command = data["commands"][-1]

                            self.loggingMessage(f"last_command {last_command}")

                            if last_command["command"] == "exit_limit":

                                if (
                                    self._base_order_id is not None
                                    and active_position is not None
                                    and self._base_order_completed
                                    and abs(active_position.amount)
                                    >= self.truncate_decimal(
                                        (float(self._base_order_filled_token) * float(active_position.leverage)), 0
                                    )
                                ):

                                    self.loggingMessage(f"limit ==> _exit_order_id {self._exit_order_id}")
                                    self.loggingMessage(
                                        f"order size ==> {self.truncate_decimal((float(self._base_order_filled_token) * float(active_position.leverage)), 0)}"
                                    )

                                    if self._exit_order_id is not None:
                                        self.cancel_exit_order()

                                    self._is_exit_limit_order_started = True

                            elif last_command["command"] == "exit_market":

                                if (
                                    self._base_order_id is not None
                                    and active_position is not None
                                    and self._base_order_completed
                                    and abs(active_position.amount)
                                    >= self.truncate_decimal(
                                        (float(self._base_order_filled_token) * float(active_position.leverage)), 0
                                    )
                                ):

                                    self.loggingMessage(f"market ==> _exit_order_id {self._exit_order_id}")
                                    self.loggingMessage(
                                        f"order size ==> {self.truncate_decimal((float(self._base_order_filled_token) * float(active_position.leverage)), 0)}"
                                    )

                                    if self._exit_order_id is not None:
                                        self.cancel_exit_order()

                                    self._is_exit_market_order_started = True

                            elif last_command["command"] == "cancel":
                                self.loggingMessage("cancel orders triggered")
                                self.cancel_orders()

                        self.loggingMessage(f"self.config {self.config}")

        except Exception as e:
            self.loggingMessage(f"get_latest_config_data_via_api error: {e}")

    def get_main_config_data_via_api(self):

        try:
            if self.config.session_id is not None:

                bot_manager_service_main_info_url = os.getenv(
                    "BOT_MANAGER_SERVICE_MANUAL_LEVERAGE_MAIN_INFO_URL", "/main-info"
                )

                url = f"{bot_manager_service_main_info_url}/{self.config.session_id}"
                self.loggingMessage(f"get_main_config_data_via_api url {url}")

                self._main_config_data = self.connect_bot_manager_service(url)
                self.loggingMessage(f"self._main_config_data {self._main_config_data}")

                if self._main_config_data is not None and self._main_config_data["message"] == "OK":

                    data = self._main_config_data["data"]
                    self.loggingMessage(f"data {data}")

                    main_config = data["main_config"]
                    if main_config is not None:
                        self.loggingMessage(
                            f"self.config.buy_order_price_margin conf file : {self.config.limit_order_percentage}"
                        )
                        self.config.limit_order_percentage = main_config["limitOrder"]

                        self.loggingMessage(
                            f"self.config.order_refresh_time conf file : {self.config.order_refresh_time}"
                        )
                        self.config.order_refresh_time = main_config["limitOrderTimeOut"]

                        self.loggingMessage(
                            f"self.config.limit_order_market_attempt conf file : {self.config.limit_order_market_attempt}"
                        )

                        if self.config.limit_order_market_attempt != main_config["limitOrderMarket"]:
                            self._base_order_attempt = 1
                            self._exit_limit_order_attempt = 1

                        self.config.limit_order_market_attempt = main_config["limitOrderMarket"]

        except Exception as e:
            self.loggingMessage(f"get_main_config_data_via_api error: {e}")

    def update_config_order_completed(self):
        self.loggingMessage("update_config_order_completed triggered")
        try:
            if self.config.session_id is not None:

                bot_manager_service_update_session_status_url = "/update-session-status"
                bot_manager_service_status_url = "/completed"

                url = f"{bot_manager_service_update_session_status_url}/{self.config.session_id}{bot_manager_service_status_url}"
                self.loggingMessage(f"update_config_order_completed url {url}")

                result = self.connect_bot_manager_service(url)
                self.loggingMessage(f"result {result}")

        except Exception as e:
            self.loggingMessage(f"update_config_order_completed error: {e}")

    def get_asset_balance_base_asset(self, base_asset) -> Any:

        data: List[Any] = []
        for connector_name, connector in self.connectors.items():
            for asset in self.get_assets(connector_name):
                exchange_balance = {
                    "asset": asset,
                    "total_amount": self.truncate_decimal(connector.get_balance(asset), 7),
                    "available_balance": self.truncate_decimal(connector.get_available_balance(asset), 7),
                }
            data.append(exchange_balance)

        for balance in data:
            if balance["asset"] == base_asset:
                return balance
        return None

    def get_active_position(self, trading_pair: str) -> Optional[Position]:
        for connector_name, connector in self.connectors.items():
            if trading_pair in self.markets[connector_name]:
                position = connector.account_positions.get(trading_pair)
                if position and position.trading_pair == trading_pair:
                    self.loggingMessage(f"position trading_pair : {trading_pair}")
                    self.loggingMessage(f"position: {position}")
                    return position

        return None

    def truncate_decimal(self, number, decimal_places):
        """
        Truncate a number to a specified number of decimal places without rounding.

        :param number: The number to truncate.
        :param decimal_places: The number of decimal places to keep.
        :return: The truncated number as a float.
        """
        # Create a Decimal object from the number
        d = Decimal(str(number))

        # Define the truncation precision
        truncating_factor = "1." + "0" * decimal_places

        # Truncate the number
        truncated_number = d.quantize(Decimal(truncating_factor), rounding=ROUND_DOWN)

        return float(truncated_number)

    def get_current_price(self) -> Decimal:
        return self.connectors[self.config.exchange].get_price_by_type(self.config.trading_pair, self.price_source)

    def calculate_limit_order_price(self, current_price: Decimal, inverted: bool) -> Decimal:
        if not inverted:
            return current_price * Decimal(1 - self.config.limit_order_percentage)
        else:
            return current_price * Decimal(1 + self.config.limit_order_percentage)

    def calculate_stop_loss_price(self, entry_price: Decimal, trade_type: str) -> Decimal:
        pct = 1

        if self.config.is_stop_loss_activated:
            if trade_type == TradeType.SELL.name:
                pct = Decimal(1 + (self.config.stop_loss_percentage / 100))

            elif trade_type == TradeType.BUY.name:
                pct = Decimal(1 - (self.config.stop_loss_percentage / 100))

        self.loggingMessage(f"pct  stop loss {pct} trade type {trade_type}")

        return entry_price * pct

    def calculate_take_profit_price(self, entry_price: Decimal, trade_type: str) -> Decimal:

        pct = 1
        if self.config.is_take_profit_activated:
            if not self.config.is_take_profit_price_selected:
                if trade_type == TradeType.SELL.name:
                    pct = Decimal(1 - (self.config.take_profit_percentage / 100))

                elif trade_type == TradeType.BUY.name:
                    pct = Decimal(1 + (self.config.take_profit_percentage / 100))
            else:
                return self.config.take_profit_price

        self.loggingMessage(f"pct take profit {pct}  trade type {trade_type}")

        return entry_price * pct

    def exit_order(self, is_market: bool):
        try:
            self.loggingMessage("exit_order ==> initiated")
            current_price = self.get_current_price()
            self.loggingMessage(f"current price: {current_price} ")

            order_type = OrderType.LIMIT

            if self._exit_order_id is None:

                if self.config.trade_type == TradeType.BUY.name:
                    exit_price = self.calculate_limit_order_price(
                        current_price, True
                    )  # calculated amount if its limit and create based on tp placing

                    if is_market:
                        exit_price = current_price  # base current price if its a market
                        order_type = OrderType.MARKET

                    self.loggingMessage(f"exit_order ==>  : exit_price {exit_price} and order_type {order_type} ")
                    self._exit_order_id = self.execute_order(
                        exit_price, self._base_order_filled_token, TradeType.SELL.name, order_type
                    )

                    self.loggingMessage(f"exit order_id return : {self._exit_order_id} ")

                elif self.config.trade_type == TradeType.SELL.name:
                    exit_price = self.calculate_limit_order_price(
                        current_price, False
                    )  # calculated amount if its limit and create based on tp placing

                    if is_market:
                        exit_price = current_price  # base current price if its a market
                        order_type = OrderType.MARKET

                    self.loggingMessage(f"exit_order ==>  : exit_price {exit_price} and order_type {order_type} ")
                    self._exit_order_id = self.execute_order(
                        exit_price, self._base_order_filled_token, TradeType.BUY.name, order_type
                    )

                    self.loggingMessage(f"exit order_id return : {self._exit_order_id} ")

                else:
                    self.loggingMessage(
                        f"exit_order ==> cannot create stop loss order in exit price trading type : {self.config.trade_type}"
                    )

        except Exception as e:
            self.loggingMessage(f"exit_order ==> error: {e}")

    def cancel_orders(self):
        try:
            self.loggingMessage("cancel_orders ==> initiated ")

            if self._base_order_id is not None and not self._base_order_completed:
                self.cancel_base_order()

            if self._stop_loss_order_id is not None:
                self.loggingMessage(f"cancel stop loss order ==> : stop loss order id  {self._stop_loss_order_id}")

                self.cancel_stop_loss_order()

            if self._take_profit_order_id is not None:
                self.loggingMessage(
                    f"cancel take profit order ==> : take profit order id  {self._take_profit_order_id}"
                )

                self.cancel_take_profit_order()

            if self._exit_order_id is not None:
                self.loggingMessage(f"cancel exit order ==> : exit order id  {self._exit_order_id}")

                self.cancel_exit_order()

            self._is_cancelled = True

        except Exception as e:
            self.loggingMessage(f"cancel_orders ==> error: {e}")

    def did_create_buy_order(self, event: BuyOrderCreatedEvent):
        """
        Method called when the connector notifies a buy order has been created
        """

        self.loggingMessage(f"The buy order {event.order_id} has been created")

    def did_create_sell_order(self, event: SellOrderCreatedEvent):
        """
        Method called when the connector notifies a sell order has been created
        """
        self.loggingMessage(f"The sell order {event.order_id} has been created")

    def did_fill_order(self, event: OrderFilledEvent):
        """
        Method called when the connector notifies that an order has been partially or totally filled (a trade happened)
        """
        self.loggingMessage(f"The buy order from did_fill_order {event.order_id} has been filled")

    def did_fail_order(self, event: MarketOrderFailureEvent):
        """
        Method called when the connector notifies an order has failed
        """
        self.loggingMessage(f"The order from did_fail_order {event} failed")
        self.loggingMessage(f"The order from did_fail_order {event.order_id} failed")

        # if self._exit_order_id == event.order_id :
        #     self._exit_order_id = None

    def did_cancel_order(self, event: OrderCancelledEvent):
        """
        Method called when the connector notifies an order has been cancelled
        """
        self.loggingMessage(f"The order from did_cancel_order {event} started")
        self.loggingMessage(f"The order from did_cancel_order {event.order_id} has been cancelled")

        self.on_order_cancelled(event.order_id)

    def did_complete_buy_order(self, event: BuyOrderCompletedEvent):
        # Buy / Long
        """
        Method called when the connector notifies a buy order has been completed (fully filled)
        """
        self.loggingMessage(f"The buy order from did_complete_buy_order {event.order_id} has been fully filled")

        self.order_completed(event.order_id)

        if self.config.trade_type == TradeType.SELL.name:
            # stop bot and remove container
            self.update_config_order_completed()

            # self._base_order_price = None
            # self._base_order_id = None
            # self._base_order_filled_token = None

            # self._stop_loss_price = None
            # self._stop_loss_order_id = None
            # self._take_profit_price = None
            # self._take_profit_order_id = None

            # self._stop_loss_order_id_by_market = None
            # self._exit_order_id = None

    def did_complete_sell_order(self, event: SellOrderCompletedEvent):
        # Sell / Short
        """
        Method called when the connector notifies a sell order has been completed (fully filled)
        """
        self.loggingMessage(f"The sell order from did_complete_sell_order {event.order_id} has been fully filled")

        self.order_completed(event.order_id)

        if self.config.trade_type == TradeType.BUY.name:
            # stop bot and remove container
            self.update_config_order_completed()

            # self._base_order_price = None
            # self._base_order_id = None
            # self._base_order_filled_token = None

            # self._stop_loss_price = None
            # self._stop_loss_order_id = None
            # self._take_profit_price = None
            # self._take_profit_order_id = None

            # self._stop_loss_order_id_by_market = None
            # self._exit_order_id = None
