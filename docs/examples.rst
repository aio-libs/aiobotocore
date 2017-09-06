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

DynamoDB
--------

Table Creation
++++++++++++++

When you create a DynamoDB table, it can take quite a while (especially if you add a few secondary index's). Instead of polling `describe_table` yourself,
boto3 came up with "waiters" that will do all the polling for you. The following snippet shows how to wait for a DynamoDB table to be created in an async way.

.. literalinclude:: ../examples/dynamodb_create_table.py

Batch Insertion
+++++++++++++++

Now if you have a massive amount of data to insert into Dynamo, I would suggest using an EMR data pipeline (theres even an example for exactly this). But
if you stubborn, here is an example of inserting lots of items into Dynamo (it's not really that complicated once you've read it).

What the code does is generates items (e.g. item0, item1, item2...) and writes them to a table "test" against a primary partition key called "pk"
(with 5 read and 5 write units, no auto-scaling).

The `batch_write_item` method only takes a max of 25 items at a time, so the script computes 25 items, writes them, then does it all over again.

After Dynamo has had enough, it will start throttling you and return any items that have not been written in the response. Once the script is
being throttled, it will start sleeping for 5 seconds until the failed items have been successfully written, after that it will exit.

.. literalinclude:: ../examples/dynamodb_batch_write.py