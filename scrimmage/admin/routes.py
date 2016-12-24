from flask import render_template

from scrimmage import app
from scrimmage.admin.helpers import admin_required

@app.route('/admin/')
@admin_required
def admin_index():
  return render_template('admin.html')
