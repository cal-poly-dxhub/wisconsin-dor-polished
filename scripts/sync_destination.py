#!/usr/bin/env python3
import argparse
import json
import os
import boto3
from botocore.exceptions import ClientError

FAQ_SOURCE_BUCKET = "wis-faq-bucket"
RAG_SOURCE_BUCKET = "wis-rag-bucket"
SOURCE_ACCOUNT_ID = "285396213403"
CDK_STACK_NAME = "WisconsinBotStack"
DEFAULT_ROLE_NAME = "CrossAccountS3SyncRole"


def get_region() -> str:
    region = (
        boto3.Session().region_name
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
    )
    if not region:
        raise ValueError("AWS region not configured. Set AWS_REGION or configure your AWS CLI.")
    return region


def get_bucket_from_cdk_output(export_name: str) -> str:
    cf = boto3.client("cloudformation", region_name=get_region())
    resp = cf.describe_stacks(StackName=CDK_STACK_NAME)
    outputs = resp["Stacks"][0].get("Outputs", [])
    for output in outputs:
        if output.get("ExportName") == export_name:
            return output["OutputValue"]
    raise ValueError(f"Could not find CDK output with ExportName={export_name}")


def main():
    parser = argparse.ArgumentParser(
        description="Create/update cross-account S3 sync role in DEST account with access to source and destination buckets."
    )
    parser.add_argument(
        "--role-name",
        default=DEFAULT_ROLE_NAME,
        help=f"Name of IAM role to create/update (default: {DEFAULT_ROLE_NAME}).",
    )
    parser.add_argument(
        "--source-account-id",
        default=SOURCE_ACCOUNT_ID,
        help=f"12-digit AWS account ID of the SOURCE account (default: {SOURCE_ACCOUNT_ID}).",
    )
    parser.add_argument(
        "--faq-source-bucket",
        default=FAQ_SOURCE_BUCKET,
        help=f"Name of the SOURCE FAQ S3 bucket (default: {FAQ_SOURCE_BUCKET}).",
    )
    parser.add_argument(
        "--faq-dest-bucket",
        help="Name of the DESTINATION FAQ S3 bucket (default: read from CDK stack output).",
    )
    parser.add_argument(
        "--rag-source-bucket",
        default=RAG_SOURCE_BUCKET,
        help=f"Name of the SOURCE RAG S3 bucket (default: {RAG_SOURCE_BUCKET}).",
    )
    parser.add_argument(
        "--rag-dest-bucket",
        help="Name of the DESTINATION RAG S3 bucket (default: read from CDK stack output).",
    )
    parser.add_argument(
        "--policy-name",
        default="CrossAccountS3SyncPolicy",
        help="Inline policy name (default: CrossAccountS3SyncPolicy).",
    )
    args = parser.parse_args()

    faq_dest_bucket = args.faq_dest_bucket or get_bucket_from_cdk_output(
        "WisconsinBot-FaqBucketName"
    )
    rag_dest_bucket = args.rag_dest_bucket or get_bucket_from_cdk_output(
        "WisconsinBot-RagBucketName"
    )

    iam = boto3.client("iam", region_name=get_region())

    source_buckets = [args.faq_source_bucket, args.rag_source_bucket]
    dest_buckets = [faq_dest_bucket, rag_dest_bucket]

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
    }

    try:
        print(f"Creating role {args.role_name}...")
        iam.create_role(
            RoleName=args.role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description=f"Cross-account S3 sync role for FAQ and RAG buckets",
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

    policy_doc = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowListSourceBuckets",
                "Effect": "Allow",
                "Action": ["s3:ListBucket"],
                "Resource": [f"arn:aws:s3:::{b}" for b in source_buckets],
            },
            {
                "Sid": "AllowGetObjectsFromSourceBuckets",
                "Effect": "Allow",
                "Action": ["s3:GetObject"],
                "Resource": [f"arn:aws:s3:::{b}/*" for b in source_buckets],
            },
            {
                "Sid": "AllowListDestBuckets",
                "Effect": "Allow",
                "Action": ["s3:ListBucket"],
                "Resource": [f"arn:aws:s3:::{b}" for b in dest_buckets],
            },
            {
                "Sid": "AllowWriteDestBucketObjects",
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                "Resource": [f"arn:aws:s3:::{b}/*" for b in dest_buckets],
            },
        ],
    }

    print(f"Putting inline policy {args.policy_name} on role {args.role_name}...")
    iam.put_role_policy(
        RoleName=args.role_name,
        PolicyName=args.policy_name,
        PolicyDocument=json.dumps(policy_doc),
    )
    print("Done.\n")

    sts = boto3.client("sts", region_name=get_region())
    acct_id = sts.get_caller_identity()["Account"]
    role_arn = f"arn:aws:iam::{acct_id}:role/{args.role_name}"
    print("Use this role ARN in the source account script:")
    print(f"  {role_arn}")


if __name__ == "__main__":
    main()
