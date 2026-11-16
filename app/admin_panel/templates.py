from pathlib import Path
from fastapi.templating import Jinja2Templates

from app.admin_panel.csrf import csrf_field

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["csrf_field"] = csrf_field
