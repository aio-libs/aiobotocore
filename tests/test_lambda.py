import base64
import contextlib
import io
import json
import zipfile

# Third Party
import botocore.client
import botocore.exceptions
import pytest


async def _get_role_arn(iam_client, role_name: str):
    try:
        response = await iam_client.get_role(RoleName=role_name)
        return response["Role"]["Arn"]
    except botocore.client.ClientError:
        response = await iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument="some policy",
            Path="/my-path/",
        )
        return response["Role"]["Arn"]


def _process_lambda(func_str) -> bytes:
    zip_output = io.BytesIO()
    zip_file = zipfile.ZipFile(zip_output, "w", zipfile.ZIP_DEFLATED)
    zip_file.writestr("lambda_function.py", func_str)
    zip_file.close()
    zip_output.seek(0)
    return zip_output.read()


@pytest.fixture
def aws_lambda_zip() -> bytes:
    lambda_src = """
import json
def lambda_handler(event, context):
    print(event)
    return {"statusCode": 200, "body": event}
"""
    return _process_lambda(lambda_src)


@pytest.fixture
async def lambda_client_with_test_function(
    iam_client, lambda_client, aws_lambda_zip
):
    role_arn = await _get_role_arn(iam_client, 'test-iam-role')
    lambda_response = await lambda_client.create_function(
        FunctionName='test-function',
        Runtime='python3.8',
        Role=role_arn,
        Handler='lambda_function.lambda_handler',
        Timeout=10,
        MemorySize=128,
        Publish=True,
        Code={'ZipFile': aws_lambda_zip},
    )
    try:
        assert lambda_response['FunctionName'] == 'test-function'
        yield lambda_client
    finally:
        with contextlib.suppress(botocore.exceptions.ClientError):
            await lambda_client.delete_function(FunctionName='test-function')


# moto sometimes attempts to use legacy Lambda image which fails to run on arm64
@pytest.mark.flaky(reruns=10)
async def test_run_lambda(lambda_client_with_test_function):
    invoke_response = await lambda_client_with_test_function.invoke(
        FunctionName="test-function",
        InvocationType="RequestResponse",
        LogType='Tail',
        Payload=json.dumps({"hello": "world"}),
    )

    async with invoke_response['Payload'] as stream:
        data = await stream.read()

    log_result = base64.b64decode(invoke_response["LogResult"])

    assert json.loads(data) == {'statusCode': 200, "body": {"hello": "world"}}
    assert b"{'hello': 'world'}" in log_result
