import pytest


@pytest.mark.moto
@pytest.mark.asyncio
async def test_ec2_snapshot(ec2_client):
    # TODO: this needs to somehow validate the presigned url sent because moto is not
    volume_response = await ec2_client.create_volume(
        AvailabilityZone="us-east-1", Size=10
    )
    tag_spec = [
        {
            "ResourceType": "snapshot",
            "Tags": [{"Key": "key", "Value": "value"}],
        }
    ]

    create_snapshot_response = await ec2_client.create_snapshot(
        VolumeId=volume_response["VolumeId"], TagSpecifications=tag_spec
    )

    copy_snapshot_response = await ec2_client.copy_snapshot(
        SourceSnapshotId=create_snapshot_response["SnapshotId"],
        SourceRegion="us-east-1",
        DestinationRegion="us-east-1",
        Encrypted=True,
        TagSpecifications=tag_spec,
        KmsKeyId="key-1234",
    )

    assert copy_snapshot_response['SnapshotId']
