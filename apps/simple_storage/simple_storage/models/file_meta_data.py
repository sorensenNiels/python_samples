from datetime import datetime
from typing import ClassVar

from beanie import Document, PydanticObjectId
from bson import ObjectId


class FileMetadata(Document):
    bucket_name: str
    filename: str
    last_modified: datetime
    gridfs_id: PydanticObjectId | ObjectId
    file_hash: str | None = None
    content_type: str
    file_size: int | None = None
    tenant_id: str

    class Config:
        collection = "file_metadata"
        arbitrary_types_allowed = True
        indexes: ClassVar[list[list[tuple[str, int]]]] = [[("tenant_id", 1), ("bucket_name", 1), ("filename", 1)]]
