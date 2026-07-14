"""R2/S3 client (boto3). Works against MinIO locally via S3_ENDPOINT."""
import os

import boto3


def make_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("S3_ENDPOINT", "http://localhost:9000"),
        aws_access_key_id=os.environ.get("S3_KEY", "adstudio"),
        aws_secret_access_key=os.environ.get("S3_SECRET", "adstudio-secret"),
    )


def bucket_name() -> str:
    return os.environ.get("S3_BUCKET", "ad-studio")
