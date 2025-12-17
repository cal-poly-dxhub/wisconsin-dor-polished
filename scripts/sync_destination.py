#!/usr/bin/env python3
import argparse
import json
import boto3
from botocore.exceptions import ClientError


def main():
    parser = argparse.ArgumentParser(
        description="Create/update cross-account S3 sync role in DEST account with access to source and destination buckets."
    )
    parser.add_argument("--role-name", required=True, help="Name of IAM role to create/update.")
    parser.add_argument(
        "--source-account-id", required=True, help="12-digit AWS account ID of the SOURCE account."
    )
    parser.add_argument("--source-bucket", required=True, help="Name of the SOURCE S3 bucket.")
    parser.add_argument(
        "--dest-bucket", required=True, help="Name of the DESTINATION S3 bucket (in this account)."
    )
    parser.add_argument(
        "--policy-name",
        default="CrossAccountS3SyncPolicy",
        help="Inline policy name (default: CrossAccountS3SyncPolicy).",
    )
    args = parser.parse_args()

    iam = boto3.client("iam")

    # Trust policy: allow the *source* account to assume this role.
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowSourceAccountAssumeRole",
                "Effect": "Allow",
                "Principal": {"AWS": f"arn:aws:iam::{args.source_account_id}:root"},
                "Action": "sts:AssumeRole",
            }
        ],
    }  # [web:62][web:67]

    try:
        print(f"Creating role {args.role_name}...")
        iam.create_role(
            RoleName=args.role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description=f"Cross-account S3 sync role for {args.source_bucket} -> {args.dest_bucket}",
        )
        print(f"Role {args.role_name} created.")
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            print(f"Role {args.role_name} already exists, updating trust policy.")
            iam.update_assume_role_policy(
                RoleName=args.role_name,
                PolicyDocument=json.dumps(trust_policy),
            )
        else:
            raise

    # Inline permissions policy: read from source bucket, read/write to destination bucket.
    policy_doc = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowReadFromSourceBucket",
                "Effect": "Allow",
                "Action": ["s3:ListBucket"],
                "Resource": f"arn:aws:s3:::{args.source_bucket}",
            },
            {
                "Sid": "AllowGetObjectsFromSourceBucket",
                "Effect": "Allow",
                "Action": ["s3:GetObject"],
                "Resource": f"arn:aws:s3:::{args.source_bucket}/*",
            },
            {
                "Sid": "AllowListDestBucket",
                "Effect": "Allow",
                "Action": ["s3:ListBucket"],
                "Resource": f"arn:aws:s3:::{args.dest_bucket}",
            },
            {
                "Sid": "AllowWriteDestBucketObjects",
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                "Resource": f"arn:aws:s3:::{args.dest_bucket}/*",
            },
        ],
    }  # [web:65][web:109][web:114]

    print(f"Putting inline policy {args.policy_name} on role {args.role_name}...")
    iam.put_role_policy(
        RoleName=args.role_name,
        PolicyName=args.policy_name,
        PolicyDocument=json.dumps(policy_doc),
    )
    print("Done.\n")

    sts = boto3.client("sts")
    acct_id = sts.get_caller_identity()["Account"]
    role_arn = f"arn:aws:iam::{acct_id}:role/{args.role_name}"
    print("Use this role ARN in the source account script:")
    print(f"  {role_arn}")


if __name__ == "__main__":
    main()
