import asyncio

from aiobotocore.session import AioSession

AWS_ACCESS_KEY_ID = "xxx"
AWS_SECRET_ACCESS_KEY = "xxx"


async def go():
    session = AioSession()
    async with session.create_client(
        'cognito-idp',
        region_name='us-west-2',
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
    ) as client:
        # initiate forgot password
        resp = await client.forgot_password(
            ClientId='xxx',
            Username='xxx',
        )
        print(resp)

        # confirm forgot password
        resp = await client.confirm_forgot_password(
            ClientId='xxx',
            Username='xxx',
            ConfirmationCode='xxx',
            Password='xxx',
        )
        print(resp)


if __name__ == '__main__':
    asyncio.run(go())
