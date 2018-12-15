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

.. literalinclude:: ../examples/sqs/create.py

Producer Consumer
+++++++++++++++++

Here is a quick and simple producer/consumer example. The producer will put messages on the queue with a delay of up to 4 seconds between each put.
The consumer will read off any messages on the queue, waiting up to 2 seconds for messages to appear before returning.

.. literalinclude:: ../examples/sqs/producer.py

.. literalinclude:: ../examples/sqs/consumer.py


Kinesis
-------

Stream Create
+++++++++++++

This snippet creates a stream `look to boto3 <https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/kinesis.html#Kinesis.Client.create_stream>`_

.. literalinclude:: ../examples/kinesis/create.py

Producer Consumer
+++++++++++++++++

Here is a quick and simple producer/consumer example. The producer will put messages on the stream.
The consumer will read off any messages on the stream.

.. literalinclude:: ../examples/kinesis/producer.py

.. literalinclude:: ../examples/kinesis/consumer.py

* send message `boto3[put_record] <https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/kinesis.html#Kinesis.Client.put_record>`_
* summary stream `boto3[describe_stream] <https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/kinesis.html#Kinesis.Client.describe_stream>`_
* get iterator for shard  `boto3[get_shard_iterator] <https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/kinesis.html#Kinesis.Client.get_shard_iterator>`_
* receive message in foreach loop `boto3[get_records] <https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/kinesis.html#Kinesis.Client.get_records>`_
* what is a **partition_key** and how to work with it `AWS Big Data Blog <https://aws.amazon.com/ru/blogs/big-data/snakes-in-the-stream-feeding-and-eating-amazon-kinesis-streams-with-python>`_



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