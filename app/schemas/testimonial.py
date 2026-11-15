import uuid
from pydantic import BaseModel, ConfigDict


class TestimonialOut(BaseModel):
    __test__ = False  # not a pytest class
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    author_name: str
    author_role: str | None = None
    avatar_url: str | None = None
    quote: str


class TestimonialsOut(BaseModel):
    __test__ = False
    items: list[TestimonialOut]
