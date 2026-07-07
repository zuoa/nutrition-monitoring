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
    from app.api.demo import bp as demo_bp
    from app.modules.students.api.organization import bp as org_bp
    from app.modules.students.api.students import bp as students_bp
    from app.modules.students.api.sync import bp as students_sync_bp

    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(dishes_bp, url_prefix="/api/v1/dishes")
    app.register_blueprint(menus_bp, url_prefix="/api/v1/menus")
    app.register_blueprint(analysis_bp, url_prefix="/api/v1/analysis")
    app.register_blueprint(consumption_bp, url_prefix="/api/v1/consumption")
    app.register_blueprint(reports_bp, url_prefix="/api/v1/reports")
    app.register_blueprint(sync_bp, url_prefix="/api/v1/sync")
    app.register_blueprint(admin_bp, url_prefix="/api/v1/admin")
    app.register_blueprint(demo_bp, url_prefix="/api/v1/demo")
    app.register_blueprint(org_bp, url_prefix="/api/v1/org")
    app.register_blueprint(students_bp, url_prefix="/api/v1/students")
    app.register_blueprint(students_sync_bp, url_prefix="/api/v1/students/sync")

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


def seed_default_admin():
    """Seed database with default admin user if missing."""
    from app.models import User, RoleEnum

    # Check if admin already exists
    admin = User.query.filter_by(username="nutri").first()
    if admin:
        click.echo("Admin user already exists.")
        return

    # Create default admin
    admin = User(
        username="nutri",
        name="系统管理员",
        role=RoleEnum.admin,
        dingtalk_user_id="local-admin",
        is_active=True,
    )
    admin.set_password("Nutri#407528")
    db.session.add(admin)
    db.session.commit()
    click.echo("Created default admin user: nutri / Nutri#407528")
    click.echo("WARNING: Please change the default password after first login!")


@click.command("seed-db")
@with_appcontext
def seed_db_command():
    """Seed database with default admin user."""
    seed_default_admin()


@click.command("seed-demo-history")
@click.option("--weeks", default=8, show_default=True, type=click.IntRange(2, 24))
@click.option("--report-weeks", default=4, show_default=True, type=click.IntRange(1, 12))
@click.option("--student-prefix", default="DEMO", show_default=True)
@with_appcontext
def seed_demo_history_command(weeks, report_weeks, student_prefix):
    """Seed demo historical students, nutrition logs, and reports."""
    if report_weeks > weeks:
        raise click.BadParameter("report-weeks 不能大于 weeks", param_hint="report-weeks")

    from app.services.demo_data_service import DemoDataService

    summary = DemoDataService().seed_historical_data(
        weeks=weeks,
        report_weeks=report_weeks,
        student_prefix=student_prefix.strip(),
    )

    click.echo("已生成历史演示数据：")
    click.echo(f"  学生数: {summary['student_count']}")
    click.echo(f"  班级数: {summary['class_count']}")
    click.echo(f"  营养日志: {summary['nutrition_log_count']}")
    click.echo(f"  个人周报: {summary['personal_report_count']}")
    click.echo(f"  班级周报: {summary['class_report_count']}")
    click.echo(f"  历史区间: {summary['history_start']} ~ {summary['history_end']}")
    click.echo(f"  最新周报: {summary['latest_report_start']} ~ {summary['latest_report_end']}")


def init_app(app):
    """Register CLI commands."""
    app.cli.add_command(seed_db_command)
    app.cli.add_command(seed_demo_history_command)
