import enum
from datetime import datetime, timezone
from app import db
from app.models.dish_image import DishSampleImage


class CategoryEnum(str, enum.Enum):
    staple = "主食"
    meat = "荤菜"
    vegetable = "素菜"
    soup = "汤"
    other = "其他"


class Dish(db.Model):
    __tablename__ = "dishes"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False, index=True)
    description = db.Column(db.Text)  # 视觉描述，用于AI图像识别
    ingredients = db.Column(db.Text)  # 配菜描述，用于营养成分分析（选填）
    image_url = db.Column(db.String(255))
    price = db.Column(db.Numeric(8, 2), nullable=False)
    category = db.Column(db.Enum(CategoryEnum), nullable=False)
    # Nutrition per 100g (or per serving weight below)
    weight = db.Column(db.Numeric(8, 2), default=100)  # g, default serving weight
    calories = db.Column(db.Numeric(8, 2))     # kcal
    protein = db.Column(db.Numeric(8, 2))      # g
    fat = db.Column(db.Numeric(8, 2))          # g
    carbohydrate = db.Column(db.Numeric(8, 2))  # g
    sodium = db.Column(db.Numeric(8, 2))       # mg
    fiber = db.Column(db.Numeric(8, 2))        # g
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    sample_images = db.relationship(
        "DishSampleImage",
        back_populates="dish",
        order_by=(DishSampleImage.sort_order.asc(), DishSampleImage.id.asc()),
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def to_dict(self, *, include_sample_internal_paths: bool = False):
        active_sample_images = [img for img in (self.sample_images or []) if img.is_active]
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "ingredients": self.ingredients,
            "image_url": self.image_url,
            "price": float(self.price) if self.price is not None else None,
            "category": self.category.value if self.category else None,
            "weight": float(self.weight) if self.weight is not None else 100,
            "calories": float(self.calories) if self.calories is not None else None,
            "protein": float(self.protein) if self.protein is not None else None,
            "fat": float(self.fat) if self.fat is not None else None,
            "carbohydrate": float(self.carbohydrate) if self.carbohydrate is not None else None,
            "sodium": float(self.sodium) if self.sodium is not None else None,
            "fiber": float(self.fiber) if self.fiber is not None else None,
            "is_active": self.is_active,
            "sample_image_count": len(active_sample_images),
            "sample_images": [
                img.to_dict(include_internal_path=include_sample_internal_paths)
                for img in active_sample_images
            ],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f"<Dish {self.name}>"
