import os

import boto3
from textractor.data.constants import TextractFeatures
from textractor.textractor import Textractor

# Lazy-initialized to avoid AWS calls at import time
_session = None
_s3_client = None


def _get_region():
    global _session
    if _session is None:
        _session = boto3.session.Session()
        print("Using AWS region:", _session.region_name)
    return _session.region_name


def _get_s3_client():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3", region_name=_get_region())
    return _s3_client


def get_emb(embeddings_client, passage: str) -> list[float]:
    """Get embedding for a given passage using titan embeddings."""

    # Invoke the model
    embedding = embeddings_client.embed_query(passage)
    return embedding


def extract_textract_data_local(local_pdf_path: str):
    """
    Run Textract on a local PDF file (synchronously).
    Returns:
      - document: Textractor Document object
      - local_pdf_path: echo of input path (for downstream helpers)
      - None: placeholder for textract_output_path (unused locally)
    """
    extractor = Textractor(region_name=_get_region())

    # Use analyze_document for local files (synchronous)
    document = extractor.analyze_document(
        file_source=local_pdf_path,
        features=[TextractFeatures.LAYOUT, TextractFeatures.TABLES],
        save_image=True,
    )

    return document, local_pdf_path, None


def extract_textract_data(s3, s3_file, bucket_name, media_bucket_name):
    """Extract structured text data using Textract."""

    extractor = Textractor(region_name=_get_region())

    file_name, ext = os.path.splitext(os.path.basename(s3_file))
    textract_output_path = f"s3://{media_bucket_name}/textract-output/{file_name}/"

    document = extractor.start_document_analysis(
        file_source=s3_file,
        features=[TextractFeatures.LAYOUT, TextractFeatures.TABLES],
        save_image=False,
        s3_output_path=textract_output_path,
    )

    print("Document analysis started... ")

    # Download pdf from s3
    os.makedirs("/tmp/pdf", exist_ok=True)
    local_pdf_path = f"/tmp/pdf/{os.path.basename(file_name)}.pdf"
    download_from_s3(s3, s3_file, local_pdf_path)

    return document, local_pdf_path, textract_output_path


def download_from_s3(s3, s3_path, local_path):
    s3_bucket, s3_key = s3_path.replace("s3://", "").split("/", 1)
    s3.download_file(s3_bucket, s3_key, local_path)


def download_pdf_from_s3(s3_client, bucket_name: str, s3_key: str) -> str:
    """Download a PDF from S3 to a local temp path and return the local path."""
    os.makedirs("/tmp/pdf", exist_ok=True)
    file_stem = os.path.splitext(os.path.basename(s3_key))[0]
    local_path = f"/tmp/pdf/{file_stem}.pdf"
    s3_client.download_file(bucket_name, s3_key, local_path)
    return local_path


def delete_s3_prefix(s3, bucket_name, prefix):
    """Deletes all objects under a given prefix in an S3 bucket."""
    try:
        objects_to_delete = s3.list_objects_v2(Bucket=bucket_name, Prefix=prefix)
        delete_keys = {"Objects": []}
        if "Contents" in objects_to_delete:
            delete_keys["Objects"] = [{"Key": obj["Key"]} for obj in objects_to_delete["Contents"]]
            if delete_keys["Objects"]:
                s3.delete_objects(Bucket=bucket_name, Delete=delete_keys)
                print(
                    f"Successfully deleted temporary Textract files from s3://{bucket_name}/{prefix}"
                )
    except Exception as e:
        print(f"Error deleting files from s3://{bucket_name}/{prefix}: {e}")
