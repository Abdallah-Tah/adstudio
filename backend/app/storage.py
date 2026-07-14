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


class Storage:
    """Thin upload/download wrapper; injectable so tests can fake it."""

    def __init__(self, client=None, bucket: str | None = None):
        self.client = client or make_s3_client()
        self.bucket = bucket or bucket_name()

    def ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            self.client.create_bucket(Bucket=self.bucket)

    def put_bytes(self, data: bytes, key: str, content_type: str) -> str:
        """Upload and return the canonical s3:// URI."""
        self.client.put_object(
            Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
        )
        return f"s3://{self.bucket}/{key}"

    def get_bytes(self, uri: str) -> bytes:
        """Download by canonical s3://bucket/key URI."""
        bucket, key = uri.removeprefix("s3://").split("/", 1)
        return self.client.get_object(Bucket=bucket, Key=key)["Body"].read()
