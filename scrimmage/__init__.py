from flask import Flask
from flask_sqlalchemy import SQLAlchemy
app = Flask(__name__)
app.config.from_object('scrimmage.config.ProductionConfig')
db = SQLAlchemy(app)

APP_ID = '00000000000000000000000000000000'

import scrimmage.webapp
import scrimmage.models
