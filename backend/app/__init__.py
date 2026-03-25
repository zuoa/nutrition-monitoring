import logging
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_cors import CORS
from flask.cli import with_appcontext
from pythonjsonlogger import jsonlogger
import click
import redis

from config import get_config

db = SQLAlchemy()
migrate = Migrate()
redis_client = None


def create_app(config_class=None):
    app = Flask(__name__)

    if config_class is None:
        config_class = get_config()
    app.config.from_object(config_class)

    # Logging
    _configure_logging(app)

    # Extensions
    db.init_app(app)
    migrate.init_app(app, db)
    CORS(app, origins=app.config.get("CORS_ORIGINS", ["*"]))

    # Initialize Redis
    global redis_client
    redis_client = redis.from_url(app.config["REDIS_URL"], decode_responses=True)

    # Register blueprints
    from app.api.auth import bp as auth_bp
    from app.api.dishes import bp as dishes_bp
    from app.api.menus import bp as menus_bp
    from app.api.analysis import bp as analysis_bp
    from app.api.consumption import bp as consumption_bp
    from app.api.reports import bp as reports_bp
    from app.api.sync import bp as sync_bp
    from app.api.admin import bp as admin_bp

    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(dishes_bp, url_prefix="/api/v1/dishes")
    app.register_blueprint(menus_bp, url_prefix="/api/v1/menus")
    app.register_blueprint(analysis_bp, url_prefix="/api/v1/analysis")
    app.register_blueprint(consumption_bp, url_prefix="/api/v1/consumption")
    app.register_blueprint(reports_bp, url_prefix="/api/v1/reports")
    app.register_blueprint(sync_bp, url_prefix="/api/v1/sync")
    app.register_blueprint(admin_bp, url_prefix="/api/v1/admin")

    # Health check
    @app.route("/health")
    def health():
        return {"status": "ok", "service": "nutrition-monitoring"}

    # Register CLI commands
    init_app(app)

    return app


def _configure_logging(app):
    handler = logging.StreamHandler()
    formatter = jsonlogger.JsonFormatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(app.config.get("LOG_LEVEL", "INFO"))
    root.addHandler(handler)


@click.command("seed-db")
@with_appcontext
def seed_db_command():
    """Seed database with default admin user."""
    from app.models import User, RoleEnum

    # Check if admin already exists
    admin = User.query.filter_by(username="admin").first()
    if admin:
        click.echo("Admin user already exists.")
        return

    # Create default admin
    admin = User(
        username="admin",
        name="系统管理员",
        role=RoleEnum.admin,
        dingtalk_user_id="local-admin",
        is_active=True,
    )
    admin.set_password("admin123")
    db.session.add(admin)
    db.session.commit()
    click.echo("Created default admin user: admin / admin123")
    click.echo("WARNING: Please change the default password after first login!")


def init_app(app):
    """Register CLI commands."""
    app.cli.add_command(seed_db_command)
