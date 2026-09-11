"""Minimal in-memory stand-in for the boto3 S3 client used by the ingestion modules.

Supports exactly what extract.py / embed.py / load.py call: get_object,
put_object, head_object, delete_object and list_objects_v2 via get_paginator.
No moto dependency. Patch it in with ``patch.object(module, "s3", FakeS3())``.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta


class _NoSuchKey(Exception):
    def __init__(self, key: str):
        super().__init__(f"NoSuchKey: {key}")
        self.response = {"Error": {"Code": "NoSuchKey"}}


class FakeS3:
    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}
        self.modified: dict[tuple[str, str], datetime] = {}
        self.puts: list[tuple[str, str]] = []
        self._clock = datetime(2026, 1, 1, tzinfo=UTC)

    # -- helpers -----------------------------------------------------------
    def _tick(self) -> datetime:
        self._clock += timedelta(seconds=1)
        return self._clock

    def put_bytes(self, bucket: str, key: str, body: bytes) -> None:
        self.objects[(bucket, key)] = body
        self.modified[(bucket, key)] = self._tick()

    def keys(self, bucket: str, prefix: str = "") -> list[str]:
        return sorted(k for (b, k) in self.objects if b == bucket and k.startswith(prefix))

    # -- boto3 surface -----------------------------------------------------
    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise _NoSuchKey(Key)
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def put_object(self, Bucket, Key, Body, **_):
        if isinstance(Body, str):
            Body = Body.encode("utf-8")
        self.put_bytes(Bucket, Key, Body)
        self.puts.append((Bucket, Key))
        return {}

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            from botocore.exceptions import ClientError

            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {
            "LastModified": self.modified[(Bucket, Key)],
            "ContentLength": len(self.objects[(Bucket, Key)]),
        }

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)
        self.modified.pop((Bucket, Key), None)
        return {}

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        store = self

        class _Paginator:
            def paginate(self, Bucket, Prefix=""):
                contents = [
                    {"Key": k, "Size": len(store.objects[(Bucket, k)])}
                    for k in store.keys(Bucket, Prefix)
                ]
                yield {"Contents": contents}

        return _Paginator()
