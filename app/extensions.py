from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from authlib.integrations.flask_client import OAuth


db = SQLAlchemy()
login_manager = LoginManager()
oauth = OAuth()
login_manager.login_view = "main.login"
login_manager.login_message = "Entre para acessar o painel."
