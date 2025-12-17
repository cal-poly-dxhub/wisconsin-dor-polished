#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys

import boto3
from botocore.exceptions import ClientError


def apply_bucket_policy(source_bucket: str, dest_role_arn: str):
    s3 = boto3.client("s3")

    bucket_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowDestRoleListBucket",
                "Effect": "Allow",
                "Principal": {"AWS": dest_role_arn},
                "Action": ["s3:ListBucket"],
                "Resource": f"arn:aws:s3:::{source_bucket}",
            },
            {
                "Sid": "AllowDestRoleReadObjects",
                "Effect": "Allow",
                "Principal": {"AWS": dest_role_arn},
                "Action": ["s3:GetObject"],
                "Resource": f"arn:aws:s3:::{source_bucket}/*",
            },
        ],
    }  # [web:69][web:84]

    policy_str = json.dumps(bucket_policy)

    try:
        print(f"Setting bucket policy on {source_bucket} to allow {dest_role_arn}...")
        s3.put_bucket_policy(Bucket=source_bucket, Policy=policy_str)
        print("Bucket policy applied.")
    except ClientError as e:
        print(f"Error applying bucket policy: {e}")
        raise


def assume_dest_role(dest_role_arn: str, session_name: str = "s3-sync-session"):
    sts = boto3.client("sts")
    try:
        resp = sts.assume_role(
            RoleArn=dest_role_arn,
            RoleSessionName=session_name,
        )
    except ClientError as e:
        print(f"Error assuming role {dest_role_arn}: {e}")
        raise

    creds = resp["Credentials"]
    return {
        "AWS_ACCESS_KEY_ID": creds["AccessKeyId"],
        "AWS_SECRET_ACCESS_KEY": creds["SecretAccessKey"],
        "AWS_SESSION_TOKEN": creds["SessionToken"],
    }  # [web:71][web:72]


def run_sync(source_bucket: str, dest_bucket: str, env_creds: dict | None):
    cmd = [
        "aws",
        "s3",
        "sync",
        f"s3://{source_bucket}",
        f"s3://{dest_bucket}",
    ]  # [web:38]

    env = None
    if env_creds:
        import os

        env = os.environ.copy()
        env.update(env_creds)

    print(f"Running: {' '.join(cmd)}")
    try:
        subprocess.check_call(cmd, env=env)
    except subprocess.CalledProcessError as e:
        print(f"Sync command failed with exit code {e.returncode}")
        sys.exit(e.returncode)


def main():
    parser = argparse.ArgumentParser(
        description="Grant DEST cross-account role read access to SOURCE bucket and optionally run aws s3 sync."
    )
    parser.add_argument(
        "--source-bucket", required=True, help="Source S3 bucket name (in this account)."
    )
    parser.add_argument("--dest-bucket", required=True, help="Destination S3 bucket name.")
    parser.add_argument(
        "--dest-role-arn",
        required=True,
        help="ARN of the IAM role in DEST account (from destination script).",
    )
    parser.add_argument(
        "--assume-role",
        action="store_true",
        help="Assume the destination role before running sync.",
    )
    args = parser.parse_args()

    # 1) Apply bucket policy on source bucket to allow dest role.
    apply_bucket_policy(args.source_bucket, args.dest_role_arn)

    # 2) Ask whether to run sync
    answer = (
        input(
            f"\nReady to sync s3://{args.source_bucket} -> s3://{args.dest_bucket} using role {args.dest_role_arn}. "
            f"Run sync now? [y/N]: "
        )
        .strip()
        .lower()
    )

    if answer != "y":
        print("Sync skipped.")
        return

    # 3) Optionally assume role, then run sync
    env_creds = None
    if args.assume_role:
        print(f"Assuming role {args.dest_role_arn}...")
        env_creds = assume_dest_role(args.dest_role_arn)

    print("Starting sync...")
    run_sync(args.source_bucket, args.dest_bucket, env_creds)
    print("Sync completed.")


if __name__ == "__main__":
    main()
