from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Any, TypeVar

from pydantic import BaseModel, Field, model_serializer

T = TypeVar("T", bound=BaseModel)


class Pagination(BaseModel):
    total_items: int
    current_page: int
    total_pages: int
    page_size: int


class PaginatedResponse[T: BaseModel](BaseModel):
    pagination: Pagination
    data: list[T]


class SuccessResponse[T: BaseModel](BaseModel):
    success: bool = True
    data: T | list[T] | None = None
    pagination: Pagination | None = None

    # Using the model_serializer to transform the response
    # Remove the pagination property if it is None from the response
    @model_serializer(mode="wrap")
    def ser_model(self, handler) -> dict[str, Any]:
        result = handler(self)
        if "pagination" in result and result["pagination"] is None:
            del result["pagination"]
        return result


class OutputFormat(StrEnum):
    json = "json"
    jsonl = "jsonl"


class FormatParam(BaseModel):
    format: OutputFormat = Field(OutputFormat.json, description="Output format")


async def generate_response[T: BaseModel](
    output_format: OutputFormat, iterator: AsyncIterator[T]
) -> tuple[AsyncIterator[str], str]:
    if output_format == OutputFormat.jsonl:

        async def jsonl_generator(iterator: AsyncIterator[T]) -> AsyncIterator[str]:
            async for item in iterator:
                yield item.model_dump_json() + "\n"

        generator = jsonl_generator(iterator)
        media_type = "application/x-ndjson"
        return generator, media_type

    # We will always return a JSON response if the format is not jsonl
    async def json_generator(iterator: AsyncIterator[T]) -> AsyncIterator[str]:
        yield '{ "success": true, "data": ['
        items = [item.model_dump_json() async for item in iterator]
        yield ",".join(items)
        yield "]}"

    generator = json_generator(iterator)
    media_type = "application/json"
    return generator, media_type
