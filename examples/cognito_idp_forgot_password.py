# Boto should get credentials from ~/.aws/credentials or the environment
import asyncio

from aiobotocore.session import get_session


async def go():
    session = get_session()
    async with session.create_client(
        'cognito-idp',
        region_name='us-west-2',
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
