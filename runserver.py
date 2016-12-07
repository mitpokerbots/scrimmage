import os
from scrimmage import app, db

app.config.from_object('scrimmage.config.DevConfig')

if not os.path.exists('dev.db'):
  print "Regenerating db..."
  db.create_all()

app.run()
