import os
from datetime import date
from decimal import Decimal
from flask import Flask
from dotenv import load_dotenv
from .extensions import db, login_manager
from .models import User, Project, Invoice


def create_app():
    load_dotenv()
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
    db_url = os.getenv("DATABASE_URL", "sqlite:///dev.db")
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)
    login_manager.init_app(app)

    from .routes import bp
    app.register_blueprint(bp)

    @app.cli.command("init-db")
    def init_db_command():
        db.create_all()
        seed_admin_and_demo()
        print("Banco inicializado com sucesso.")

    @app.template_filter("brl")
    def brl(value):
        try:
            value = Decimal(value or 0)
        except Exception:
            value = Decimal("0")
        return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    @app.template_filter("datebr")
    def datebr(value):
        if not value:
            return "-"
        return value.strftime("%d/%m/%Y")

    return app


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def seed_admin_and_demo():
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    if not User.query.filter_by(email=admin_email).first():
        admin = User(
            name=os.getenv("ADMIN_NAME", "Admin"),
            email=admin_email,
            role="admin",
            accepted_terms=True,
            is_active_client=True,
        )
        admin.set_password(os.getenv("ADMIN_PASSWORD", "admin123"))
        db.session.add(admin)

    if not User.query.filter_by(email="cliente@demo.com").first():
        client = User(
            name="João Silva",
            email="cliente@demo.com",
            company="Loja Online",
            whatsapp="+55 11 99999-9999",
            document="00.000.000/0001-00",
            accepted_terms=True,
            is_active_client=True,
        )
        client.set_password("cliente123")
        db.session.add(client)
        db.session.flush()

        project = Project(
            client_id=client.id,
            name="Loja Online",
            railway_internal_name="easygoing-curiosity",
            railway_project_id="rw-demo-001",
            public_url="https://lojaonline.com.br",
            environment="produção",
            status="ativo",
            plan="Growth",
            monthly_value=Decimal("146.00"),
            usage_limit=Decimal("100.00"),
            current_cost=Decimal("82.00"),
            estimated_cost=Decimal("96.00"),
            management_fee=Decimal("50.00"),
            notes="Projeto demo criado automaticamente.",
        )
        db.session.add(project)
        db.session.flush()

        invoice = Invoice(
            number="2026-0031",
            client_id=client.id,
            project_id=project.id,
            period="Março/2026",
            railway_cost=Decimal("96.00"),
            management_fee=Decimal("50.00"),
            discounts=Decimal("0.00"),
            fines=Decimal("0.00"),
            total=Decimal("146.00"),
            status="pendente",
            due_date=date(2026, 4, 10),
        )
        db.session.add(invoice)

    db.session.commit()
