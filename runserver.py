import os
from scrimmage import app, db

if not os.path.exists('dev.db'):
  print "Regenerating db..."
  db.create_all()

app.run()
