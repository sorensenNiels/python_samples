import hashlib
from contextlib import asynccontextmanager
from io import BytesIO
from typing import (
    Annotated,
    cast,  # Import 'cast'
)

import jwt
from beanie import init_beanie
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Response, UploadFile
from fastapi.responses import StreamingResponse
from motor.motor_asyncio import AsyncIOMotorDatabase, AsyncIOMotorGridFSBucket
from pymongo import AsyncMongoClient
from simple_storage.constants import DATABASE_NAME
from simple_storage.dtos.bucket_delete_body_dto import BucketDeleteBody
from simple_storage.dtos.file_delete_body_dto import FilesDeleteBody
from simple_storage.dtos.files_download_body_dto import FilesDownloadBody
from simple_storage.dtos.files_list_body_dto import FilesListBody
from simple_storage.dtos.token_request_dto import TokenRequest
from simple_storage.logger import logger
from simple_storage.models.file_meta_data import FileMetadata
from simple_storage.settings import ADMIN_API_KEY, JWT_SECRET, MAX_FILE_SIZE, MONGO_URI
from simple_storage.utils.current_utc_timestamp import current_utc_timestamp
from starlette import status


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Lifecycle started")

    # Initialize the AsyncMongoClient
    client: AsyncMongoClient = AsyncMongoClient(MONGO_URI)

    # Cast the database object to the expected AsyncIOMotorDatabase type
    db: AsyncIOMotorDatabase = cast(AsyncIOMotorDatabase, client[DATABASE_NAME])  # This is the corrected line.

    # Initialize Beanie with the correctly typed database
    await init_beanie(database=db, document_models=[FileMetadata])  # type: ignore

    # Initialize GridFS with the correctly typed database
    gridfs = AsyncIOMotorGridFSBucket(db)

    app.state.gridfs = gridfs

    yield

    # On shutdown, close the client connection
    logger.info("Closing MongoDB connection")
    await client.close()
    logger.info("MongoDB connection closed")


app = FastAPI(lifespan=lifespan)


def get_tenant_id(authorization: str = Header(...)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    token = authorization.split("Bearer ")[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return payload["tenant_id"]
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(status_code=401, detail="Token expired") from e
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail="Invalid token") from e
    except KeyError as e:
        raise HTTPException(status_code=400, detail="tenant_id missing from token") from e


def verify_admin_key(admin_key: str = Header(...)):
    if admin_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid admin API key")


@app.get("/generate_token")
async def generate_token(request: Annotated[TokenRequest, Depends()], _admin_key: Annotated[str, Depends(verify_admin_key)]) -> str:
    tenant_id = request.tenant_id
    now = current_utc_timestamp().timestamp()

    logger.info("Generating token")

    payload = {"tenant_id": tenant_id, "exp": now + 3600, "iat": now}

    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


@app.post("/files/list")
async def list_files(body: FilesListBody, tenant_id: Annotated[str, Depends(get_tenant_id)]):
    bucket_name = body.bucket_name
    logger.info("Listing files in bucket: %s", bucket_name)

    if bucket_name is None:
        return await FileMetadata.find(FileMetadata.tenant_id == tenant_id).to_list()

    return await FileMetadata.find(FileMetadata.tenant_id == tenant_id, FileMetadata.bucket_name == bucket_name).to_list()


@app.post("/files/upload")
async def upload_file(
    tenant_id: Annotated[str, Depends(get_tenant_id)],
    file: Annotated[UploadFile, File(description="The file to upload")],
    bucket_name: Annotated[str, Form()],
    file_name: Annotated[str | None, Form()] = None,
):
    filename = file_name if file_name is not None else file.filename

    if filename is None:
        logger.warning("Upload rejected: Empty filename")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty filename")

    logger.info(f"Attempting to upload file: {filename} in bucket: {bucket_name}, size: {file.size}")

    if file.size is None or file.size == 0:
        logger.warning(f"Upload rejected: Empty file - {filename} in {bucket_name}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file")

    if file.size > MAX_FILE_SIZE:
        logger.warning(f"Upload rejected: File size exceeded - {filename} in {bucket_name}, size: {file.size}")
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File size exceeds the limit of {MAX_FILE_SIZE} bytes",
        )

    existing_file = await FileMetadata.find_one(
        FileMetadata.tenant_id == tenant_id,
        FileMetadata.bucket_name == bucket_name,
        FileMetadata.filename == filename,
    )
    if existing_file:
        logger.warning(f"Upload rejected: File already exists - {filename} in {bucket_name}")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"File '{filename}' already exists in bucket '{bucket_name}'",
        )

    try:
        gridfs: AsyncIOMotorGridFSBucket = app.state.gridfs
        file_hash = hashlib.sha256()
        file_content = b""

        while chunk := await file.read(1024 * 1024):  # Read in 1MB chunks
            file_content += chunk
            file_hash.update(chunk)

        gridfs_id = await gridfs.upload_from_stream(filename=filename, source=BytesIO(file_content))

        await FileMetadata(
            tenant_id=tenant_id,
            bucket_name=bucket_name,
            filename=filename,
            last_modified=current_utc_timestamp(),
            gridfs_id=gridfs_id,
            file_hash=file_hash.hexdigest(),
            content_type=file.content_type or "",
            file_size=file.size,
        ).insert()

        logger.info(
            f"File uploaded successfully: {filename} in bucket: {bucket_name}, size: {file.size}, hash: {file_hash.hexdigest()}"
        )
        return Response(status_code=status.HTTP_201_CREATED, headers={"ETag": file_hash.hexdigest()})

    except Exception as e:
        logger.error(f"File upload failed: {e!s} - {filename} in {bucket_name}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"File upload failed: {e!s}") from e
    finally:
        await file.close()


@app.post("/files/download")
async def download_object(body: FilesDownloadBody, tenant_id: Annotated[str, Depends(get_tenant_id)]):
    bucket_name = body.bucket_name
    filename = body.filename
    if_none_match = body.if_none_match

    logger.info(f"Attempting to download file: {filename} in bucket: {bucket_name}")

    file_metadata = await FileMetadata.find_one(
        FileMetadata.tenant_id == tenant_id, FileMetadata.bucket_name == bucket_name, FileMetadata.filename == filename
    )
    if not file_metadata:
        logger.warning(f"Download failed: File not found - {filename} in {bucket_name}")
        raise HTTPException(status_code=404, detail="File not found")

    if if_none_match and file_metadata.file_hash == if_none_match:
        logger.info(f"Download prevented: File not modified - {filename} in {bucket_name}")
        return Response(status_code=status.HTTP_304_NOT_MODIFIED)

    gridfs: AsyncIOMotorGridFSBucket = app.state.gridfs
    gridfs_file = await gridfs.open_download_stream(file_metadata.gridfs_id)
    if not gridfs_file:
        logger.error(f"Download failed: GridFS file not found - {filename} in {bucket_name}")
        raise HTTPException(status_code=500, detail="GridFS file not found")

    try:

        async def generate_chunks():
            chunk_size = 1024 * 1024  # 1MB chunks
            while chunk := await gridfs_file.read(chunk_size):
                yield chunk

        logger.info(f"File download started: {filename} in bucket: {bucket_name}, size: {file_metadata.file_size}")

        return StreamingResponse(
            generate_chunks(),
            media_type=file_metadata.content_type,
            headers={
                "Content-Disposition": f"attachment; filename={filename or ''}",
                "ETag": file_metadata.file_hash or "",
                "Content-Length": str(gridfs_file.length),
            },
        )

    except Exception as e:
        logger.error(f"Download failed: {e!s} - {filename} in {bucket_name}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Download failed: {e!s}") from e


@app.post("/files/delete")
async def delete_object(body: FilesDeleteBody, tenant_id: Annotated[str, Depends(get_tenant_id)]):
    bucket_name = body.bucket_name
    filename = body.filename

    logger.info("Attempting to delete file: %s in bucket: %s", filename, bucket_name)
    file_metadata = await FileMetadata.find_one(
        FileMetadata.tenant_id == tenant_id, FileMetadata.bucket_name == bucket_name, FileMetadata.filename == filename
    )

    if not file_metadata:
        logger.warning("Delete failed: File not found - %s in %s", filename, bucket_name)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    gridfs = app.state.gridfs
    await gridfs.delete(file_metadata.gridfs_id)
    await file_metadata.delete()

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/buckets/delete")
async def delete_bucket(body: BucketDeleteBody, tenant_id: Annotated[str, Depends(get_tenant_id)]):
    bucket_name = body.bucket_name
    force = body.force

    logger.info("Attempting to delete bucket: %s", bucket_name)

    files = await FileMetadata.find(FileMetadata.tenant_id == tenant_id, FileMetadata.bucket_name == bucket_name).to_list()

    if not force and files:
        logger.warning("Bucket delete rejected: Files exist in bucket - %s", bucket_name)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Bucket not empty")

    # Delete gridfs file instances
    gridfs = app.state.gridfs
    for file in files:
        await gridfs.delete(file.gridfs_id)
        await file.delete()

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/buckets/list")
async def list_buckets(tenant_id: Annotated[str, Depends(get_tenant_id)]):
    logger.info("Listing buckets")
    return await FileMetadata.distinct("bucket_name", {"tenant_id": tenant_id})
