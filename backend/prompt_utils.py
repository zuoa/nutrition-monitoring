def render_prompt_template(template: str, values: dict[str, object]) -> str:
    """Replace known placeholders without treating other braces as format syntax."""
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"{{{key}}}", str(value))
    return rendered
