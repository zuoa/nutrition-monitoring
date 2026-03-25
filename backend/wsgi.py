from app import create_app, db
from app import seed_default_admin
from celery_app import make_celery
import app.models  # Ensure all SQLAlchemy models are registered before create_all

app = create_app()
celery = make_celery(app)


@app.cli.command("init-db")
def init_db():
    """Initialize database tables."""
    with app.app_context():
        db.create_all()
        print("Database tables created.")


@app.cli.command("bootstrap-db")
def bootstrap_db():
    """Initialize database tables and seed default admin user."""
    with app.app_context():
        db.create_all()
        print("Database tables created.")
        seed_default_admin()


@app.cli.command("seed-dishes")
def seed_dishes():
    """Seed demo dishes for development."""
    from app.models import Dish, CategoryEnum
    from decimal import Decimal

    sample_dishes = [
        {"name": "红烧肉", "price": 8.0, "category": CategoryEnum.meat, "calories": 395, "protein": 13.7, "fat": 37.0, "carbohydrate": 2.6, "sodium": 685, "fiber": 0},
        {"name": "清炒菠菜", "price": 4.0, "category": CategoryEnum.vegetable, "calories": 24, "protein": 2.6, "fat": 0.3, "carbohydrate": 3.1, "sodium": 85, "fiber": 1.7},
        {"name": "白米饭", "price": 2.0, "category": CategoryEnum.staple, "calories": 116, "protein": 2.6, "fat": 0.3, "carbohydrate": 25.6, "sodium": 2, "fiber": 0.3},
        {"name": "紫菜蛋花汤", "price": 3.0, "category": CategoryEnum.soup, "calories": 15, "protein": 1.2, "fat": 0.5, "carbohydrate": 1.8, "sodium": 210, "fiber": 0.2},
        {"name": "宫保鸡丁", "price": 9.0, "category": CategoryEnum.meat, "calories": 172, "protein": 15.2, "fat": 10.8, "carbohydrate": 4.9, "sodium": 680, "fiber": 0.5},
        {"name": "炒西兰花", "price": 5.0, "category": CategoryEnum.vegetable, "calories": 33, "protein": 3.6, "fat": 0.4, "carbohydrate": 4.6, "sodium": 33, "fiber": 1.6},
        {"name": "花卷", "price": 2.0, "category": CategoryEnum.staple, "calories": 238, "protein": 7.2, "fat": 1.0, "carbohydrate": 50.5, "sodium": 280, "fiber": 1.5},
        {"name": "鱼香肉丝", "price": 8.0, "category": CategoryEnum.meat, "calories": 148, "protein": 11.2, "fat": 9.6, "carbohydrate": 4.1, "sodium": 820, "fiber": 0.3},
        {"name": "豆腐汤", "price": 3.0, "category": CategoryEnum.soup, "calories": 30, "protein": 3.2, "fat": 1.5, "carbohydrate": 1.5, "sodium": 190, "fiber": 0.1},
        {"name": "番茄炒蛋", "price": 6.0, "category": CategoryEnum.meat, "calories": 65, "protein": 4.5, "fat": 4.0, "carbohydrate": 3.2, "sodium": 320, "fiber": 0.4},
    ]

    for d in sample_dishes:
        if not Dish.query.filter_by(name=d["name"]).first():
            dish = Dish(**d)
            db.session.add(dish)

    db.session.commit()
    print(f"Seeded {len(sample_dishes)} dishes.")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
