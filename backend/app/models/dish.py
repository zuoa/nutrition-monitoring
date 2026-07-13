import enum
from datetime import datetime, timezone
from app import db
from app.models.dish_image import DishSampleImage
from app.nutrition_metadata import NUTRITION_FIELD_KEYS


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
    cholesterol = db.Column(db.Numeric(8, 2))  # mg
    carbohydrate = db.Column(db.Numeric(8, 2))  # g
    added_sugar = db.Column(db.Numeric(8, 2))  # g
    sodium = db.Column(db.Numeric(8, 2))       # mg
    fiber = db.Column(db.Numeric(8, 2))        # g
    calcium = db.Column(db.Numeric(8, 2))      # mg
    iron = db.Column(db.Numeric(8, 2))         # mg
    zinc = db.Column(db.Numeric(8, 2))         # mg
    vitamin_a = db.Column(db.Numeric(8, 2))    # ug RAE
    vitamin_c = db.Column(db.Numeric(8, 2))    # mg
    vitamin_d = db.Column(db.Numeric(8, 2))    # ug
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
        embedding_counts = {
            "pending": 0,
            "processing": 0,
            "ready": 0,
            "failed": 0,
        }
        for image in active_sample_images:
            status = image.embedding_status.value if image.embedding_status else "pending"
            if status in embedding_counts:
                embedding_counts[status] += 1

        if not active_sample_images:
            sample_embedding_status = "none"
        elif embedding_counts["failed"]:
            sample_embedding_status = "failed"
        elif embedding_counts["processing"]:
            sample_embedding_status = "processing"
        elif embedding_counts["ready"] == len(active_sample_images):
            sample_embedding_status = "ready"
        else:
            sample_embedding_status = "pending"

        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "ingredients": self.ingredients,
            "image_url": self.image_url,
            "price": float(self.price) if self.price is not None else None,
            "category": self.category.value if self.category else None,
            "weight": float(self.weight) if self.weight is not None else 100,
            **{
                field: float(getattr(self, field)) if getattr(self, field) is not None else None
                for field in NUTRITION_FIELD_KEYS
            },
            "is_active": self.is_active,
            "sample_image_count": len(active_sample_images),
            "sample_embedding_status": sample_embedding_status,
            "sample_embedding_ready_count": embedding_counts["ready"],
            "sample_embedding_pending_count": embedding_counts["pending"],
            "sample_embedding_processing_count": embedding_counts["processing"],
            "sample_embedding_failed_count": embedding_counts["failed"],
            "sample_images": [
                img.to_dict(include_internal_path=include_sample_internal_paths)
                for img in active_sample_images
            ],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f"<Dish {self.name}>"
