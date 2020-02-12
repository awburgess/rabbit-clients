"""
Base classes for Rabbit

"""
from typing import Any, Dict, Tuple
import json

import pika
from retry import retry

from rabbit_clients.clients.config import rabbit_config


def _create_connection_and_channel() -> Tuple[pika.BlockingConnection, pika.BlockingConnection.channel]:
    """
    Will run immediately on library import.  Requires that an environment variable
    for RABBIT_URL has been set.

    :return: Tuple as rabbitmq connection and channel
    :rtype: tuple

    """
    credentials = pika.PlainCredentials(rabbit_config.RABBITMQ_USER, rabbit_config.RABBITMQ_PASSWORD)
    connection = pika.BlockingConnection(pika.ConnectionParameters(rabbit_config.RABBITMQ_HOST,
                                                                   virtual_host=rabbit_config.RABBITMQ_VIRTUAL_HOST,
                                                                   credentials=credentials))
    return connection, connection.channel()


def send_log(channel: Any, method: str, properties: Any, body: str) -> Dict[str, Any]:
    """
    Helper function to send messages to logging queue

    :param channel: Channel from incoming message
    :param method: Method from incoming message
    :param properties: Properties from incoming message
    :param body: JSON from incoming message
    :return: Dictionary representation of message

    """
    return {
        'channel': str(channel),
        'method': str(method),
        'properties': str(properties),
        'body': body
    }


class ConsumeMessage:
    def __init__(self, queue: str, exchange: str = '', exchange_type: str = '',
                 logging: bool = True, logging_queue: str = 'logging'):
        self._consume_queue = queue
        self._exchange = exchange
        self._exchange_type = exchange_type
        self._logging = logging
        self._logging_queue = logging_queue

    def __call__(self, func, *args, **kwargs) -> Any:
        @retry(pika.exceptions.AMQPConnectionError, tries=5, delay=5, jitter=(1, 3))
        def prepare_channel(*args, **kwargs):
            """
            Ensure RabbitMQ Connection is open and that you have an open
            channel.  Then provide a callback returns the target function
            but ensures that the incoming message body has been
            converted from JSON to a Python dictionary.

            :param func: The user function being decorated
            :return: An open listener utilizing the user function or
            a one time message receive in the event of parent function
            parameter of production ready being set to False

            """
            # Open RabbitMQ connection if it has closed or is not set
            connection, channel = _create_connection_and_channel()

            if self._exchange:
                channel.exchange_declare(exchange=self._exchange, exchange_type=self._exchange_type)
                result = channel.queue_declare(queue=self._consume_queue)
                channel.queue_bind(exchange=self._exchange, queue=result.method.queue)
            else:
                channel.queue_declare(queue=self._consume_queue)

            log_publisher = PublishMessage(queue=self._logging_queue)

            # Callback function for when a message is received
            def message_handler(channel, method, properties, body):

                # Utilize module decorator to send logging messages
                decoded_body = json.loads(body.decode('utf-8'))
                if self._logging:
                    log_publisher(send_log)(channel, method, properties, decoded_body)

                func(decoded_body)

            channel.basic_consume(queue=self._consume_queue, on_message_callback=message_handler, auto_ack=True)

            try:
                channel.start_consuming()
            except pika.exceptions.ConnectionClosedByBroker:
                pass
            except KeyboardInterrupt:
                channel.stop_consuming()

        return prepare_channel


class PublishMessage:
    def __init__(self, queue: str, exchange: str = '', exchange_type: str = 'fanout'):
        self._queue = queue
        self._exchange = exchange
        self._exchange_type = exchange_type

    def __call__(self, func, *args, **kwargs) -> Any:
        @retry(pika.exceptions.AMQPConnectionError, tries=5, delay=5, jitter=(1, 3))
        def wrapper(*args, **kwargs):
            """
            Run the function as expected but the return from the function must
            be a Python dictionary as it will be converted to JSON. Then ensure
            RabbitMQ connection is open and that you have an open channel.  Then
            use a basic_publish method to send the message to the target queue.

            :param args:  Any positional arguments passed to the function
            :param kwargs: Any keyword arguments pass to the function
            :return: None

            """
            # Run the function and get dictionary as result
            result = func(*args, **kwargs)

            # Ensure open connection and channel
            connection, channel = _create_connection_and_channel()

            if self._exchange:
                channel.exchange_declare(exchange=self._exchange, exchange_type=self._exchange_type)
                result = channel.queue_declare(queue=self._queue)
                channel.queue_bind(exchange=self._exchange, queue=result.method.queue)
            else:
                # Ensure queue exists
                channel.queue_declare(queue=self._queue)

            # Send message to queue
            channel.basic_publish(
                exchange=self._exchange,
                routing_key=self._queue,
                body=json.dumps(result)
            )

        return wrapper
