import routes

from scrimmage import app
from scrimmage.admin.helpers import is_admin

@app.context_processor
def inject_admin():
  return { 'is_admin': is_admin() }
