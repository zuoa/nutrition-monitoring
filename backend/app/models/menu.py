from datetime import datetime, timezone
from app import db


class DailyMenu(db.Model):
    __tablename__ = "daily_menus"

    id = db.Column(db.Integer, primary_key=True)
    menu_date = db.Column(db.Date, unique=True, nullable=False, index=True)
    dish_ids = db.Column(db.JSON, default=list)
    is_default = db.Column(db.Boolean, default=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "menu_date": self.menu_date.isoformat() if self.menu_date else None,
            "dish_ids": self.dish_ids or [],
            "is_default": self.is_default,
            "created_by": self.created_by,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f"<DailyMenu {self.menu_date}>"
