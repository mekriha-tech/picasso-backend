from pydantic import BaseModel
import uuid

# What the API expects to receive when someone registers
class UserCreate(BaseModel):
    email: str
    password: str
    full_name: str

# What the API will safely return back (notice we hide the password!)
class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    is_admin: bool

    class Config:
        from_attributes = True

# Add this below your existing UserResponse class
class RegisterResponse(BaseModel):
    user: UserResponse
    access_token: str
    token_type: str = "bearer"


class EmailCheckRequest(BaseModel):
    email: str

class EmailCheckResponse(BaseModel):
    exists: bool