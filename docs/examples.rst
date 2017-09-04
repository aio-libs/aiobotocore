Examples of aiobotocore usage
=============================

Below is a list of examples from `aiobotocore/examples
<https://github.com/jettify/aiobotocore/tree/master/examples>`_

Every example is a correct tiny python program.

.. _aiobotocore-examples-simple:

Basic Usage
-----------

Simple put, get, delete example for S3 service:

.. literalinclude:: ../examples/simple.py


SQS
---

Queue Create
++++++++++++

This snippet creates a queue, lists the queues, then deletes the queue.

.. literalinclude:: ../examples/sqs_queue_create.py

Producer Consumer
+++++++++++++++++

Here is a quick and simple producer/consumer example. The producer will put messages on the queue with a delay of up to 4 seconds between each put.
The consumer will read off any messages on the queue, waiting up to 2 seconds for messages to appear before returning.

.. literalinclude:: ../examples/sqs_queue_producer.py

.. literalinclude:: ../examples/sqs_queue_consumer.py
