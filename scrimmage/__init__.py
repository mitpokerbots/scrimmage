from flask import Flask
from flask_sqlalchemy import SQLAlchemy
app = Flask(__name__)
app.config.from_object('scrimmage.config.ProductionConfig')
db = SQLAlchemy(app)

import scrimmage.user
import scrimmage.admin
