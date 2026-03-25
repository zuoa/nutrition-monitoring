"""Simple CAPTCHA generator using PIL."""
import random
import string
from io import BytesIO
import base64
from PIL import Image, ImageDraw, ImageFont


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    font_candidates = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "DejaVuSans-Bold.ttf",
        "DejaVuSans.ttf",
    ]

    for font_path in font_candidates:
        try:
            return ImageFont.truetype(font_path, size)
        except OSError:
            continue

    return ImageFont.load_default()


def generate_captcha(length: int = 4) -> tuple[str, str]:
    """Generate a CAPTCHA code and its base64 image.

    Returns:
        (code, base64_image_string)
    """
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

    # Create image
    width, height = 168, 56
    image = Image.new('RGB', (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)

    # Add noise (dots)
    for _ in range(140):
        x = random.randint(0, width - 1)
        y = random.randint(0, height - 1)
        draw.point((x, y), fill=(random.randint(100, 200), random.randint(100, 200), random.randint(100, 200)))

    # Add noise (lines)
    for _ in range(6):
        x1 = random.randint(0, width)
        y1 = random.randint(0, height)
        x2 = random.randint(0, width)
        y2 = random.randint(0, height)
        draw.line([(x1, y1), (x2, y2)], fill=(random.randint(150, 220),
                  random.randint(150, 220), random.randint(150, 220)), width=1)

    # Draw text
    font = _load_font(32)

    # Draw each character with slight offset and rotation
    for i, char in enumerate(code):
        x = 16 + i * 34
        y = random.randint(8, 14)
        color = (random.randint(0, 100), random.randint(0, 100), random.randint(0, 100))
        draw.text((x, y), char, font=font, fill=color)

    # Save to base64
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    img_str = base64.b64encode(buffer.getvalue()).decode()

    return code, f"data:image/png;base64,{img_str}"
