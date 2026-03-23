from flask import request


def paginate(query, default_page_size=20):
    """Apply pagination to a SQLAlchemy query and return (items, total, page, page_size)."""
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = min(100, max(1, int(request.args.get("page_size", default_page_size))))
    except (ValueError, TypeError):
        page, page_size = 1, default_page_size

    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return items, total, page, page_size


def paginated_response(items_dict_list, total, page, page_size):
    return {
        "items": items_dict_list,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }
